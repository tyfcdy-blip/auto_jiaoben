"""真实运行独立目录示例；仅使用 pytest 临时目录和合成文件。"""

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

import pytest


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "folder-jobs"


def invoke(command: list[str], timeout: float = 8) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          env=os.environ | {"PYTHONUTF8": "1"}, check=False)


def context_file(tmp_path: Path, contents: dict[str, str] | None = None, **parameters) -> tuple[Path, dict]:
    inputs = tmp_path / "input"
    output = tmp_path / "output"
    state = tmp_path / "state"
    inputs.mkdir()
    output.mkdir()
    state.mkdir()
    manifest = []
    for name, content in (contents or {"a.csv": "id,value\nA,1\n", "nested/b.csv": "id,value\nB,2\n"}).items():
        path = inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        manifest.append({"name": name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    context = {"protocol_version": "1", "run_id": "synthetic-run", "attempt_id": "synthetic-attempt",
               "operation": "run", "parameters": {"input_dir": str(inputs), "batch_key": "synthetic-batch",
                                                     **parameters},
               "input_manifest": manifest, "output_dir": str(output), "state_dir": str(state),
               "cancel_file": str(state / "cancel")}
    target = tmp_path / "context.json"
    target.write_text(json.dumps(context), encoding="utf-8")
    return target, context


def command(job: str, context: Path) -> list[str]:
    if job == "node-summary":
        node = shutil.which("node")
        assert node, "本机缺少 Node.js，目录统计子进程测试不可执行"
        return [node, str(EXAMPLES / job / "main.js"), "--context-file", str(context)]
    return [sys.executable, str(EXAMPLES / job / "main.py"), "--context-file", str(context)]


def report(context: dict) -> dict:
    result = json.loads((Path(context["output_dir"]) / "result.json").read_text(encoding="utf-8"))
    assert result["protocol_version"] == "1"
    assert result["run_id"] == context["run_id"]
    assert result["attempt_id"] == context["attempt_id"]
    assert not list(Path(context["output_dir"]).glob("*.tmp"))
    return result


def test_csv_merge_real_process_and_read_only_input(tmp_path):
    path, context = context_file(tmp_path)
    before = {entry["path"]: Path(entry["path"]).read_bytes() for entry in context["input_manifest"]}
    completed = invoke(command("csv-merge", path))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = report(context)
    assert result["status"] == "success"
    assert result["metrics"]["rows"] == 2
    assert result["artifacts"] == [{"name": "merged.csv", "required": True}]
    with (Path(context["output_dir"]) / "merged.csv").open(encoding="utf-8-sig", newline="") as merged:
        assert list(csv.reader(merged)) == [["id", "value"], ["A", "1"], ["B", "2"]]
    assert all(Path(name).read_bytes() == content for name, content in before.items())


@pytest.mark.parametrize("contents,expected", [
    ({"a.csv": "id,value\nA,1\n", "b.csv": "different,header\nB,2\n"}, "表头不一致"),
    ({"a.csv": "id,value\nA,1,extra\n"}, "列数"),
    ({"a.csv": 'id,value\n"unclosed,1\n'}, "CSV 合并失败"),
    ({"a.csv": ""}, "表头"),
    ({"a.txt": "synthetic"}, "没有 CSV"),
])
def test_csv_bad_input_reports_failure(tmp_path, contents, expected):
    path, context = context_file(tmp_path, contents)
    completed = invoke(command("csv-merge", path))
    assert completed.returncode == 2
    result = report(context)
    assert result["status"] == "failed"
    assert expected in result["summary"]
    assert not (Path(context["output_dir"]) / "merged.csv").exists()


@pytest.mark.parametrize("job", ["csv-merge", "node-summary"])
def test_changed_input_rejected(tmp_path, job):
    path, context = context_file(tmp_path)
    Path(context["input_manifest"][0]["path"]).write_text("changed", encoding="utf-8")
    assert invoke(command(job, path)).returncode == 2
    assert report(context)["status"] == "failed"


def test_node_real_process_counts_nested_mixed_files(tmp_path):
    path, context = context_file(tmp_path, {"a.csv": "a\n1\n", "nested/b.json": '{"synthetic":true}',
                                          "说明.txt": "合成文本"})
    completed = invoke(command("node-summary", path))
    assert completed.returncode == 0, completed.stderr
    assert report(context)["status"] == "success"
    stats = json.loads((Path(context["output_dir"]) / "directory-summary.json").read_text(encoding="utf-8"))
    assert stats["files"] == 3
    assert stats["bytes"] == sum(Path(entry["path"]).stat().st_size for entry in context["input_manifest"])
    assert stats["extensions"] == {".csv": 1, ".json": 1, ".txt": 1}


def test_active_failure_produces_report_and_stderr(tmp_path):
    path, context = context_file(tmp_path)
    completed = invoke(command("fail", path))
    assert completed.returncode == 7
    assert "synthetic_failure_started" in completed.stdout
    assert "synthetic_failure_expected" in completed.stderr
    assert report(context)["status"] == "failed"


@pytest.mark.parametrize("job", ["csv-merge", "node-summary", "fail", "wait"])
def test_bad_output_directory_never_writes_into_input(tmp_path, job):
    path, context = context_file(tmp_path)
    context["output_dir"] = context["parameters"]["input_dir"]
    path.write_text(json.dumps(context), encoding="utf-8")
    completed = invoke(command(job, path))
    assert completed.returncode != 0
    assert not (Path(context["output_dir"]) / "result.json").exists()
    assert not list(Path(context["output_dir"]).glob("*.tmp"))


def test_wait_completes_normally(tmp_path):
    path, context = context_file(tmp_path, wait_seconds=1)
    completed = invoke(command("wait", path))
    assert completed.returncode == 0
    assert "waiting elapsed_seconds=0" in completed.stdout
    assert report(context)["status"] == "success"


@pytest.mark.parametrize("cancel", [False, True])
def test_wait_timeout_or_cancel_cleans_process(tmp_path, cancel):
    path, context = context_file(tmp_path, wait_seconds=30)
    process = subprocess.Popen(command("wait", path), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=os.environ | {"PYTHONUTF8": "1"})
    try:
        if cancel:
            time.sleep(0.2)
            Path(context["cancel_file"]).write_text("cancel", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=3)
            assert process.returncode == 130, stderr
            assert "cancel_file_observed" in stdout
            assert report(context)["status"] == "canceled"
        else:
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.4)
            process.kill()
            stdout, _ = process.communicate(timeout=3)
            assert "waiting elapsed_seconds=0" in stdout
            assert process.returncode != 0
            assert not (Path(context["output_dir"]) / "result.json").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
    assert process.poll() is not None


def test_generator_packages_and_refuses_overwrite(tmp_path):
    inputs, bundles = tmp_path / "inputs", tmp_path / "bundles"
    args = [sys.executable, str(EXAMPLES / "prepare.py"), "--input-dir", str(inputs), "--bundle-dir", str(bundles)]
    completed = invoke(args)
    assert completed.returncode == 0, completed.stderr
    assert len(list(inputs.rglob("*.csv"))) == 2
    definitions = json.loads((bundles / "programs.json").read_text(encoding="utf-8"))
    assert len(definitions) == 4
    for definition in definitions:
        assert definition["required_capabilities"] == [definition["command"][0], "directory-input-v1"]
        schema = definition["parameter_schema"]
        assert schema["x-workbench-input"] == "directory"
        assert schema["required"] == ["input_dir", "batch_key"]
        assert schema["properties"]["input_dir"]["maxLength"] == 2000
        assert schema["properties"]["batch_key"]["maxLength"] == 180
        with zipfile.ZipFile(bundles / definition["zip"]) as package:
            assert package.namelist() == [definition["command"][1]]
    before = (inputs / "region-a.csv").read_bytes()
    assert invoke(args).returncode != 0
    assert (inputs / "region-a.csv").read_bytes() == before


def test_generator_rejects_overlapping_directories_before_write(tmp_path):
    inputs = tmp_path / "inputs"
    completed = invoke([sys.executable, str(EXAMPLES / "prepare.py"), "--input-dir", str(inputs),
                        "--bundle-dir", str(inputs / "bundles")])
    assert completed.returncode != 0
    assert not inputs.exists()
