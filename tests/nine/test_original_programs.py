"""真实执行显式授权源脚本的复制包，合并计算不Mock，外部写入始终禁止。"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from jsonschema import Draft202012Validator
import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("synthetic_workbooks.py")


@pytest.fixture(scope="module")
def runtime():
    source = os.environ.get("NINE_SOURCE_DIRECTORY")
    python = os.environ.get("NINE_TEST_PYTHON")
    if not source or not python:
        pytest.skip("需显式NINE_SOURCE_DIRECTORY和独立NINE_TEST_PYTHON；不自动读取个人目录")
    assert Path(source).is_dir() and Path(python).is_file()
    return Path(source), python


@pytest.fixture
def packaged(runtime, tmp_path):
    source, python = runtime
    bundle = tmp_path / "nine.zip"
    process = subprocess.run([sys.executable, str(ROOT / "integrations/nine/package.py"),
                              "--source", str(source), "--output", str(bundle)],
                             capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert process.returncode == 0, process.stderr
    package = tmp_path / "package"
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {
            "legacy/pdd_billing_check.py", "legacy/pdd_billing_warehouse_load.py",
            "adapter.py", "legacy_launcher.py", "requirements.txt", "parameters.schema.json", "source-manifest.json",
        }
        archive.extractall(package)
    for name in ("pdd_billing_check.py", "pdd_billing_warehouse_load.py"):
        assert (source / name).read_bytes() == (package / "legacy" / name).read_bytes()
    return package, python


def context_for(packaged, tmp_path, operation, scenario="normal"):
    package, python = packaged
    inputs = tmp_path / "inputs"
    created = subprocess.run([python, "-X", "utf8", str(FIXTURES), "create", "--directory", str(inputs),
                              "--scenario", scenario], capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert created.returncode == 0, created.stderr
    manifest = [{"name": path.name, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted(inputs.glob("*.xlsx"))]
    context = {"protocol_version": "1", "run_id": "synthetic-run-001", "attempt_id": "synthetic-attempt-001",
               "operation": operation, "parameters": {"period": "2026-07", "mapping_file": "shops.xlsx",
                   "bill_files": ["SYNTHETIC_SHOP_A.xlsx", "SYNTHETIC_SHOP_B.xlsx"]},
               "input_manifest": manifest, "output_dir": str(tmp_path / "output"),
               "state_dir": str(tmp_path / "state"), "cancel_file": str(tmp_path / "cancel.flag")}
    schema = json.loads((ROOT / "contracts/runtime/context.schema.json").read_text())
    Draft202012Validator(schema).validate(context)
    return context


def execute(packaged, tmp_path, context):
    package, python = packaged
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    response = subprocess.run([python, "-X", "utf8", str(package / "adapter.py"), "--context-file", str(path)],
                              capture_output=True, text=True, encoding="utf-8", timeout=30)
    result_path = Path(context["output_dir"]) / "result.json"
    assert result_path.is_file(), response.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "contracts/runtime/result.schema.json").read_text())
    Draft202012Validator(schema).validate(result)
    return response, result


def test_report_runs_original_aggregation_and_preserves_inputs(packaged, tmp_path):
    context = context_for(packaged, tmp_path, "check")
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 0, response.stderr
    assert result["status"] == "success" and len(result["artifacts"]) == 2
    for artifact in result["artifacts"]:
        report = Path(context["output_dir"]) / artifact["name"]
        read = subprocess.run([packaged[1], "-X", "utf8", str(FIXTURES), "inspect", "--report", str(report)],
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert read.returncode == 0, read.stderr
        values = json.loads(read.stdout)
        assert values["merchant_receivable"] == 130
        assert values["promotion"] == -8.5 and values["review_gift"] == -2
        assert values["cashflow_total"] == ["总计", 130, -6]
    for entry in context["input_manifest"]:
        assert hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def test_preflight_uses_real_source_without_credentials_or_upload(packaged, tmp_path):
    context = context_for(packaged, tmp_path, "preflight")
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 0, response.stderr
    assert result["status"] == "success" and result["metrics"]["total_rows"] == 22
    assert result["metrics"]["network_allowed"] is False
    manifest = json.loads((Path(context["output_dir"]) / "synthetic-run-001_manifest.json").read_text(encoding="utf-8"))
    assert manifest["execute"] is False and manifest["input_file_count"] == 2
    assert not (Path(context["output_dir"]) / ".nine-work/unused-requests").exists()
    assert not (Path(context["output_dir"]) / ".nine-work/unused-id-state.json").exists()


@pytest.mark.parametrize("operation", ["check", "preflight"])
@pytest.mark.parametrize("scenario", ["missing_sheet", "missing_mapping"])
def test_original_failures_are_not_success(packaged, tmp_path, operation, scenario):
    context = context_for(packaged, tmp_path, operation, scenario)
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode != 0
    assert result["status"] == "failed"


@pytest.mark.parametrize("scenario", ["invalid_amount", "invalid_date"])
def test_existing_preflight_field_validation_limit_is_explicit(packaged, tmp_path, scenario):
    context = context_for(packaged, tmp_path, "preflight", scenario)
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 0 and result["status"] == "success"
    assert "金额、日期逐字段" in result["summary"]
    context["operation"] = "check"
    context["output_dir"] = str(tmp_path / "check-output")
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 2 and result["status"] == "failed"


@pytest.mark.parametrize("operation", ["check", "preflight"])
def test_empty_tables_preserve_original_behavior_without_assuming_business_completeness(packaged, tmp_path, operation):
    context = context_for(packaged, tmp_path, operation, "empty_data")
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 0 and result["status"] == "success"
    if operation == "preflight":
        assert result["metrics"]["total_rows"] == 0
    else:
        for artifact in result["artifacts"]:
            read = subprocess.run([packaged[1], "-X", "utf8", str(FIXTURES), "inspect", "--report",
                                   str(Path(context["output_dir"]) / artifact["name"])],
                                  capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert read.returncode == 0
            assert json.loads(read.stdout)["merchant_receivable"] == 0


@pytest.mark.parametrize("violation", ["upload", "tampered_input", "extra_parameter", "bad_period", "duplicate_file"])
def test_unsafe_requests_stop_before_legacy_execution(packaged, tmp_path, violation):
    context = context_for(packaged, tmp_path, "check")
    if violation == "upload":
        context["operation"] = "upload"
    elif violation == "tampered_input":
        with Path(context["input_manifest"][0]["path"]).open("ab") as stream:
            stream.write(b"not-the-frozen-input")
    elif violation == "extra_parameter":
        context["parameters"]["execute"] = True
    elif violation == "duplicate_file":
        context["parameters"]["bill_files"].append("SYNTHETIC_SHOP_A.xlsx")
    else:
        context["parameters"]["period"] = "2026-99"
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 1 and result["status"] == "failed"
    assert "nine operation=" not in response.stdout


def test_cancel_before_start_does_not_execute_original(packaged, tmp_path):
    context = context_for(packaged, tmp_path, "check")
    Path(context["cancel_file"]).touch()
    response, result = execute(packaged, tmp_path, context)
    assert response.returncode == 130 and result["status"] == "canceled"
    assert result["artifacts"] == []


def test_network_guard_blocks_socket_before_any_connect(packaged, tmp_path):
    package, python = packaged
    synthetic = tmp_path / "pdd_billing_check.py"
    synthetic.write_text("import socket\nsocket.create_connection(('127.0.0.1', 1))\n", encoding="utf-8")
    process = subprocess.run([python, "-X", "utf8", str(package / "legacy_launcher.py"), str(synthetic)],
                             capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert process.returncode != 0
    assert "offline_adapter_network_or_child_process_forbidden" in process.stderr
