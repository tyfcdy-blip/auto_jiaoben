"""在本机隔离工作台真实上传并运行Excel多页签合并程序。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from agent.connection import import_connection  # noqa: E402


def checked(response: httpx.Response, expected: int = 200) -> dict:
    if response.status_code != expected:
        raise AssertionError(f"隔离工作台接口未通过：{response.status_code}，预期{expected}")
    return response.json()


def hashes(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*.xlsx")}


def run(session_file: Path, output: Path, excel_python: Path) -> None:
    metadata = json.loads(session_file.read_text(encoding="utf-8"))
    if urlsplit(metadata["url"]).hostname != "127.0.0.1" or not metadata.get("schema", "").startswith("integration_"):
        raise ValueError("只允许本机独立integration schema")
    output = output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "runtime"):
        raise ValueError("输出必须是runtime下的全新目录")
    output.mkdir(parents=True)
    inputs = output / "inputs"
    helper = ROOT / "tests/excel_sheet_merger/workbook_fixture.py"
    subprocess.run([str(excel_python), str(helper), "create", str(inputs), "basic"], check=True, cwd=ROOT)
    before = hashes(inputs)
    bundle = ROOT / "examples/excel-sheet-merger/dist/excel-sheet-merger.zip"
    definition = json.loads((ROOT / "examples/excel-sheet-merger/program.json").read_text(encoding="utf-8"))
    stamp = uuid.uuid4().hex[:8]
    evidence = {"url": metadata["url"], "project_name": "Excel合并验收-" + stamp, "checks": []}
    with httpx.Client(base_url=metadata["url"].rstrip("/") + "/api/v1/", timeout=20,
                      follow_redirects=False, trust_env=False) as client:
        login = checked(client.post("auth/login", json={"username": metadata["username"],
                                                        "password": metadata["password"]}))
        client.headers["Authorization"] = "Bearer " + login["token"]
        project = checked(client.post("projects", json={"name": evidence["project_name"]}), 201)
        node = checked(client.post("nodes", json={"project_id": project["id"], "name": "Excel合并隔离节点",
                                                   "capabilities": ["python", "directory-input-v1"], "slots": 1,
                                                   "input_roots": [str(inputs)]}), 201)
        connection = output / "connection.local.json"
        connection.write_text(json.dumps({"format": "workbench-connection-v1", "node_id": node["id"],
            "name": node["name"], "base_url": metadata["url"], "token": node["token"],
            "runtimes": {"python": sys.executable}, "capabilities": ["python"],
            "input_roots": [str(inputs)]}), encoding="utf-8")
        try:
            agent_config = import_connection(connection, output / "connection")
        finally:
            connection.unlink(missing_ok=True)
        program = checked(client.post("programs", json={"project_id": project["id"], "name": definition["name"],
                                                         "description": definition["description"]}), 201)
        version = checked(client.post(f"programs/{program['id']}/versions", json={
            "name": "acceptance-v1", "command": definition["command"],
            "parameter_schema": definition["parameter_schema"], "side_effects": False,
            "result_required": True, "required_capabilities": definition["required_capabilities"]}), 201)
        checked(client.post(f"versions/{version['id']}/bundle", content=bundle.read_bytes(),
                            headers={"Content-Type": "application/zip"}))
        checked(client.post(f"versions/{version['id']}/publish"))
        parameters = {"input_dir": str(inputs), "batch_key": "acceptance-" + stamp,
                      **definition["example_parameters"]}
        task = checked(client.post("tasks", json={"project_id": project["id"], "name": "Excel单页签合并测试",
            "version_id": version["id"], "node_id": node["id"], "default_parameters": parameters,
            "timeout_seconds": definition["suggested_timeout_seconds"]}), 201)
        assert task["input_mode"] == "directory" and task["business_key_fields"] == ["batch_key"]
        evidence["checks"].append("程序ZIP上传、版本发布及目录任务创建")
        caller = checked(client.post("callers", json={"project_id": project["id"], "name": "Excel合并合成触发",
                                                       "task_ids": [task["id"]]}), 201)
        caller_headers = {"Authorization": "Bearer " + caller["token"]}
        response = checked(client.post(f"tasks/{task['id']}/runs",
            headers=caller_headers,
            json={"event_id": "excel-merge-" + stamp, "parameters": parameters}), 202)
        process_environment = os.environ | {
            "USERPROFILE": str(output / "synthetic-profile"),
            "HOME": str(output / "synthetic-profile"),
        }
        completed = subprocess.run([sys.executable, "-m", "agent", "--config", str(agent_config), "--once"],
                                   cwd=ROOT, capture_output=True, timeout=90, env=process_environment)
        assert completed.returncode == 0
        detail = checked(client.get(f"runs/{response['run_id']}", headers=caller_headers))
        assert detail["state"] == "succeeded" and detail["result"]["metrics"]["sheets"] == 2
        evidence["checks"].append("事件受理、真实执行器领取及自包含依赖运行")
        artifact = detail["artifacts"][0]
        downloaded = client.get(f"runs/{detail['id']}/artifacts/{artifact['id']}", headers=caller_headers)
        downloaded.raise_for_status()
        result_path = output / "downloaded-result.xlsx"
        result_path.write_bytes(downloaded.content)
        inspected = subprocess.run([str(excel_python), str(helper), "inspect", str(result_path)], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", timeout=30, check=True,
                                   env=os.environ | {"PYTHONUTF8": "1"})
        workbook = json.loads(inspected.stdout)
        assert workbook["sheetnames"] == ["report", "report (2)"]
        assert workbook["values"][4][1] == "='report'!B4"
        assert workbook["a1_bold"] and workbook["validations"] == 1 and workbook["conditional_formats"] == 2
        assert '"prefix"&\'report\'!A2="prefixNORTH-A"' in workbook["conditional_formulas"]
        evidence["checks"].append("鉴权下载多页签产物并回读内容、公式及格式")
        assert hashes(inputs) == before
        evidence["checks"].append("运行前后所有源Excel摘要保持不变")
        checked(client.patch(f"callers/{caller['id']}", json={"active": False}))
        evidence.update(project_id=project["id"], task_id=task["id"], run_id=detail["id"],
                        input_dir=str(inputs), artifact=str(result_path), passed=len(evidence["checks"]), failed=0)
    (output / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Excel合并工作台验收通过：{len(evidence['checks'])}项，证据位于{output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--excel-python", type=Path, required=True)
    options = parser.parse_args()
    run(options.session_file, options.output, options.excel_python)
