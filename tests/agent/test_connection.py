import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent.config import AgentError, Config
from agent.connection import import_connection


@pytest.fixture
def connection(tmp_path):
    source = tmp_path / "download.json"
    payload = {"format": "workbench-connection-v1", "node_id": "synthetic-node-01", "name": "合成执行电脑",
               "base_url": "https://workbench.example.invalid/automation", "token": "SYNTHETIC_NODE_TOKEN_ONLY_0123456789",
               "runtimes": {"python": sys.executable}, "capabilities": ["python"]}
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source, payload, tmp_path / "connection"


def test_real_cli_import_protects_token_and_does_not_connect(connection):
    source, payload, directory = connection
    process = subprocess.run([sys.executable, "-m", "agent", "--import-connection", str(source),
                              "--connection-dir", str(directory)], capture_output=True, text=True, encoding="utf-8", timeout=20,
                             env=os.environ | {"PYTHONUTF8": "1"})
    assert process.returncode == 0
    assert payload["token"] not in process.stdout + process.stderr
    config = Config.load(directory / "agent.json")
    assert config.read_token() == payload["token"]
    assert config.base_url == payload["base_url"] and config.runtimes == payload["runtimes"]
    assert config.isolation == "development" and not config.state_dir.exists()
    assert payload["token"] not in (directory / "agent.json").read_text(encoding="utf-8")
    assert "--config" in process.stdout and source.exists()
    if os.name == "nt":
        # 使用真实系统工具核验新目录不存在继承 ACE，不回显账号信息。
        inspected = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"), str(directory)],
                                   capture_output=True, timeout=10)
        assert inspected.returncode == 0
        assert b"(I)" not in inspected.stdout
    else:
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "node-token.txt").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("updates", [
    {"base_url": "http://remote.example.invalid"}, {"base_url": "https://host.invalid/a/../b"},
    {"base_url": {"bad": "type"}}, {"format": "unknown"}, {"node_id": "../escape"},
    {"token": "bad\nvalue"}, {"runtimes": {"python": "python.exe"}},
    {"runtimes": {"python": "/missing/runtime/python"}}, {"capabilities": "python"},
    {"isolation": "os_user"}, {"slots": 16},
])
def test_import_invalid_input_creates_no_directory(connection, updates):
    source, payload, directory = connection
    source.write_text(json.dumps(payload | updates), encoding="utf-8")
    with pytest.raises(AgentError):
        import_connection(source, directory)
    assert not directory.exists()


def test_import_refuses_existing_config_without_modification(connection):
    source, _, directory = connection
    config = import_connection(source, directory)
    snapshots = {path.name: path.read_bytes() for path in directory.iterdir()}
    with pytest.raises(AgentError, match="已存在"):
        import_connection(source, directory)
    assert config.is_file()
    assert snapshots == {path.name: path.read_bytes() for path in directory.iterdir()}


def test_import_permission_failure_leaves_no_partial_credentials(connection, monkeypatch):
    source, _, directory = connection
    def deny(_):
        raise AgentError("合成权限故障")
    monkeypatch.setattr("agent.connection.protect_directory", deny)
    with pytest.raises(AgentError, match="合成权限故障"):
        import_connection(source, directory)
    assert not directory.exists()


def test_import_write_failure_cleans_only_own_files(connection, monkeypatch):
    source, _, directory = connection
    original = Path.open
    def fail_config(path, *args, **kwargs):
        if path == directory / "agent.json" and args and args[0] == "x":
            raise OSError("合成写盘故障")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_config)
    with pytest.raises(OSError, match="合成写盘故障"):
        import_connection(source, directory)
    assert not directory.exists() and source.exists()


def test_import_permission_tool_timeout_is_controlled(connection, monkeypatch):
    source, _, directory = connection
    def timeout(_):
        raise subprocess.TimeoutExpired("synthetic-permission-tool", 10)
    monkeypatch.setattr("agent.connection.protect_directory", timeout)
    with pytest.raises(AgentError, match="权限超时或失败"):
        import_connection(source, directory)
    assert not directory.exists()


def test_real_cli_invalid_connection_does_not_print_payload(connection):
    source, payload, directory = connection
    source.write_text(json.dumps(payload | {"base_url": "http://remote.example.invalid"}), encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "agent", "--import-connection", str(source),
                              "--connection-dir", str(directory)], capture_output=True, text=True, encoding="utf-8", timeout=20,
                             env=os.environ | {"PYTHONUTF8": "1"})
    assert process.returncode == 2
    assert payload["token"] not in process.stdout + process.stderr
    assert "Traceback" not in process.stderr
    assert not directory.exists()
