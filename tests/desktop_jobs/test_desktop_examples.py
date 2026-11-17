"""独立程序包真实执行及受控浏览器测试，不连接生产环境。"""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile

from jsonschema import validate
import pytest

from workbench.bundle_scan import scan_bundle

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("desktop_builder", ROOT / "examples/desktop-jobs/build_packages.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
spec = importlib.util.spec_from_file_location("desktop_runtime", ROOT / "examples/desktop-jobs/job_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def packages(tmp_path):
    target = tmp_path / "packages"
    builder.build(target)
    return target


def invoke(tmp_path, packages, slug, parameters=None, python=sys.executable):
    package = tmp_path / slug
    package.mkdir()
    with zipfile.ZipFile(packages / f"{slug}.zip") as archive:
        archive.extractall(package)
    output = tmp_path / "output"
    output.mkdir()
    context = {"protocol_version": "1", "run_id": "synthetic-desktop", "attempt_id": "synthetic-attempt",
               "operation": "run", "parameters": parameters or {}, "input_manifest": [],
               "output_dir": str(output), "state_dir": str(tmp_path / "state"),
               "cancel_file": str(tmp_path / "cancel")}
    validate(context, json.loads((ROOT / "contracts/runtime/context.schema.json").read_text()))
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "LANG")
                   if key in os.environ}
    environment.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
                       PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    process = subprocess.run([python, str(package / "main.py"), "--context-file", str(context_path)],
                             cwd=package, env=environment, capture_output=True, text=True, encoding="utf-8",
                             timeout=60, check=False)
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    validate(result, json.loads((ROOT / "contracts/runtime/result.schema.json").read_text()))
    assert result["run_id"] == context["run_id"]
    assert result["attempt_id"] == context["attempt_id"]
    assert not list(output.glob("*.tmp"))
    return process, result, output


def test_packages_scan_and_parameter_schema(packages):
    for program in json.loads((packages / "programs.json").read_text(encoding="utf-8")):
        validate(program["example_parameters"], program["parameter_schema"])
        scanned = scan_bundle((packages / program["zip"]).read_bytes(), "main.py", "python")
        assert scanned["decision"] == "pass", scanned
    with pytest.raises(ValueError, match="拒绝覆盖"):
        builder.build(packages)


def test_local_files(packages, tmp_path):
    completed, result, output = invoke(tmp_path, packages, "local-files")
    assert completed.returncode == 0, completed.stderr
    assert result["status"] == "success"
    assert result["metrics"]["files"] == 3
    with zipfile.ZipFile(output / "local-files.zip") as archive:
        assert len(archive.namelist()) == 3
        for name in archive.namelist():
            assert archive.read(name) == (output / "synthetic-source" / name).read_bytes()
            assert archive.read(name) == (output / "synthetic-copy" / name).read_bytes()


@pytest.mark.parametrize("url", ["http://example.com", "file:///C:/Windows/win.ini",
                                 "https://user:password@example.com", "javascript:alert(1)", 42,
                                 "https://127.0.0.1/", "https://intranet/", "https://example.com/?secret=1",
                                 "https://example.com/private", "https://example.com:8443/"])
def test_reject_unsafe_url(url):
    with pytest.raises(ValueError):
        runtime.page_url({"url": url})


def test_default_page_is_package_file():
    assert runtime.page_url({}).endswith("/examples/desktop-jobs/demo.html")


@pytest.mark.parametrize("method,url,initial,allowed", [
    ("GET", "https://example.com/", "https://example.com/", True),
    ("HEAD", "https://example.com/style.css", "https://example.com/", True),
    ("POST", "https://example.com/", "https://example.com/", False),
    ("GET", "https://internal.local/", "https://example.com/", False),
    ("GET", "https://example.com.attacker.com/", "https://example.com/", False),
    ("GET", "https://example.com:8443/", "https://example.com/", False),
    ("GET", "https://name:secret@example.com/", "https://example.com/", False),
    ("GET", "http://example.com/", "https://example.com/", False),
    ("GET", "https://example.com/", "file:///demo.html", False),
    ("GET", "file:///demo.html", "file:///demo.html", True),
    ("GET", "file:///secret.txt", "file:///demo.html", False),
])
def test_request_origin_and_method_restrictions(method, url, initial, allowed):
    assert runtime.request_allowed(method, url, initial) is allowed


def test_arbitrary_executable_parameter_rejected(packages, tmp_path):
    completed, result, _ = invoke(tmp_path, packages, "web-screenshot",
                                  {"browser_path": "C:/Windows/System32/cmd.exe"})
    assert completed.returncode == 2
    assert result["status"] == "failed"
    assert "未定义" in result["summary"]


def test_invalid_parameters_report_failure(packages, tmp_path):
    completed, result, _ = invoke(tmp_path, packages, "web-screenshot", {"wait_seconds": 0})
    assert completed.returncode == 2
    assert result["status"] == "failed"
    assert result["artifacts"] == []


def test_failure_does_not_leak_url(packages, tmp_path):
    completed, result, _ = invoke(tmp_path, packages, "web-screenshot",
                                  {"url": "https://user:secret@example.com/?access_token=secret"})
    assert completed.returncode == 2
    assert "secret" not in json.dumps(result) + completed.stdout + completed.stderr


def test_real_browser_screenshot_in_agent_environment(packages, tmp_path):
    python = os.environ.get("DESKTOP_JOB_PYTHON")
    if not python:
        pytest.skip("需提供安装 Playwright 的独立 Python：DESKTOP_JOB_PYTHON")
    completed, result, output = invoke(tmp_path, packages, "web-screenshot", python=python)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result["status"] == "success"
    png = (output / "page.png").read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (1280, 720)
    info = json.loads((output / "page-info.json").read_text(encoding="utf-8"))
    assert info["title"] == "工作台网页自动化验收"
    assert info["source"] == "offline-demo"
    assert "notification_text" in result


@pytest.mark.parametrize("canceled", [False, True])
def test_real_visible_browser_and_cancel(packages, tmp_path, canceled):
    python = os.environ.get("DESKTOP_JOB_PYTHON")
    if not python:
        pytest.skip("需提供安装 Playwright 的独立 Python：DESKTOP_JOB_PYTHON")
    if canceled:
        (tmp_path / "cancel").touch()
    completed, result, _ = invoke(tmp_path, packages, "open-web", {"wait_seconds": 1}, python=python)
    assert completed.returncode == (130 if canceled else 0), completed.stdout + completed.stderr
    assert result["status"] == ("canceled" if canceled else "success")
