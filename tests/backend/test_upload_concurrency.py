"""真实PostgreSQL行锁等待不能阻塞上传服务的事件循环。"""

from concurrent.futures import ThreadPoolExecutor
import io
import threading
import time
import zipfile

import pytest
from sqlalchemy import text

from workbench.db import lock


def prepare_upload(kind, client, admin, project, registered):
    if kind == "bundle":
        response = client.post(
            f'/api/v1/programs/{registered["program"]["id"]}/versions', headers=admin,
            json={"name": "upload-lock-regression", "command": ["python", "main.py"],
                  "parameter_schema": {"type": "object", "additionalProperties": False}},
        )
        assert response.status_code == 201
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("main.py", "print('isolated upload regression')")
        return "POST", f'/api/v1/versions/{response.json()["id"]}/bundle', admin, archive.getvalue()
    if kind == "input":
        response = client.post("/api/v1/batches", headers=admin,
                               json={"project_id": project["id"], "label": "上传行锁合成批次"})
        assert response.status_code == 201
        return "PUT", f'/api/v1/batches/{response.json()["id"]}/files/input.csv', admin, b"count\n1\n"
    response = client.post("/api/v1/nodes", headers=admin,
                           json={"name": "上传回归测试节点", "project_id": project["id"]})
    assert response.status_code == 201
    node = {"Authorization": "Bearer " + response.json()["token"]}
    assert client.post("/api/v1/agent/heartbeat", headers=node, json={
        "capabilities": [], "isolation": "development", "agent_version": "0.1.0",
    }).status_code == 200
    response = client.post(f'/api/v1/tasks/{registered["task"]["id"]}/runs', headers=admin,
                           json={"event_id": "artifact-upload-lock-test", "parameters": {}})
    assert response.status_code == 202
    lease = client.post("/api/v1/agent/claim", headers=node, json={})
    assert lease.status_code == 200
    claimed = lease.json()
    return ("POST", f'/api/v1/agent/runs/{claimed["run_id"]}/artifacts/report.txt'
            f'?attempt_id={claimed["attempt_id"]}', node, b"isolated artifact\n")


def wait_for_database_lock(engine, holder_pid, timeout=5):
    deadline = time.monotonic() + timeout
    # 直接观察真实PG阻塞关系，不能仅sleep后猜测上传请求已经进入行锁等待。
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            blocked = connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE datname = current_database() AND :holder = ANY(pg_blocking_pids(pid)))"
            ), {"holder": holder_pid})
            if blocked:
                return
            time.sleep(0.02)
    raise AssertionError("上传请求未进入预期的真实PostgreSQL行锁等待")


@pytest.mark.parametrize("kind", ["bundle", "input", "artifact"])
def test_gate_waiting_upload_keeps_health_responsive(app, client, admin, project, registered, kind):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("需通过WORKBENCH_TEST_DATABASE_FILE指定独立PostgreSQL；SQLite无法验证行锁等待")
    method, path, headers, payload = prepare_upload(kind, client, admin, project, registered)
    holding = threading.Event()
    release = threading.Event()
    holder_pids = []

    def hold_gate():
        with app.state.factory.begin() as session:
            lock(session)
            holder_pids.append(session.scalar(text("SELECT pg_backend_pid()")))
            holding.set()
            if not release.wait(timeout=15):
                raise AssertionError("测试未在期限内释放Gate")

    with ThreadPoolExecutor(max_workers=3) as executor:
        holder = executor.submit(hold_gate)
        try:
            assert holding.wait(timeout=5), "未取得测试Gate"
            upload = executor.submit(client.request, method, path, headers=headers, content=payload)
            wait_for_database_lock(app.state.engine, holder_pids[0])
            assert not upload.done(), "Gate释放前上传不应完成"
            # TestClient上下文共用同一ASGI事件循环，旧实现会让此请求也等到Gate释放。
            health = executor.submit(client.get, "/api/v1/health/live")
            assert health.result(timeout=2).status_code == 200
            assert not upload.done(), "健康响应不是通过提前释放Gate获得"
        finally:
            release.set()
            holder.result(timeout=5)
        response = upload.result(timeout=5)
        assert response.status_code == 200
        checksum_field = "bundle_sha256" if kind == "bundle" else "sha256"
        assert len(response.json()[checksum_field]) == 64
