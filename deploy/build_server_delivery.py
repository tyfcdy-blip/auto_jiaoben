"""组装FinalShell离线交付包；不生成或包含任何生产秘密。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SERVER_FILES = (
    "common.sh", "preflight.sh", "install.sh", "verify.sh", "enable-dispatch.sh",
    "rollback-first-install.sh", "patch_nginx.py", "nginx-location.conf", "README-FIRST.txt",
)
PAYLOAD_FILES = (
    "compose.yaml", "compose.external-db.yaml", "compose.shared-nginx.yaml",
)


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def build(image_archive: Path, manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {"release", "git_commit", "image", "image_id", "platform", "archive_sha256"}
    if (not isinstance(manifest, dict) or not required.issubset(manifest)
            or manifest["platform"] != "linux/amd64"
            or digest(image_archive) != manifest["archive_sha256"]):
        raise ValueError("镜像发布清单不完整或归档摘要不一致")
    version = str(manifest["release"]).removeprefix("v")
    if version != "0.6.0-rc.4" or manifest["git_commit"] != (
            "825e310fd567023542140471e31a147bd0f45b1d"):
        raise ValueError("服务器包只允许当前已验证的rc.4基线")
    root_name = f"automation-workbench-server-v{version}"
    delivery_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True,
        text=True, timeout=10,
    ).stdout.strip()
    if not re.fullmatch(r"[a-f0-9]{40}", delivery_commit):
        raise ValueError("无法确认部署脚本Git提交")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or Path(str(output) + ".sha256").exists():
        raise ValueError("输出文件已存在，拒绝覆盖")
    with tempfile.TemporaryDirectory(prefix="workbench-delivery-") as temporary_name:
        staging = Path(temporary_name) / root_name
        scripts = staging / "scripts"
        payload = staging / "payload"
        scripts.mkdir(parents=True)
        payload.mkdir()
        for name in SERVER_FILES:
            source = ROOT / "deploy" / "server" / name
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"交付文件缺失：{name}")
            shutil.copy2(source, scripts / name)
        for name in PAYLOAD_FILES:
            shutil.copy2(ROOT / "deploy" / name, payload / name)
        environment = (ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8")
        environment = environment.replace("REPLACE_WITH_FULL_GIT_COMMIT", manifest["git_commit"])
        if "REPLACE_WITH" in environment:
            raise ValueError("生产环境模板仍有未填写占位")
        (payload / ".env.production").write_text(environment, encoding="utf-8", newline="\n")
        shutil.copy2(image_archive, payload / f"automation-workbench_{version}_linux-amd64.tar")
        (payload / "release-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (payload / "delivery-manifest.json").write_text(json.dumps({
            "application_release": manifest["release"],
            "application_commit": manifest["git_commit"],
            "delivery_commit": delivery_commit,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sums = []
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                sums.append(f"{digest(path)}  {path.relative_to(staging).as_posix()}")
        (staging / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        with tarfile.open(output, "w:gz", compresslevel=6) as archive:
            for path in [staging, *sorted(staging.rglob("*"))]:
                info = archive.gettarinfo(str(path), arcname=str(path.relative_to(staging.parent)))
                if path.is_file():
                    info.mode = 0o755 if path.suffix in {".sh", ".py"} else 0o644
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    info.mode = 0o755
                    archive.addfile(info)
    package_hash = digest(output)
    Path(str(output) + ".sha256").write_text(
        f"{package_hash}  {output.name}\n", encoding="utf-8", newline="\n",
    )
    return {"package": str(output), "size": output.stat().st_size, "sha256": package_hash,
            "release": manifest["release"], "git_commit": manifest["git_commit"],
            "delivery_commit": delivery_commit}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build(args.image_archive.resolve(), args.manifest.resolve(), args.output.resolve())
    except (OSError, ValueError, json.JSONDecodeError, tarfile.TarError,
            subprocess.SubprocessError) as exc:
        parser.exit(1, f"交付包生成失败：{type(exc).__name__}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
