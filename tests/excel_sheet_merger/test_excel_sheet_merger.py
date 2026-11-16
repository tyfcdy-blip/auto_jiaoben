"""真实XLSX、无依赖平台Python和工作台上下文的端到端程序测试。"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "examples" / "excel-sheet-merger"
HELPER = Path(__file__).with_name("workbook_fixture.py")
EXCEL_PYTHON = Path(os.environ.get("EXCEL_TEST_PYTHON", ROOT / "integrations/nine/.venv/Scripts/python.exe"))


def invoke(command: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                          env=os.environ | {"PYTHONUTF8": "1"}, check=False)


@pytest.fixture(scope="session")
def package(tmp_path_factory) -> Path:
    assert EXCEL_PYTHON.is_file(), "缺少隔离的openpyxl测试环境"
    directory = tmp_path_factory.mktemp("excel-merger-package")
    archive = directory / "excel-sheet-merger.zip"
    completed = invoke([sys.executable, str(SOURCE / "build_package.py"), "--dependency-python", str(EXCEL_PYTHON),
                        "--output", str(archive)])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    extracted = directory / "extracted"
    with zipfile.ZipFile(archive) as bundle:
        assert len(bundle.namelist()) < 1000
        assert "main.py" in bundle.namelist()
        assert "THIRD_PARTY_NOTICES.md" in bundle.namelist()
        assert any(name.startswith("openpyxl/") for name in bundle.namelist())
        assert any(name.endswith(".dist-info/LICENCE.rst") for name in bundle.namelist())
        bundle.extractall(extracted)
    return extracted


def create_inputs(directory: Path, mode: str) -> None:
    completed = invoke([str(EXCEL_PYTHON), str(HELPER), "create", str(directory), mode])
    assert completed.returncode == 0, completed.stdout + completed.stderr


def context_file(tmp_path: Path, mode: str = "basic", **parameters) -> tuple[Path, dict, dict[str, bytes]]:
    inputs, output, state = tmp_path / "input", tmp_path / "output", tmp_path / "state"
    create_inputs(inputs, mode)
    output.mkdir()
    state.mkdir()
    manifest = []
    before = {}
    for path in sorted((path for path in inputs.rglob("*") if path.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(inputs).as_posix()
        content = path.read_bytes()
        before[relative] = content
        manifest.append({"name": relative, "path": str(path), "sha256": hashlib.sha256(content).hexdigest()})
    context = {"protocol_version": "1", "run_id": "synthetic-run", "attempt_id": "synthetic-attempt",
               "operation": "run", "parameters": {"input_dir": str(inputs), "batch_key": "synthetic-batch",
                                                     **parameters},
               "input_manifest": manifest, "output_dir": str(output), "state_dir": str(state),
               "cancel_file": str(state / "cancel")}
    target = tmp_path / "context.json"
    target.write_text(json.dumps(context), encoding="utf-8")
    return target, context, before


def run_program(package: Path, context: Path) -> subprocess.CompletedProcess:
    # sys.executable所在环境没有openpyxl，成功证明ZIP内依赖自足。
    return invoke([sys.executable, str(package / "main.py"), "--context-file", str(context)], timeout=45)


def result(context: dict) -> dict:
    return json.loads((Path(context["output_dir"]) / "result.json").read_text(encoding="utf-8"))


def test_merges_two_real_workbooks_and_preserves_supported_content(package, tmp_path):
    context_path, context, before = context_file(tmp_path)
    completed = run_program(package, context_path)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = result(context)
    assert report["status"] == "success"
    assert report["metrics"]["files"] == 2
    assert report["artifacts"] == [{"name": "合并结果.xlsx", "required": True}]
    output = Path(context["output_dir"]) / "合并结果.xlsx"
    inspected = invoke([str(EXCEL_PYTHON), str(HELPER), "inspect", str(output)])
    assert inspected.returncode == 0, inspected.stderr
    workbook = json.loads(inspected.stdout)
    assert workbook["sheetnames"] == ["report", "report (2)"]
    assert workbook["values"][0][:3] == ["区域", "金额", "说明"]
    assert workbook["values"][3][1] == "=SUM(B2:B3)"
    assert workbook["values"][4][1] == "='report'!B4"
    assert workbook["a1_bold"] and workbook["a1_fill"] == "FF2563EB"
    assert workbook["a2_comment"] == "仅为合成测试"
    assert workbook["a2_hyperlink"] == "https://example.invalid/synthetic"
    assert workbook["merged"] == ["C1:D1"]
    assert workbook["column_a_width"] == 18 and workbook["row_1_height"] == 24
    assert workbook["freeze_panes"] == "B2" and workbook["auto_filter"] == "A1:B3"
    assert workbook["validations"] == 1 and workbook["conditional_formats"] == 2
    assert workbook["validation_formula1"] == '"Source!A,Keep"'
    assert '"prefix"&\'report\'!A2="prefixNORTH-A"' in workbook["conditional_formulas"]
    assert workbook["show_grid_lines"] is False and workbook["zoom"] == 90
    assert workbook["calculation"] == {"mode": "auto", "full": True, "force": True}
    inputs = Path(context["parameters"]["input_dir"])
    assert {name: (inputs / name).read_bytes() for name in before} == before
    assert not list(Path(context["output_dir"]).glob("*.tmp"))


@pytest.mark.parametrize("mode,expected", [("multi", "只能包含一个"), ("table", "表格对象"),
                                             ("defined-name", "局部名称定义"), ("empty", "没有可合并")])
def test_unsupported_or_empty_input_fails_explicitly(package, tmp_path, mode, expected):
    path, context, _ = context_file(tmp_path, mode)
    assert run_program(package, path).returncode == 2
    report = result(context)
    assert report["status"] == "failed" and expected in report["summary"]
    assert not list(Path(context["output_dir"]).glob("*.xlsx"))


@pytest.mark.parametrize("mode,expected", [("sparse", "单元格区域过大"), ("corrupt", "文件可能损坏")])
def test_sparse_or_corrupt_workbook_fails_with_structured_result(package, tmp_path, mode, expected):
    path, context, _ = context_file(tmp_path, mode)
    assert run_program(package, path).returncode == 2
    report = result(context)
    assert report["status"] == "failed" and expected in report["summary"]


def test_unsupported_legacy_excel_fails_instead_of_ignoring(package, tmp_path):
    path, context, _ = context_file(tmp_path, "empty")
    old = Path(context["parameters"]["input_dir"]) / "legacy.xlsm"
    old.write_bytes(b"SYNTHETIC")
    context["input_manifest"].append({"name": old.name, "path": str(old),
                                      "sha256": hashlib.sha256(old.read_bytes()).hexdigest()})
    path.write_text(json.dumps(context), encoding="utf-8")
    assert run_program(package, path).returncode == 2
    assert "仅接受.xlsx" in result(context)["summary"]


def test_changed_input_hash_is_rejected(package, tmp_path):
    path, context, _ = context_file(tmp_path)
    Path(context["input_manifest"][0]["path"]).write_bytes(b"changed")
    assert run_program(package, path).returncode == 2
    assert "清单不一致" in result(context)["summary"]


def test_unknown_parameter_is_rejected(package, tmp_path):
    path, context, _ = context_file(tmp_path, output_name="月度合并.xlsx")
    assert run_program(package, path).returncode == 2
    assert "只允许input_dir和batch_key参数" in result(context)["summary"]


def test_cancel_before_opening_workbooks(package, tmp_path):
    path, context, before = context_file(tmp_path)
    Path(context["cancel_file"]).write_text("cancel", encoding="utf-8")
    assert run_program(package, path).returncode == 130
    assert result(context)["status"] == "canceled"
    inputs = Path(context["parameters"]["input_dir"])
    assert {name: (inputs / name).read_bytes() for name in before} == before


def test_build_refuses_to_overwrite_existing_package(tmp_path):
    archive = tmp_path / "program.zip"
    archive.write_bytes(b"existing")
    completed = invoke([sys.executable, str(SOURCE / "build_package.py"), "--dependency-python", str(EXCEL_PYTHON),
                        "--output", str(archive)])
    assert completed.returncode != 0
    assert archive.read_bytes() == b"existing"


def test_program_metadata_matches_folder_contract():
    definition = json.loads((SOURCE / "program.json").read_text(encoding="utf-8"))
    assert definition["command"] == ["python", "main.py"]
    assert definition["required_capabilities"] == ["python", "directory-input-v1"]
    assert definition["parameter_schema"]["x-workbench-input"] == "directory"
    assert definition["parameter_schema"]["required"] == ["input_dir", "batch_key"]
