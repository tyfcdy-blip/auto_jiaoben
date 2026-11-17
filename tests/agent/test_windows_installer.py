import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from workbench.agent_package import windows_agent_package


def connection_file(path: Path, runtime: Path | None = None) -> tuple[Path, dict]:
    payload = {
        "format": "workbench-connection-v1",
        "node_id": "synthetic-windows-node",
        "name": "合成Windows执行电脑",
        "base_url": "https://workbench.example.invalid/automation",
        "token": "SYNTHETIC_NODE_TOKEN_ONLY_0123456789",
        "runtimes": {"python": str(runtime or sys.executable)},
        "capabilities": ["python"],
    }
    target = path / "workbench-connection.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target, payload


def extracted_package(path: Path) -> Path:
    target = path / "package"
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(windows_agent_package())) as archive:
        archive.extractall(target)
    return target


def invoke(package: Path, connection: Path, install: Path, imported: Path, startup: Path):
    return subprocess.run([
        sys.executable, str(package / "installer.py"), "--connection", str(connection),
        "--install-root", str(install), "--connection-dir", str(imported),
        "--startup-dir", str(startup), "--no-start", "--accept-config",
    ], capture_output=True, text=True, encoding="utf-8", timeout=90,
       env=os.environ | {"PYTHONUTF8": "1"}, check=False)


