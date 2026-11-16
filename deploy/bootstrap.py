"""生成单个部署实例的秘密文件，不打印秘密，不覆盖已有凭据。"""

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SECRET_NAMES = (
    "database_password", "database_url", "encryption_key", "monitor_token", "admin_password"
)


def initialize(secret_dir: Path) -> list[str]:
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
    password = secrets.token_hex(32)
    values = {
        "database_password": password,
        "database_url": f"postgresql+psycopg://workbench:{password}@db:5432/workbench",
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
    arguments = parser.parse_args()
    try:
        names = initialize(arguments.directory)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"初始化未完成：{type(exc).__name__}；请核对目录和权限，未自动删除任何文件。\n")
    print(json.dumps({"created": names, "directory": str(arguments.directory.resolve()),
                      "secrets_displayed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
