import argparse
import getpass
import json
import os
from pathlib import Path
from sqlalchemy import select
from .config import Settings
from .db import database, initialize, lock
from .models import User
from .security import hasher


def main():
    parser = argparse.ArgumentParser(description="工作台本地管理命令")
    parser.add_argument("command", choices=["init-db", "init-admin", "export-openapi"])
    args = parser.parse_args()
    settings = Settings()
    if args.command == "export-openapi":
        from .main import create_app
        target = Path("contracts/openapi.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
        print("OpenAPI 已导出")
        return
    engine, factory = database(settings)
    try:
        if args.command == "init-db":
            initialize(engine, factory)
            print("数据库结构版本 1 已就绪；未创建默认账号")
            return
        username = os.environ.get("WB_ADMIN_USERNAME") or input("管理员账号：")
        filename = os.environ.get("WB_ADMIN_PASSWORD_FILE")
        password = Path(filename).read_text(encoding="utf-8").strip() if filename else getpass.getpass("管理员密码：")
        if not username or len(username) > 80 or len(password) < 12 or len(password) > 256:
            raise ValueError("管理员账号或密码长度不符合要求")
        with factory.begin() as db:
            lock(db)
            if db.scalar(select(User.id).where(User.is_admin.is_(True))):
                raise ValueError("管理员已存在，不能重复初始化或重置")
            db.add(User(username=username, display_name=os.environ.get("WB_ADMIN_DISPLAY_NAME", "管理员"),
                        password_hash=hasher.hash(password), is_admin=True))
        print("管理员已创建；密码未写入日志")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
