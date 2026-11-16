"""仅本机的合成联调服务，不连接生产数据库、不使用真实业务凭据。"""
import json
import argparse
from pathlib import Path
import secrets
import socket
import sys
import uuid
import threading
import uvicorn
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-path', default='')
    parser.add_argument('--frontend-directory', type=Path)
    parser.add_argument('--session-file', type=Path, default=ROOT / 'runtime/integration/session.local.json')
    parser.add_argument('--database-url-file', type=Path,
                        help='仅供隔离联调容器使用；省略时读取本机临时 PostgreSQL 指针')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "backend"))
    from workbench.config import Settings
    from workbench.db import initialize
    from workbench.main import create_app
    from workbench.models import User
    from workbench.security import hasher
    from workbench.worker import tick
    if args.database_url_file:
        database_url = args.database_url_file.read_text(encoding="utf-8").strip()
    else:
        pointer = json.loads((ROOT / "runtime/postgres17/instance.local.json").read_text(encoding="utf-8"))
        database_url = json.loads(Path(pointer["connection_file"]).read_text(encoding="utf-8"))["database_url"]
    schema = "integration_" + uuid.uuid4().hex
    maintenance = create_engine(database_url)
    with maintenance.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(database_url).update_query_dict({"options": "-csearch_path="+schema}).render_as_string(hide_password=False)
    settings = Settings(env="test", database_url=scoped_url, data_dir=ROOT / "runtime/integration/storage",
                        secret_key=Fernet.generate_key().decode(), monitor_token=secrets.token_urlsafe(32), base_path=args.base_path)
    app = create_app(settings, frontend_directory=args.frontend_directory)
    initialize(app.state.engine, app.state.factory)
    password = secrets.token_urlsafe(24)
    with app.state.factory.begin() as db:
        db.add(User(username="integration-admin", display_name="联调管理员", password_hash=hasher.hash(password), is_admin=True))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config_file = args.session_file
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps({"url": f"http://127.0.0.1:{port}"+args.base_path, "username": "integration-admin", "password": password,
                                      "schema": schema, "database_url": scoped_url}, indent=2), encoding="utf-8")
    stopping = threading.Event()
    def background():
        while not stopping.wait(1):
            tick(app.state.factory, settings)
    thread = threading.Thread(target=background, daemon=True)
    thread.start()
    print(f"本机合成联调服务 http://127.0.0.1:{port}{args.base_path}；登录材料位于本地会话文件", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    try:
        server.run(sockets=[listener])
    finally:
        stopping.set()
        thread.join(timeout=5)
        app.state.engine.dispose()
        maintenance.dispose()
        listener.close()


if __name__ == "__main__":
    main()
