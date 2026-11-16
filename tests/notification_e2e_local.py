"""用隔离 SQLite 和本机浏览器核对通知配置页面，不触达现有服务或真实钉钉。"""

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from workbench.config import Settings  # noqa: E402
from workbench.db import initialize  # noqa: E402
from workbench.main import create_app  # noqa: E402
from workbench.models import User  # noqa: E402
from workbench.notifications import send_test_message  # noqa: E402
from workbench import routes_operations  # noqa: E402
from workbench.security import hasher  # noqa: E402


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main() -> int:
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    location = tempfile.TemporaryDirectory(prefix="notification-e2e-", dir=runtime)
    directory = Path(location.name).resolve()
    if not directory.is_relative_to(ROOT.resolve()):
        raise RuntimeError("隔离目录不在项目工作区内")
    server = None
    thread = None
    probe_client = None
    original_probe = routes_operations.send_test_message
    try:
        password = secrets.token_urlsafe(22)
        settings = Settings(env="test", database_url="sqlite:///" + str(directory / "test.db"),
                            data_dir=directory / "storage", secret_key=Fernet.generate_key().decode())
        application = create_app(settings)
        initialize(application.state.engine, application.state.factory)
        with application.state.factory.begin() as db:
            db.add(User(username="notification-e2e", display_name="通知隔离测试",
                        password_hash=hasher.hash(password), is_admin=True))
        probe_client = httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"errcode": 0})))
        routes_operations.send_test_message = (
            lambda encrypted, current_settings, test_id:
            send_test_message(encrypted, current_settings, test_id, client=probe_client))
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        session_file = directory / "session.json"
        session_file.write_text(json.dumps({"url": url, "username": "notification-e2e",
                                            "password": password}), encoding="utf-8")
        server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=port,
                                              log_level="error", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(50):
            try:
                if httpx.get(url + "/api/v1/health/ready", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError("隔离测试服务未就绪")
        environment = os.environ.copy()
        environment["WORKBENCH_E2E_SESSION"] = str(session_file)
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            raise RuntimeError("找不到 npm，无法运行浏览器测试")
        result = subprocess.run([npm, "run", "test:e2e:notifications"],
                                cwd=ROOT / "frontend", env=environment, check=False, timeout=120)
        return result.returncode
    finally:
        routes_operations.send_test_message = original_probe
        if probe_client:
            probe_client.close()
        if server and thread:
            server.should_exit = True
            thread.join(timeout=10)
        if directory.is_relative_to(ROOT.resolve()):
            location.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
