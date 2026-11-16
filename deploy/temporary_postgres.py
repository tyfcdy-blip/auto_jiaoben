"""使用官方EDB便携PG17建立隔离本地测试实例；不注册全局服务。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import socket
import subprocess
import tempfile
from urllib.parse import urlsplit
import zipfile

import httpx
import psycopg


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runtime" / "postgres17"
# EDB Windows initdb在非ASCII安装路径可能混用系统代码页，因此二进制及数据放专用TEMP目录。
BASE = Path(tempfile.gettempdir()) / ("codex-workbench-pg17-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8])
SOURCE_PAGE = "https://www.enterprisedb.com/download-postgresql-binaries"
SOURCE_URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run(args: list[str], detached: bool = False, **kwargs: object) -> subprocess.CompletedProcess:
    options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if detached else {"capture_output": True}
    return subprocess.run(args, check=True, timeout=120, creationflags=HIDDEN, **options, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-initialized", action="store_true",
                        help="核对本工具已初始化的隔离目录并完成配置记录，不重置密码")
    arguments = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("本辅助工具仅用于Windows隔离测试；其他系统使用已验证的PG17部署")
    BASE.mkdir(parents=True, exist_ok=True)
    if (BASE / "connection.local.json").exists():
        raise SystemExit("已有测试实例配置；拒绝自动覆盖，请核对原实例")
    if os.name == "nt":
        identity = run(["whoami"]).stdout.decode().strip()
        run(["icacls", str(BASE), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F",
             "SYSTEM:(OI)(CI)F"])
    else:
        BASE.chmod(0o700)
    CACHE.mkdir(parents=True, exist_ok=True)
    archive_path = CACHE / "postgresql-17.11-3-windows-x64-binaries.zip"
    if not archive_path.exists():
        partial = archive_path.with_suffix(".partial")
        with httpx.stream("GET", SOURCE_URL, follow_redirects=True, timeout=120) as response:
            response.raise_for_status()
            if urlsplit(str(response.url)).hostname != "get.enterprisedb.com":
                raise RuntimeError("下载被重定向到未批准的主机")
            expected = int(response.headers.get("content-length", "0"))
            downloaded = 0
            next_report = 32 * 1024 * 1024
            with partial.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_report:
                        print(f"官方归档已下载 {downloaded // (1024 * 1024)} MiB", flush=True)
                        next_report += 32 * 1024 * 1024
            if expected and downloaded != expected:
                raise RuntimeError("下载长度不一致")
        partial.replace(archive_path)
    with archive_path.open("rb") as archive_stream:
        checksum = hashlib.file_digest(archive_stream, "sha256").hexdigest()
    if not arguments.resume_initialized:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                parts = PurePosixPath(member.filename).parts
                if len(parts) < 2 or parts[0] != "pgsql" or parts[1] not in {"bin", "lib", "share"}:
                    continue
                target = (BASE / member.filename).resolve()
                if not target.is_relative_to(BASE.resolve()) or ".." in parts:
                    raise RuntimeError("不安全的归档路径")
                archive.extract(member, BASE)
    binary = BASE / "pgsql" / "bin"
    version = run([str(binary / "postgres.exe"), "--version"]).stdout.decode().strip()
    if "17.11" not in version:
        raise RuntimeError("解压程序版本不匹配")
    password_file = BASE / "password.local"
    data = BASE / "data"
    if arguments.resume_initialized:
        if (data / "PG_VERSION").read_text().strip() != "17":
            raise RuntimeError("已有目录不是预期PG17隔离实例")
        password = password_file.read_text().strip()
        settings = (data / "postgresql.conf").read_text(encoding="utf-8")
        port = int(re.findall(r"^port\s*=\s*(\d+)\s*$", settings, flags=re.MULTILINE)[-1])
        if "listen_addresses = '127.0.0.1'" not in settings:
            raise RuntimeError("现有实例不是仅本地监听配置")
    else:
        if data.exists():
            raise RuntimeError("已有data目录，拒绝隐式重建；请核对后选择恢复初始化记录")
        password = secrets.token_hex(32)
        password_file.write_text(password + "\n", encoding="utf-8")
        run([str(binary / "initdb.exe"), "-D", str(data), "-U", "wb_test", "-E", "UTF8",
             "--locale=C", "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
             f"--pwfile={password_file}"])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (data / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write(f"\nlisten_addresses = '127.0.0.1'\nport = {port}\nmax_connections = 30\n")
    logfile = BASE / "server.log"
    status = subprocess.run([str(binary / "pg_ctl.exe"), "-D", str(data), "status"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=HIDDEN, timeout=10)
    if status.returncode != 0:
        run([str(binary / "pg_ctl.exe"), "-D", str(data), "-l", str(logfile), "-w", "start"], detached=True)
    dsn = f"postgresql://wb_test:{password}@127.0.0.1:{port}/postgres"
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("workbench_test",)).fetchone():
            connection.execute("CREATE DATABASE workbench_test")
    application_url = f"postgresql+psycopg://wb_test:{password}@127.0.0.1:{port}/workbench_test"
    connection_path = BASE / "connection.local.json"
    connection_path.write_text(json.dumps({
        "database_url": application_url, "port": port, "user": "wb_test",
        "database": "workbench_test", "data_directory": str(data),
        "pg_ctl": str(binary / "pg_ctl.exe"), "postgres_version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # 工作区仅保存不含密码的指针，连接秘密保留在受限TEMP目录。
    (CACHE / "instance.local.json").write_text(json.dumps({
        "connection_file": str(connection_path), "postgres_version": version,
        "data_directory": str(data), "pg_ctl": str(binary / "pg_ctl.exe"), "port": port,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (BASE / "provenance.json").write_text(json.dumps({
        "community_page": "https://www.postgresql.org/download/windows/",
        "vendor_page": SOURCE_PAGE, "download": SOURCE_URL, "version": version,
        "archive_sha256": checksum, "checksum_origin": "本地计算；非厂商签名校验值",
        "extracted_directories": ["pgsql/bin", "pgsql/lib", "pgsql/share"],
        "service_registered": False, "listen_address": "127.0.0.1",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"version": version, "port": port, "connection_file": str(connection_path),
                      "password_printed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
