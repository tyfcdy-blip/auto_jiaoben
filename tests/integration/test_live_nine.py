"""显式选择的本机真实平台联调；仅合成工作簿，不读取真实配置或执行外部上传。"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest
from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/nine/synthetic_workbooks.py"
REQUIRED_ENV = ("WB_LIVE_AGENT_SESSION", "NINE_SOURCE_DIRECTORY", "NINE_TEST_PYTHON")
pytestmark = pytest.mark.skipif(
    not all(os.environ.get(key) for key in REQUIRED_ENV),
    reason="必须显式选择本机隔离会话、原源码目录和独立 nine 运行时",
)

spec = importlib.util.spec_from_file_location("nine_live_support", Path(__file__).with_name("test_live_agent.py"))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
checked = support.checked


def invoke(command: list[str], timeout: int = 40) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    assert completed.returncode == 0, "受控本机进程失败；检查测试临时目录，不打印凭据或输入内容"
    return completed


@pytest.fixture(scope="module")
def nine_workspace(tmp_path_factory):
    directory = tmp_path_factory.mktemp("live-nine")
    runtime = str(Path(os.environ["NINE_TEST_PYTHON"]).resolve(strict=True))
    bundle = directory / "nine.zip"
    invoke([sys.executable, str(ROOT / "integrations/nine/package.py"),
            "--source", os.environ["NINE_SOURCE_DIRECTORY"], "--output", str(bundle)])
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "legacy/pdd_billing_check.py", "legacy/pdd_billing_warehouse_load.py", "adapter.py",
            "legacy_launcher.py", "requirements.txt", "parameters.schema.json", "source-manifest.json",
        }
        manifest = json.loads(archive.read("source-manifest.json"))
        assert manifest["operations"] == ["check", "preflight"]
        for name, digest in manifest["sha256"].items():
            assert hashlib.sha256(archive.read("legacy/" + name)).hexdigest() == digest
    inputs = directory / "synthetic-inputs"
    invoke([runtime, "-X", "utf8", str(FIXTURES), "create", "--directory", str(inputs), "--scenario", "normal"])
    input_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs.glob("*.xlsx")}
    assert set(input_hashes) == {"shops.xlsx", "SYNTHETIC_SHOP_A.xlsx", "SYNTHETIC_SHOP_B.xlsx"}
    metadata = json.loads(Path(os.environ["WB_LIVE_AGENT_SESSION"]).read_text(encoding="utf-8"))
    workspace = support.LiveWorkspace(metadata, directory)
    try:
        config = json.loads(workspace.config_path.read_text(encoding="utf-8"))
        config["runtimes"] = {"python-nine": runtime}
        config["capabilities"] = ["nine-billing"]
        workspace.config_path.write_text(json.dumps(config), encoding="utf-8")
        program = checked(workspace.client.post("/api/v1/programs", json={
            "project_id": workspace.project["id"], "name": workspace.name + "-nine-offline"}), 201)
        version = checked(workspace.client.post(f"/api/v1/programs/{program['id']}/versions", json={
            "name": "offline-original-1", "command": ["python-nine", "adapter.py"],
            "side_effects": False, "result_required": True, "required_capabilities": ["nine-billing"],
            "parameter_schema": json.loads((ROOT / "integrations/nine/parameters.schema.json").read_text())}), 201)
        checked(workspace.client.post(f"/api/v1/versions/{version['id']}/bundle", content=bundle.read_bytes(),
                                      headers={"Content-Type": "application/zip"}))
        checked(workspace.client.post(f"/api/v1/versions/{version['id']}/publish"))
        tasks = {}
        for operation in ("check", "preflight"):
            task = checked(workspace.client.post("/api/v1/tasks", json={
                "project_id": workspace.project["id"], "name": workspace.name + "-" + operation,
                "version_id": version["id"], "node_id": workspace.node_id, "timeout_seconds": 60,
                "requires_input": True, "operation": operation}), 201)
            workspace.tasks.append(task["id"])
            tasks[operation] = task["id"]
        caller = checked(workspace.client.post("/api/v1/callers", json={
            "project_id": workspace.project["id"], "name": workspace.name + "-synthetic-upstream",
            "task_ids": list(tasks.values()), "allow_input_upload": True}), 201)
        workspace.callers.append(caller["id"])
        headers = {"Authorization": "Bearer " + caller["token"]}
        batch = checked(workspace.client.post("/api/v1/batches", headers=headers, json={
            "project_id": workspace.project["id"], "label": "SYNTHETIC-2026-07"}), 201)
        for name, digest in input_hashes.items():
            uploaded = checked(workspace.client.put(f"/api/v1/batches/{batch['id']}/files/{name}",
                                                    headers=headers, content=(inputs / name).read_bytes()))
            assert uploaded["sha256"] == digest
        checked(workspace.client.post(f"/api/v1/batches/{batch['id']}/ready", headers=headers))
        yield {"workspace": workspace, "runtime": runtime, "tasks": tasks, "headers": headers,
               "batch_id": batch["id"], "inputs": inputs, "input_hashes": input_hashes}
    finally:
        workspace.clean_controls()


@pytest.mark.parametrize("operation", ["check", "preflight"])
def test_live_nine_original_on_platform(nine_workspace, operation):
    fixture = nine_workspace
    workspace = fixture["workspace"]
    with workspace.engine.connect() as connection:
        loops_before = dict(connection.execute(text("SELECT name, last_success FROM loop_status")).all())
    assert set(loops_before) == {"scheduler", "reconciler", "notifier", "storage_cleanup"}
    route = f"/api/v1/tasks/{fixture['tasks'][operation]}/runs"
    request = {"event_id": workspace.name + "-" + operation, "input_batch_id": fixture["batch_id"],
               "parameters": {"period": "2026-07", "mapping_file": "shops.xlsx",
                              "bill_files": ["SYNTHETIC_SHOP_A.xlsx", "SYNTHETIC_SHOP_B.xlsx"]}}
    admitted = checked(workspace.client.post(route, headers=fixture["headers"], json=request), 202)
    duplicate = checked(workspace.client.post(route, headers=fixture["headers"], json=request))
    assert duplicate["run_id"] == admitted["run_id"] and duplicate["deduplicated"] is True
    invoke(workspace.agent_command(), timeout=75)
    run = workspace.detail(admitted["run_id"])
    assert run["state"] == "succeeded" and run["exit_code"] == 0
    assert run["result"]["status"] == "success" and run["artifact_state"] == "complete"
    assert run["result"]["metrics"]["network_allowed"] is False
    assert run["result"]["metrics"]["input_files"] == 2
    downloaded_directory = workspace.directory / (operation + "-downloaded")
    downloaded_directory.mkdir()
    downloaded = {}
    for artifact in run["artifacts"]:
        name = artifact["name"]
        assert Path(name).name == name
        response = workspace.client.get(f"/api/v1/runs/{run['id']}/artifacts/{artifact['id']}")
        assert response.status_code == 200
        path = downloaded_directory / name
        path.write_bytes(response.content)
        downloaded[name] = path
    if operation == "check":
        assert len(downloaded) == 2 and all(path.suffix == ".xlsx" for path in downloaded.values())
        for path in downloaded.values():
            inspected = invoke([fixture["runtime"], "-X", "utf8", str(FIXTURES), "inspect", "--report", str(path)])
            values = json.loads(inspected.stdout)
            assert values["merchant_receivable"] == 130
            assert values["promotion"] == -8.5 and values["review_gift"] == -2
            assert values["cashflow_total"] == ["总计", 130, -6]
    else:
        manifest = json.loads(downloaded[run["id"] + "_manifest.json"].read_text(encoding="utf-8"))
        assert manifest["execute"] is False and manifest["input_file_count"] == 2
        assert run["result"]["metrics"]["total_rows"] == 22
        assert "未上传" in run["result"]["summary"]
    contexts = [json.loads(path.read_text(encoding="utf-8"))
                for path in (workspace.directory / "state").glob("*/context.json")]
    context = next(context for context in contexts if context["run_id"] == run["id"])
    assert context["operation"] == operation
    for entry in context["input_manifest"]:
        assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == fixture["input_hashes"][entry["name"]]
    offline_work = Path(context["output_dir"]) / ".nine-work"
    assert not (offline_work / "unused-requests").exists()
    assert not (offline_work / "unused-id-state.json").exists()
    for name, digest in fixture["input_hashes"].items():
        assert hashlib.sha256((fixture["inputs"] / name).read_bytes()).hexdigest() == digest
    channels = checked(workspace.client.get("/api/v1/channels", params={"project_id": workspace.project["id"]}))["items"]
    assert channels == []
    logs = checked(workspace.client.get(f"/api/v1/runs/{run['id']}/logs"))["entries"]
    assert any("nine operation=" + operation in entry["text"] for entry in logs)
    # 验证联调环境的真实后台循环仍在推进；这不冒充自然定时触发测试。
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with workspace.engine.connect() as connection:
            loops_after = dict(connection.execute(text("SELECT name, last_success FROM loop_status")).all())
        if all(loops_after.get(name, 0) > stamp for name, stamp in loops_before.items()):
            break
        time.sleep(0.1)
    else:
        pytest.fail("联调后台循环在真实任务执行期间未推进")
