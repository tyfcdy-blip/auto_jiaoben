"""真实网络响应必须晚于真实 PostgreSQL 事务提交；仅使用独立测试 schema。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import socket
import threading
import time

import httpx
import pytest
from sqlalchemy import event, select
import uvicorn

from workbench.models import Run, TriggerEvent


@contextmanager
def live_server(app):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    # app/client fixture 已负责初始化及生命周期；本测试只增加真实网络传输。
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", access_log=False, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "本机 uvicorn 未就绪"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        listener.close()
        assert not thread.is_alive(), "本机测试服务未停止"


@contextmanager
def intercept_run_commit(app, task_id, before_commit):
    def mark_run_transaction(session, _flush_context):
        for record in session.new:
            if isinstance(record, Run) and record.task_id == task_id:
                session.info["commit_visibility_run_id"] = record.id

    def controlled_commit(session):
        run_id = session.info.pop("commit_visibility_run_id", None)
        if run_id:
            before_commit(run_id)

    event.listen(app.state.factory, "after_flush", mark_run_transaction)
    event.listen(app.state.factory, "before_commit", controlled_commit)
    try:
        yield
    finally:
        event.remove(app.state.factory, "before_commit", controlled_commit)
        event.remove(app.state.factory, "after_flush", mark_run_transaction)


def require_postgres(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("需显式提供 WORKBENCH_TEST_DATABASE_FILE，SQLite 不能代替 PostgreSQL 提交时序")
    with app.state.engine.connect() as connection:
        from sqlalchemy import text
        schema = connection.scalar(text("SELECT current_schema()"))
    assert schema.startswith("test_"), "只允许独立测试 fixture schema"


def test_admission_response_headers_wait_for_commit_then_immediate_get_sees_run(app, admin, registered):
    require_postgres(app)
    commit_entered = threading.Event()
    allow_commit = threading.Event()
    headers_received = threading.Event()
    observed_ids = []

    def block_commit(run_id):
        observed_ids.append(run_id)
        commit_entered.set()
        if not allow_commit.wait(timeout=10):
            raise AssertionError("提交故障注入未及时释放")

    with live_server(app) as base_url:
        def post_via_real_network():
            with httpx.Client(base_url=base_url, timeout=12) as network:
                with network.stream("POST", f"/api/v1/tasks/{registered['task']['id']}/runs", headers=admin,
                                    json={"event_id": "commit-visibility-network", "parameters": {}}) as response:
                    headers_received.set()
                    response.read()
                    return response.status_code, response.json()

        with intercept_run_commit(app, registered["task"]["id"], block_commit):
            with ThreadPoolExecutor(max_workers=1) as executor:
                request = executor.submit(post_via_real_network)
                try:
                    assert commit_entered.wait(timeout=5), "请求未到达真实 Session.before_commit"
                    # 独立连接证明新行尚不可见，不以模拟数据库替代事务隔离。
                    with app.state.factory() as observer:
                        assert observer.get(Run, observed_ids[0]) is None
                    with httpx.Client(base_url=base_url, timeout=2) as network:
                        assert network.get("/api/v1/health/live").status_code == 200
                    assert not headers_received.wait(timeout=0.3), "事务提交前已经发送受理响应头"
                    assert not request.done(), "事务提交前不应完成 POST 受理响应"
                finally:
                    allow_commit.set()
                status, payload = request.result(timeout=5)
                assert status == 202
                assert payload["run_id"] == observed_ids[0]
                # POST返回后立即GET，不sleep、不重试掩盖响应先于提交的问题。
                with httpx.Client(base_url=base_url, timeout=3) as network:
                    visible = network.get(f"/api/v1/runs/{payload['run_id']}", headers=admin)
                assert visible.status_code == 200
                assert visible.json()["id"] == payload["run_id"]


def test_commit_failure_never_returns_accepted_or_consumes_event(app, admin, registered):
    require_postgres(app)
    fault_entered = threading.Event()
    observed_ids = []

    def fail_commit(run_id):
        observed_ids.append(run_id)
        fault_entered.set()
        raise RuntimeError("controlled_test_commit_failure")

    path = f"/api/v1/tasks/{registered['task']['id']}/runs"
    body = {"event_id": "commit-failure-network", "parameters": {}}
    with live_server(app) as base_url:
        with intercept_run_commit(app, registered["task"]["id"], fail_commit):
            with httpx.Client(base_url=base_url, timeout=5) as network:
                response = network.post(path, headers=admin, json=body)
            assert fault_entered.is_set(), "未触发真实提交边界故障"
            assert response.status_code == 500, "提交失败不能仍返回 202 受理成功"
            assert response.json()["code"] == "internal_error"
        with app.state.factory() as observer:
            assert observer.get(Run, observed_ids[0]) is None
            assert observer.scalar(select(TriggerEvent).where(TriggerEvent.event_id == body["event_id"])) is None
        # 注入撤销后相同事件可重新受理，验证之前没有残留去重记录。
        with httpx.Client(base_url=base_url, timeout=5) as network:
            retried = network.post(path, headers=admin, json=body)
            assert retried.status_code == 202
            visible = network.get(f"/api/v1/runs/{retried.json()['run_id']}", headers=admin)
            assert visible.status_code == 200
