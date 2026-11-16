"""针对显式Compose项目执行备份/恢复；不删除卷，不自动启用恢复后的服务。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "deploy" / "compose.yaml"

# 只接受当前已知结构，恢复出的旧派发开关必须落库关闭，再恢复平台文件。
DISABLE_RESTORED_DISPATCH_SQL = """
DO $restore_guard$
DECLARE
    changed_rows integer;
BEGIN
    IF (SELECT count(*) FROM public.system_gate) <> 1 THEN
        RAISE EXCEPTION 'restore requires exactly one system gate';
    END IF;
    UPDATE public.system_gate SET dispatch_enabled = false
        WHERE id = 1 AND schema_version = 1;
    GET DIAGNOSTICS changed_rows = ROW_COUNT;
    IF changed_rows <> 1 THEN
        RAISE EXCEPTION 'unsupported restored system gate schema or identity';
    END IF;
END;
$restore_guard$;
"""


def command(project: str, env_file: Path | None = None) -> list[str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,62}", project):
        raise ValueError("项目名必须是3至63位小写字母、数字、下划线或连字符")
    args = ["docker", "compose", "--project-name", project, "--file", str(COMPOSE)]
    if env_file is not None:
        args += ["--env-file", str(env_file.resolve())]
    return args


def execute(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, check=True, timeout=600, **kwargs)


def require_stopped(prefix: list[str]) -> None:
    for service in ("api", "worker", "init-admin", "init-db"):
        state = execute(prefix + ["ps", "--status", "running", "--quiet", service],
                        capture_output=True, text=True)
        if state.stdout.strip():
            raise ValueError(f"{service}仍在运行；先停止入口、派发和写入，再备份或恢复")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def backup(prefix: list[str], project: str, directory: Path, note: str) -> None:
    require_stopped(prefix)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    # API和后台停机只是平台一致性边界；节点上的业务状态需要另行核对和备份。
    with (directory / "database.dump").open("xb") as stream:
        execute(prefix + ["exec", "-T", "db", "pg_dump", "-U", "workbench", "-d", "workbench", "-Fc"],
                stdout=stream)
    with (directory / "platform-data.tar").open("xb") as stream:
        execute(prefix + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "api",
                          "-C", "/app/data", "-cf", "-", "."], stdout=stream)
    names = ("database.dump", "platform-data.tar")
    manifest = {
        "format": 1, "project": project,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: digest(directory / name) for name in names},
        "operator_note": note,
        "scope": "平台数据库和共享数据；不含执行器本地状态或解密秘密",
        "restore_requires_reconciliation": True,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                              encoding="utf-8")


def check_archive(path: Path) -> None:
    with tarfile.open(path, "r:") as archive:
        for entry in archive:
            normalized = entry.name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError("归档包含不安全路径")
            if not (entry.isfile() or entry.isdir()):
                raise ValueError("归档含链接或特殊设备，禁止恢复")


def restore(prefix: list[str], project: str, directory: Path, confirm_project: str) -> None:
    if confirm_project != project:
        raise ValueError("恢复需用 --confirm-project 再次明确目标实例")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("restore_requires_reconciliation") is not True:
        raise ValueError("不支持的备份格式")
    for name in ("database.dump", "platform-data.tar"):
        if digest(directory / name) != manifest.get("files", {}).get(name):
            raise ValueError("备份校验失败")
    check_archive(directory / "platform-data.tar")
    require_stopped(prefix)
    # 仅还原至空数据卷，拒绝覆盖并混入旧文件；数据库恢复指令本身仍是破坏性操作。
    execute(prefix + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python", "api", "-c",
                      "from pathlib import Path; raise SystemExit(1 if any(Path('/app/data').iterdir()) else 0)"],
            capture_output=True)
    with (directory / "database.dump").open("rb") as stream:
        execute(prefix + ["exec", "-T", "db", "pg_restore", "-U", "workbench", "-d", "workbench",
                          "--clean", "--if-exists", "--exit-on-error", "--single-transaction"], stdin=stream)
    execute(prefix + ["exec", "-T", "db", "psql", "-X", "-U", "workbench", "-d", "workbench",
                      "--set", "ON_ERROR_STOP=1", "--single-transaction", "--command",
                      DISABLE_RESTORED_DISPATCH_SQL], capture_output=True)
    with (directory / "platform-data.tar").open("rb") as stream:
        execute(prefix + ["run", "--rm", "--no-deps", "-T", "--entrypoint", "tar", "api",
                          "-C", "/app/data", "--no-same-owner", "-xf", "-"], stdin=stream)
    print("数据库派发开关已关闭，恢复文件已写入；API与worker保持停止。"
          "须使用WB_DISPATCH_ENABLED=false核对后另行批准启用。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--confirm-project", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--database-mode", choices=("bundled", "external"), default="bundled")
    args = parser.parse_args()
    try:
        if args.database_mode == "external":
            raise ValueError("外部数据库由服务器管理员备份恢复；本工具仅支持自带数据库")
        prefix = command(args.project, args.env_file)
        directory = args.directory.resolve()
        if args.action == "backup":
            if not args.note.strip():
                raise ValueError("备份前须用--note记录停止派发及在途节点核对情况")
            backup(prefix, args.project, directory, args.note)
        else:
            restore(prefix, args.project, directory, args.confirm_project)
    except (ValueError, OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
        # 不把数据库连接、完整子进程命令或秘密路径内容写进错误摘要。
        print(f"操作未完成：{type(exc).__name__}；保持服务停止并核对备份及恢复步骤。", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps({"operation": args.action, "project": args.project, "directory": str(directory)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