def test_real_one_click_installer_imports_and_creates_user_autostart_without_secret(tmp_path):
    package = extracted_package(tmp_path)
    connection, payload = connection_file(tmp_path)
    install, imported, startup = tmp_path / "install", tmp_path / "connection", tmp_path / "startup"
    first = invoke(package, connection, install, imported, startup)
    assert first.returncode == 0, first.stdout + first.stderr
    config = json.loads((imported / "agent.json").read_text(encoding="utf-8"))
    assert config["base_url"] == payload["base_url"]
    assert (imported / "node-token.txt").read_text(encoding="utf-8") == payload["token"]
    assert (imported / "run-agent-background.py").is_file()
    launchers = list(startup.glob("AutomationWorkbench-*.vbs"))
    assert len(launchers) == 1
    non_secret = (imported / "agent.json").read_text(encoding="utf-8") + launchers[0].read_text(encoding="utf-16")
    non_secret += (imported / "run-agent-background.py").read_text(encoding="utf-8")
    assert payload["token"] not in non_secret
    assert "limit=5*1024*1024" in non_secret
    compile_result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(imported / "run-agent-background.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    assert connection.is_file()
    token_hash = hashlib.sha256((imported / "node-token.txt").read_bytes()).hexdigest()
    second = invoke(package, connection, install, imported, startup)
    assert second.returncode == 0, second.stdout + second.stderr
    assert hashlib.sha256((imported / "node-token.txt").read_bytes()).hexdigest() == token_hash


def test_generated_background_runner_bounds_large_unicode_log_with_newlines(tmp_path):
    package = extracted_package(tmp_path)
    connection, _ = connection_file(tmp_path)
    install, imported, startup = tmp_path / "install", tmp_path / "connection", tmp_path / "startup"
    installed = invoke(package, connection, install, imported, startup)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    repeats = 2 * 1024 * 1024
    payload_size = len(("汉\n" * repeats).encode("utf-8"))
    (install / "agent/__main__.py").write_text(
        "import sys\n\ndef main():\n"
        f"    sys.stdout.write('汉\\n' * {repeats})\n"
        "    return 0\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(imported / "run-agent-background.py")],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    current_log = imported / "agent.log"
    backup_log = imported / "agent.log.1"
    assert current_log.stat().st_size <= 5 * 1024 * 1024
    assert backup_log.stat().st_size <= 5 * 1024 * 1024
    assert current_log.stat().st_size + backup_log.stat().st_size == payload_size


def test_invalid_connection_does_not_install_or_create_startup(tmp_path):
    package = extracted_package(tmp_path)
    connection = tmp_path / "bad.json"
    connection.write_text('{"format":"wrong"}', encoding="utf-8")
    install, imported, startup = tmp_path / "install", tmp_path / "connection", tmp_path / "startup"
    result = invoke(package, connection, install, imported, startup)
    assert result.returncode == 2
    assert not install.exists() and not imported.exists() and not startup.exists()
    assert "Traceback" not in result.stderr


def test_existing_binding_upgrade_keeps_config_token_state_and_changes_runtime(tmp_path):
    package = extracted_package(tmp_path)
    connection, _ = connection_file(tmp_path)
    imported = tmp_path / ".automation-workbench/connections/synthetic-windows-node"
    old_install, startup = tmp_path / "old-runtime", tmp_path / "startup"
    first = invoke(package, connection, old_install, imported, startup)
    assert first.returncode == 0, first.stdout + first.stderr
    (old_install / "package-version.txt").write_text("0.1.0", encoding="utf-8")
    (imported / "recovery-evidence.txt").write_text("keep-evidence", encoding="utf-8")
    preserved = {name: (imported / name).read_bytes()
                 for name in ("agent.json", "node-token.txt", "recovery-evidence.txt")}
    new_install = tmp_path / "new-runtime"
    result = subprocess.run([
        sys.executable, str(package / "installer.py"), "--upgrade-existing",
        "--install-root", str(new_install), "--startup-dir", str(startup), "--accept-config",
    ], capture_output=True, text=True, encoding="utf-8", timeout=90,
        env=os.environ | {"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "PYTHONUTF8": "1"})
    assert result.returncode == 0, result.stdout + result.stderr
    for name, content in preserved.items():
        assert (imported / name).read_bytes() == content
    assert old_install.is_dir() and new_install.is_dir()
    wrapper = (imported / "run-agent-background.py").read_text(encoding="utf-8")
    assert repr(str(new_install)) in wrapper and repr(str(old_install)) not in wrapper
    assert "0.6.1-rc.1" in (new_install / "package-version.txt").read_text()
    assert "node-files-v1" in (new_install / "agent/node_files.py").read_text(encoding="utf-8")
    assert len(list(startup.glob("*.vbs"))) == 1
    assert "node-token" not in result.stdout


def test_upgrade_refuses_ambiguous_binding_without_installing(tmp_path):
    package = extracted_package(tmp_path)
    for name in ("one", "two"):
        directory = tmp_path / ".automation-workbench/connections" / name
        directory.mkdir(parents=True)
        (directory / "agent.json").write_text('{}')
    result = subprocess.run([
        sys.executable, str(package / "installer.py"), "--upgrade-existing",
        "--install-root", str(tmp_path / "new-runtime"), "--accept-config",
    ], capture_output=True, timeout=30,
        env=os.environ | {"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "PYTHONUTF8": "1"})
    assert result.returncode == 2
    assert not (tmp_path / "new-runtime").exists()


def test_installer_never_executes_runtime_path_from_connection_file(tmp_path):
    package = extracted_package(tmp_path)
    untrusted = tmp_path / "declared-runtime.exe"
    untrusted.write_text("not an executable", encoding="utf-8")
    connection, _ = connection_file(tmp_path, untrusted)
    result = invoke(package, connection, tmp_path / "install", tmp_path / "connection", tmp_path / "startup")
    assert result.returncode == 0, result.stdout + result.stderr
    config = json.loads((tmp_path / "connection/agent.json").read_text(encoding="utf-8"))
    assert config["runtimes"]["python"] == str(untrusted)


def test_user_must_confirm_visible_connection_details_before_install(tmp_path):
    package = extracted_package(tmp_path)
    connection, payload = connection_file(tmp_path)
    completed = subprocess.run([
        sys.executable, str(package / "installer.py"), "--connection", str(connection),
        "--install-root", str(tmp_path / "install"), "--connection-dir", str(tmp_path / "connection"),
        "--startup-dir", str(tmp_path / "startup"), "--no-start",
    ], input="NO\n", capture_output=True, text=True, encoding="utf-8", timeout=30,
       env=os.environ | {"PYTHONUTF8": "1"}, check=False)
    assert completed.returncode == 2
    assert payload["base_url"] in completed.stdout
    assert payload["token"] not in completed.stdout + completed.stderr
    assert not (tmp_path / "install").exists()


@pytest.mark.skipif(os.name != "nt", reason="真实CMD入口仅适用于Windows")
def test_upgrade_cmd_works_from_chinese_windows_codepage(tmp_path):
    package = extracted_package(tmp_path)
    launcher = tmp_path / 'test-upgrade.cmd'
    launcher.write_bytes(b'@echo off\r\nchcp 936 >nul\r\ncall "%UPGRADE_ENTRY%" --accept-config\r\nexit /b %errorlevel%\r\n')
    completed = subprocess.run([
        os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher),
    ], input=b"\n", capture_output=True, timeout=30,
        env=os.environ | {"USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "PYTHONUTF8": "1",
                          "UPGRADE_ENTRY": str(package / "升级已绑定电脑.cmd")})
    assert completed.returncode == 2  # 无已有绑定：必须到达Python校验，而不是CMD找不到入口。
    assert b"is not recognized" not in completed.stdout + completed.stderr
    assert "没有找到唯一" in (completed.stdout + completed.stderr).decode('utf-8', errors='replace')


@pytest.mark.skipif(os.name != "nt", reason="真实CMD入口仅适用于Windows")
def test_real_cmd_entry_propagates_installer_failure(tmp_path):
    package = extracted_package(tmp_path)
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"format":"wrong"}', encoding="utf-8")
    completed = subprocess.run([
        os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(package / "安装并连接.cmd"),
        "--connection", str(invalid), "--install-root", str(tmp_path / "install"),
        "--connection-dir", str(tmp_path / "connection"), "--startup-dir", str(tmp_path / "startup"),
        "--no-start", "--no-autostart", "--accept-config",
    ], input=b"\n", capture_output=True, timeout=30, check=False)
    assert completed.returncode == 2
    assert b"is not recognized" not in completed.stdout + completed.stderr
    assert not (tmp_path / "install").exists()
