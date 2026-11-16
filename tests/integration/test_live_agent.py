"""显式选择的本机 PostgreSQL 联调；只访问新建合成项目，禁止真实通知或上传。"""

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid
import zipfile

import httpx
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
SESSION_PATH = os.environ.get("WB_LIVE_AGENT_SESSION")
pytestmark = pytest.mark.skipif(not SESSION_PATH, reason="真实服务联调必须显式提供本机隔离会话文件")


def checked(response: httpx.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        try:
            code = response.json().get("code", "unclassified")
        except ValueError:
            code = "non-json"
        pytest.fail(f"本机联调接口返回异常：status={response.status_code}, code={code}")
    return response.json()


class LiveWorkspace:
    def __init__(self, metadata: dict, directory: Path):
        origin = urlsplit(metadata["url"])
        if origin.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("真实联调只接受本机服务")
        schema = metadata["schema"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) or schema in {"public", "information_schema", "pg_catalog"}:
            raise ValueError("必须使用独立测试 schema")
        self.schema = schema
        self.base_url = metadata["url"].rstrip("/")
        self.directory = directory
        self.client = httpx.Client(base_url=self.base_url, timeout=15, follow_redirects=False)
        login = checked(self.client.post("/api/v1/auth/login", json={"username": metadata["username"], "password": metadata["password"]}))
        self.client.headers["Authorization"] = "Bearer " + login["token"]
        self.name = "agent-live-" + uuid.uuid4().hex[:12]
        self.project = checked(self.client.post("/api/v1/projects", json={"name": self.name}), 201)
        node = checked(self.client.post("/api/v1/nodes", json={"project_id": self.project["id"],
                       "name": self.name + "-node", "capabilities": ["python"], "slots": 1}), 201)
        self.node_id = node["id"]
        token_file = directory / "node-token.txt"
        token_file.write_text(node["token"], encoding="utf-8")
        self.config_path = directory / "agent.json"
        self.config_path.write_text(json.dumps({"base_url": self.base_url, "token_file": str(token_file),
            "state_dir": str(directory / "state"), "runtimes": {"python": sys.executable},
            "capabilities": ["python"], "poll_seconds": 0.1, "heartbeat_seconds": 0.2}), encoding="utf-8")
        self.engine = create_engine(metadata["database_url"], pool_pre_ping=True)
        with self.engine.connect() as connection:
            actual = connection.scalar(text("SELECT current_schema()"))
        if actual != schema:
            self.engine.dispose()
            raise ValueError("测试数据库连接与会话 schema 不一致，禁止注入时钟")
        self.tasks = []
        self.schedules = []
        self.callers = []
        self.process_environment = os.environ.copy()
        self.process_environment["USERPROFILE"] = str(directory / "synthetic-profile")
        self.process_environment["HOME"] = str(directory / "synthetic-profile")

    def register(self, suffix: str, source: str, timeout: int = 15) -> dict:
        program = checked(self.client.post("/api/v1/programs", json={"project_id": self.project["id"], "name": self.name + suffix}), 201)
        version = checked(self.client.post(f"/api/v1/programs/{program['id']}/versions", json={
            "name": "test-1", "command": ["python", "main.py"], "side_effects": False, "result_required": True,
            "required_capabilities": ["python"], "parameter_schema": {"type": "object", "additionalProperties": False,
             "properties": {"greeting": {"type": "string"}}}}), 201)
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr("main.py", source)
        checked(self.client.post(f"/api/v1/versions/{version['id']}/bundle", content=content.getvalue(), headers={"Content-Type": "application/zip"}))
        checked(self.client.post(f"/api/v1/versions/{version['id']}/publish"))
        task = checked(self.client.post("/api/v1/tasks", json={"project_id": self.project["id"], "name": self.name + suffix,
                        "version_id": version["id"], "node_id": self.node_id, "timeout_seconds": timeout}), 201)
        self.tasks.append(task["id"])
        return task

    def agent_command(self) -> list[str]:
        return [sys.executable, "-m", "agent", "--config", str(self.config_path), "--once"]

    def run_agent(self) -> None:
        process = subprocess.run(self.agent_command(), cwd=ROOT, capture_output=True, timeout=35,
                                 env=self.process_environment)
        assert process.returncode == 0, "执行器 CLI 未正常退出；检查受控本地状态和日志"

    def detail(self, run_id: str) -> dict:
        return checked(self.client.get(f"/api/v1/runs/{run_id}"))

    def clean_controls(self) -> None:
        # 只禁用当前测试创建的触发入口，保留记录供主代理复核，不操作别的项目。
        for schedule_id in self.schedules:
            checked(self.client.patch(f"/api/v1/schedules/{schedule_id}", json={"enabled": False}))
        for task_id in self.tasks:
            checked(self.client.patch(f"/api/v1/tasks/{task_id}", json={"enabled": False}))
        for caller_id in self.callers:
            checked(self.client.patch(f"/api/v1/callers/{caller_id}", json={"active": False}))
        checked(self.client.patch(f"/api/v1/nodes/{self.node_id}", json={"enabled": False, "maintenance": True}))
        self.engine.dispose()
        self.client.close()


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    metadata = json.loads(Path(SESSION_PATH).read_text(encoding="utf-8"))
    fixture = LiveWorkspace(metadata, tmp_path_factory.mktemp("live-agent"))
    try:
        yield fixture
    finally:
        fixture.clean_controls()


@pytest.fixture(scope="module")
def successful_task(workspace):
    return workspace.register("-success", (ROOT / "examples" / "safe-job" / "main.py").read_text(encoding="utf-8"))


def test_live_event_duplicate_real_process_artifact(workspace, successful_task):
    caller = checked(workspace.client.post("/api/v1/callers", json={"project_id": workspace.project["id"],
                     "name": workspace.name + "-caller", "task_ids": [successful_task["id"]]}), 201)
    workspace.callers.append(caller["id"])
    headers = {"Authorization": "Bearer " + caller["token"]}
    request = {"event_id": workspace.name + "-download-done", "parameters": {"greeting": "真实事件参数"}}
    route = f"/api/v1/tasks/{successful_task['id']}/runs"
    admitted = checked(workspace.client.post(route, headers=headers, json=request), 202)
    duplicate = checked(workspace.client.post(route, headers=headers, json=request))
    assert duplicate["run_id"] == admitted["run_id"] and duplicate["deduplicated"] is True
    workspace.run_agent()
    run = workspace.detail(admitted["run_id"])
    caller_run = checked(workspace.client.get(f"/api/v1/runs/{admitted['run_id']}", headers=headers))
    assert run["state"] == "succeeded" and run["exit_code"] == 0
    assert caller_run["result"] == run["result"]
    assert caller_run["artifacts"] == run["artifacts"]
    assert run["result"]["status"] == "success" and run["artifact_state"] == "complete"
    artifact = next(item for item in run["artifacts"] if item["name"] == "summary.txt")
    downloaded = workspace.client.get(f"/api/v1/runs/{run['id']}/artifacts/{artifact['id']}", headers=headers)
    assert downloaded.status_code == 200 and downloaded.text == "真实事件参数"
    logs = checked(workspace.client.get(f"/api/v1/runs/{run['id']}/logs"))
    assert any("输入文件数量" in entry["text"] for entry in logs["entries"])
    workspace.run_agent()
    assert workspace.detail(run["id"])["attempt_id"] == run["attempt_id"]


def test_live_schedule_same_task_with_explicit_test_clock(workspace, successful_task):
    schedule = checked(workspace.client.post("/api/v1/schedules", json={"task_id": successful_task["id"],
        "name": workspace.name + "-schedule", "cron": "0 0 * * *", "timezone": "Asia/Shanghai",
        "parameters": {"greeting": "${scheduled_date}"}, "misfire_policy": "once", "enabled": False}), 201)
    workspace.schedules.append(schedule["id"])
    checked(workspace.client.patch(f"/api/v1/schedules/{schedule['id']}", json={"enabled": True}))
    due = time.time() - 2
    # 明确的测试时钟注入，只修改本测试项目此计划，未宣称自然时间等待触发。
    with workspace.engine.begin() as connection:
        result = connection.execute(text("UPDATE schedules SET next_fire = :due WHERE id = :schedule AND project_id = :project"),
                                    {"due": due, "schedule": schedule["id"], "project": workspace.project["id"]})
        assert result.rowcount == 1
    deadline = time.monotonic() + 20
    scheduled_runs = []
    while time.monotonic() < deadline:
        records = checked(workspace.client.get("/api/v1/runs", params={"project_id": workspace.project["id"]}))["items"]
        scheduled_runs = [run for run in records if run["task_id"] == successful_task["id"] and run["source"] == "schedule"]
        if scheduled_runs:
            break
        time.sleep(0.2)
    assert len(scheduled_runs) == 1, "等待真实 worker 调度测试计划超时或重复触发"
    checked(workspace.client.patch(f"/api/v1/schedules/{schedule['id']}", json={"enabled": False}))
    workspace.run_agent()
    run = workspace.detail(scheduled_runs[0]["id"])
    assert run["state"] == "succeeded"
    from zoneinfo import ZoneInfo
    expected_date = datetime.fromtimestamp(due, timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    assert run["parameters"]["greeting"] == expected_date


def test_live_failure_records_exit_log_and_alert_without_channel(workspace):
    task = workspace.register("-failure", "import sys\nprint('合成业务失败', file=sys.stderr, flush=True)\nsys.exit(9)\n")
    admitted = checked(workspace.client.post(f"/api/v1/tasks/{task['id']}/runs", json={"event_id": workspace.name + "-failure"}), 202)
    workspace.run_agent()
    run = workspace.detail(admitted["run_id"])
    assert run["state"] == "failed" and run["exit_code"] == 9
    logs = checked(workspace.client.get(f"/api/v1/runs/{run['id']}/logs"))
    assert any("合成业务失败" in entry["text"] for entry in logs["entries"])
    alerts = checked(workspace.client.get("/api/v1/alerts", params={"project_id": workspace.project["id"]}))["items"]
    assert any(alert["run_id"] == run["id"] for alert in alerts)
    channels = checked(workspace.client.get("/api/v1/channels", params={"project_id": workspace.project["id"]}))["items"]
    assert channels == []  # 未建立通知通道，不会向真实钉钉发送消息。


def test_live_cancel_running_process(workspace):
    task = workspace.register("-cancel", "import time\nprint('合成长任务启动', flush=True)\ntime.sleep(25)\n", timeout=30)
    admitted = checked(workspace.client.post(f"/api/v1/tasks/{task['id']}/runs", json={"event_id": workspace.name + "-cancel"}), 202)
    process = subprocess.Popen(workspace.agent_command(), cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=workspace.process_environment)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if workspace.detail(admitted["run_id"])["state"] == "running":
                break
            if process.poll() is not None:
                pytest.fail("执行器在运行状态出现前退出")
            time.sleep(0.1)
        else:
            pytest.fail("合成长任务未进入 running")
        checked(workspace.client.post(f"/api/v1/runs/{admitted['run_id']}/cancel"))
        process.communicate(timeout=10)
        assert process.returncode == 0
        run = workspace.detail(admitted["run_id"])
        assert run["state"] == "canceled" and run["observation"] == "canceled"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
