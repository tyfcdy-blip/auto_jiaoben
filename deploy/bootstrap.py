"""生成单个部署实例的秘密文件，不打印秘密，不覆盖已有凭据。"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = (
    "database_password", "database_url", "encryption_key", "monitor_token", "admin_password"
)


def _validate_database_settings(host: str, port: int, name: str, user: str, password: str) -> None:
    if (not host or len(host) > 253 or host[0] in ".-" or host[-1] in ".-"
            or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                   for char in host)):
        raise ValueError("数据库主机名格式无效")
    if not 1 <= port <= 65535:
        raise ValueError("数据库端口超出范围")
    identifier_characters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    for label, value in (("数据库名", name), ("数据库用户名", user)):
        if (not value or len(value) > 63
                or value[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
                or any(char not in identifier_characters for char in value)):
            raise ValueError(f"{label}格式无效")
    if not password or len(password) > 512 or any(char in password for char in "\x00\r\n"):
        raise ValueError("数据库密码格式无效")


def _read_password_file(path: Path) -> str:
    password = path.read_text(encoding="utf-8")
    if password.endswith("\r\n"):
        password = password[:-2]
    elif password.endswith("\n"):
        password = password[:-1]
    if any(char in password for char in "\r\n"):
        raise ValueError("数据库密码文件必须只有一行")
    return password


def initialize(
    secret_dir: Path,
    *,
    database_host: str = "db",
    database_port: int = 5432,
    database_name: str = "workbench",
    database_user: str = "workbench",
    database_password: str | None = None,
) -> list[str]:
    password = secrets.token_hex(32) if database_password is None else database_password
    _validate_database_settings(
        database_host, database_port, database_name, database_user, password
    )
    secret_dir = secret_dir.resolve()
    secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = [name for name in SECRET_NAMES if (secret_dir / name).exists()]
    if existing:
        raise ValueError("秘密目录已含凭据；拒绝覆盖或自动轮换，请先核对部署实例")
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami"], check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        # 限制父目录继承权限；秘密不打印到命令参数或终端。
        subprocess.run(
            ["icacls", str(secret_dir), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F",
             "SYSTEM:(OI)(CI)F"],
            check=True, capture_output=True, timeout=10,
        )
    else:
        secret_dir.chmod(0o700)
    encoded_user = quote(database_user, safe="")
    encoded_password = quote(password, safe="")
    values = {
        "database_password": password,
        "database_url": (
            f"postgresql+psycopg://{encoded_user}:{encoded_password}"
            f"@{database_host}:{database_port}/{database_name}"
        ),
        "encryption_key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        "monitor_token": secrets.token_urlsafe(48),
        "admin_password": secrets.token_urlsafe(32),
    }
    for name, value in values.items():
        target = secret_dir / name
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(value + "\n")
        # 父目录0700保护宿主读取；只读文件绑定进被授权服务后允许非root读取。
        if os.name != "nt":
            target.chmod(0o444)
    return list(values)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "secrets")
    parser.add_argument("--external-database", action="store_true")
    parser.add_argument("--database-host")
    parser.add_argument("--database-port", type=int, default=5432)
    parser.add_argument("--database-name", default="workbench")
    parser.add_argument("--database-user", default="workbench")
    parser.add_argument("--database-password-file", type=Path)
    arguments = parser.parse_args()
    try:
        options = {}
        if arguments.external_database:
            if arguments.database_host is None or arguments.database_password_file is None:
                raise ValueError("外部数据库模式必须提供主机名和密码文件")
            password = _read_password_file(arguments.database_password_file)
            options = {
                "database_host": arguments.database_host,
                "database_port": arguments.database_port,
                "database_name": arguments.database_name,
                "database_user": arguments.database_user,
                "database_password": password,
            }
        elif arguments.database_host is not None or arguments.database_password_file is not None:
            raise ValueError("数据库连接参数只能与--external-database一起使用")
        names = initialize(arguments.directory, **options)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"初始化未完成：{type(exc).__name__}；请核对目录和权限，未自动删除任何文件。\n")
    print(json.dumps({"created": names, "directory": str(arguments.directory.resolve()),
                      "secrets_displayed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
