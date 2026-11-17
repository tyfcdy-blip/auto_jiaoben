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


def _read_json_member(archive: tarfile.TarFile, name: str,
                      expected_digest: str | None = None) -> object:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValueError(f"Docker归档缺少{name}") from exc
    if not member.isfile() or member.size > 1024 * 1024:
        raise ValueError(f"Docker归档{name}无效")
    stream = archive.extractfile(member)
    if stream is None:
        raise ValueError(f"Docker归档{name}不可读")
    content = stream.read()
    if expected_digest and hashlib.sha256(content).hexdigest() != expected_digest:
        raise ValueError(f"Docker归档{name}摘要不一致")
    return json.loads(content)


def _digest_path(value: object, field: str) -> tuple[str, str]:
    match = re.fullmatch(r"sha256:([a-f0-9]{64})", str(value))
    if not match:
        raise ValueError(f"Docker归档{field}摘要无效")
    return f"blobs/sha256/{match.group(1)}", match.group(1)


def docker_archive_identity(image_archive: Path) -> dict[str, str]:
    """校验 OCI index→linux/amd64 manifest→config 链路。

    经典 Docker Engine 的 Image ID 是 config digest，不是 OCI index digest。
    """
    with tarfile.open(image_archive, "r") as archive:
        docker_manifest = _read_json_member(archive, "manifest.json")
        if not isinstance(docker_manifest, list) or len(docker_manifest) != 1:
            raise ValueError("Docker归档必须只包含一个镜像")
        config_name = (docker_manifest[0].get("Config")
                       if isinstance(docker_manifest[0], dict) else None)
        match = re.fullmatch(r"blobs/sha256/([a-f0-9]{64})", str(config_name))
        if not match:
            raise ValueError("Docker归档配置摘要路径无效")
        _read_json_member(archive, str(config_name), match.group(1))

        root_index = _read_json_member(archive, "index.json")
        root_descriptors = root_index.get("manifests") if isinstance(root_index, dict) else None
        if not isinstance(root_descriptors, list) or len(root_descriptors) != 1:
            raise ValueError("Docker归档index.json必须唯一指向镜像index")
        root_descriptor = root_descriptors[0]
        if (not isinstance(root_descriptor, dict)
                or root_descriptor.get("mediaType") != "application/vnd.oci.image.index.v1+json"):
            raise ValueError("Docker归档镜像index类型无效")
        index_path, index_hash = _digest_path(root_descriptor.get("digest"), "index")
        image_index = _read_json_member(archive, index_path, index_hash)
        image_descriptors = (image_index.get("manifests")
                             if isinstance(image_index, dict) else None)
        platform_descriptors = [
            descriptor for descriptor in image_descriptors or []
            if isinstance(descriptor, dict)
            and descriptor.get("mediaType") == "application/vnd.oci.image.manifest.v1+json"
            and descriptor.get("platform") == {"architecture": "amd64", "os": "linux"}
        ]
        if len(platform_descriptors) != 1:
            raise ValueError("Docker归档必须唯一包含linux/amd64 manifest")
        manifest_path, manifest_hash = _digest_path(
            platform_descriptors[0].get("digest"), "manifest",
        )
        image_manifest = _read_json_member(archive, manifest_path, manifest_hash)
        if (not isinstance(image_manifest, dict)
                or image_manifest.get("mediaType")
                != "application/vnd.oci.image.manifest.v1+json"):
            raise ValueError("Docker归档平台manifest类型无效")
        manifest_config = image_manifest.get("config")
        if (not isinstance(manifest_config, dict)
                or manifest_config.get("mediaType")
                != "application/vnd.oci.image.config.v1+json"
                or manifest_config.get("digest") != f"sha256:{match.group(1)}"):
            raise ValueError("Docker归档manifest与config摘要不一致")
        return {
            "image_index_digest": f"sha256:{index_hash}",
            "image_manifest_digest": f"sha256:{manifest_hash}",
            "image_config_digest": f"sha256:{match.group(1)}",
        }


def docker_archive_config_digest(image_archive: Path) -> str:
    """为现有发布工具保留的精简入口。"""
    return docker_archive_identity(image_archive)["image_config_digest"]


def build(image_archive: Path, manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {"release", "git_commit", "image", "image_id", "image_index_digest",
                "image_manifest_digest", "image_config_digest", "platform", "archive_sha256"}
    archive_identity = docker_archive_identity(image_archive)
    if (not isinstance(manifest, dict) or not required.issubset(manifest)
            or manifest["platform"] != "linux/amd64"
            or digest(image_archive) != manifest["archive_sha256"]
            or any(manifest[field] != value for field, value in archive_identity.items())
            or manifest["image_id"] != manifest["image_config_digest"]):
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
        common_path = scripts / "common.sh"
        common = common_path.read_text(encoding="utf-8")
        if common.count("REPLACE_WITH_IMAGE_CONFIG_DIGEST") != 1:
            raise ValueError("服务器脚本缺少唯一的镜像config摘要占位")
        common_path.write_text(
            common.replace("REPLACE_WITH_IMAGE_CONFIG_DIGEST",
                           manifest["image_config_digest"]),
            encoding="utf-8", newline="\n",
        )
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
        (staging / "SHA256SUMS").write_text(
            "\n".join(sums) + "\n", encoding="utf-8", newline="\n",
        )
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
