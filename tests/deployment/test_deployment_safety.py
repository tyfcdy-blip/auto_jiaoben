"""部署边界测试；不连接生产环境，也不以这些测试替代容器演练。"""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load_module("bootstrap")
operations = load_module("operations")


@pytest.fixture(scope="module")
def rendered_compose():
    standalone = shutil.which("docker-compose")
    if standalone is None and os.name == "nt":
        candidate = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe")
        standalone = str(candidate) if candidate.exists() else None
    if standalone:
        command = [standalone]
    elif shutil.which("docker"):
        command = ["docker", "compose"]
    else:
        pytest.skip("本机未安装Compose；部署配置解析未验证")
    env = {key: value for key, value in os.environ.items() if not key.startswith("WB_")}
    response = subprocess.run(
        command + ["--file", str(ROOT / "deploy" / "compose.yaml"), "--profile", "admin",
                   "config", "--format", "json"],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
    )
    return json.loads(response.stdout)


def test_secrets_are_unique_valid_and_never_overwritten(tmp_path, capsys):
    directory = tmp_path / "private"
    created = bootstrap.initialize(directory)
    assert set(created) == set(bootstrap.SECRET_NAMES)
    key = (directory / "encryption_key").read_text().strip()
    assert len(base64.urlsafe_b64decode(key)) == 32
    password = (directory / "database_password").read_text().strip()
    assert password in (directory / "database_url").read_text()
    assert len({(directory / name).read_text() for name in created}) == len(created)
    with pytest.raises(ValueError, match="拒绝覆盖"):
        bootstrap.initialize(directory)
    assert (directory / "encryption_key").read_text().strip() == key
    assert key not in capsys.readouterr().out


def test_database_is_private_and_startup_is_ordered(rendered_compose):
    services = rendered_compose["services"]
    assert not services["db"].get("ports")
    assert services["init-db"]["depends_on"]["db"]["condition"] == "service_healthy"
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["init-db"]["condition"] == "service_completed_successfully"
    assert services["init-admin"]["profiles"] == ["admin"]
    assert services["init-admin"]["command"][-1] == "init-admin"


def test_business_secrets_are_not_environment_values(rendered_compose):
    services = rendered_compose["services"]
    for name in ("api", "worker", "init-db"):
        service = services[name]
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert "ALL" in service["cap_drop"]
        env = service["environment"]
        assert "WB_SECRET_KEY" not in env and "WB_DATABASE_URL" not in env
        assert env["WB_SECRET_KEY_FILE"] == "/run/secrets/encryption_key"
        assert "admin_password" not in {entry["source"] for entry in service["secrets"]}
        assert all("docker.sock" not in str(volume) for volume in service.get("volumes", []))


def test_local_bind_and_bounded_logs(rendered_compose):
    for port in rendered_compose["services"]["api"]["ports"]:
        assert port["host_ip"] == "127.0.0.1"
    for service in rendered_compose["services"].values():
        assert service["logging"]["options"]["max-file"] == "3"
        assert service["logging"]["options"]["max-size"] == "10m"


@pytest.mark.parametrize("name", ["../other", "PROJECT", "bad;command", "", "a"])
def test_operations_reject_ambiguous_project_name(name):
    with pytest.raises(ValueError):
        operations.command(name)


def test_backup_refuses_active_services(monkeypatch, tmp_path):
    monkeypatch.setattr(operations, "execute", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "container-id"))
    with pytest.raises(ValueError, match="仍在运行"):
        operations.backup(["docker", "compose"], "isolated-test", tmp_path / "new", "test")
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("entry_name,entry_type", [("../outside", tarfile.REGTYPE),
                                                  ("/absolute", tarfile.REGTYPE),
                                                  ("link", tarfile.SYMTYPE)])
def test_restore_rejects_unsafe_archive(tmp_path, entry_name, entry_type):
    archive_path = tmp_path / "archive.tar"
    with tarfile.open(archive_path, "w") as archive:
        entry = tarfile.TarInfo(entry_name)
        entry.type = entry_type
        entry.linkname = "../../outside" if entry_type == tarfile.SYMTYPE else ""
        archive.addfile(entry, io.BytesIO())
    with pytest.raises(ValueError):
        operations.check_archive(archive_path)


def test_restore_requires_exact_project_confirmation_before_io(tmp_path):
    with pytest.raises(ValueError, match="confirm-project"):
        operations.restore(["docker", "compose"], "isolated-test", tmp_path, "other-project")


def test_restore_checks_digest_before_starting_any_service(monkeypatch, tmp_path):
    (tmp_path / "database.dump").write_bytes(b"tampered")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "format": 1, "restore_requires_reconciliation": True,
        "files": {"database.dump": "incorrect"},
    }))
    def forbidden(*args, **kwargs):
        raise AssertionError("校验失败时不能启动容器或还原数据库")
    monkeypatch.setattr(operations, "execute", forbidden)
    with pytest.raises(ValueError, match="校验失败"):
        operations.restore(["docker", "compose"], "isolated-test", tmp_path, "isolated-test")


def valid_backup(directory):
    (directory / "database.dump").write_bytes(b"synthetic-boundary-test-dump")
    with tarfile.open(directory / "platform-data.tar", "w") as archive:
        entry = tarfile.TarInfo("report.txt")
        entry.size = 4
        archive.addfile(entry, io.BytesIO(b"test"))
    (directory / "manifest.json").write_text(json.dumps({
        "format": 1, "restore_requires_reconciliation": True,
        "files": {name: operations.digest(directory / name)
                  for name in ("database.dump", "platform-data.tar")},
    }), encoding="utf-8")


def test_restore_disables_database_dispatch_before_restoring_files(monkeypatch, tmp_path, capsys):
    valid_backup(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="")
    monkeypatch.setattr(operations, "execute", run)
    operations.restore(["docker", "compose"], "isolated-test", tmp_path, "isolated-test")
    restore_index = next(i for i, call in enumerate(calls) if "pg_restore" in call)
    guard = calls[restore_index + 1]
    assert "psql" in guard and "ON_ERROR_STOP=1" in guard and "--single-transaction" in guard
    assert "-X" in guard and guard[-1] == operations.DISABLE_RESTORED_DISPATCH_SQL
    assert "public.system_gate SET dispatch_enabled = false" in guard[-1]
    assert "changed_rows <> 1" in guard[-1] and "schema_version = 1" in guard[-1]
    assert calls[restore_index + 2][-2:] == ["-xf", "-"]
    assert all("up" not in call and "start" not in call for call in calls)
    assert "数据库派发开关已关闭" in capsys.readouterr().out


@pytest.mark.parametrize("failed_command", ["pg_restore", "psql"])
def test_restore_database_or_guard_failure_stops_before_files(monkeypatch, tmp_path, capsys, failed_command):
    valid_backup(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if failed_command in command:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="")
    monkeypatch.setattr(operations, "execute", run)
    with pytest.raises(subprocess.CalledProcessError):
        operations.restore(["docker", "compose"], "isolated-test", tmp_path, "isolated-test")
    assert failed_command in calls[-1]
    assert all("tar" not in call and "up" not in call and "start" not in call for call in calls)
    assert "恢复文件已写入" not in capsys.readouterr().out
