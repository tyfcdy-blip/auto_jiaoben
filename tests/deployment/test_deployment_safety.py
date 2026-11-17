"""部署边界测试；不连接生产环境，也不以这些测试替代容器演练。"""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("/", "_"),
                                                  ROOT / "deploy" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load_module("bootstrap")
operations = load_module("operations")
delivery_builder = load_module("build_server_delivery")
nginx_patcher = load_module("server/patch_nginx")


def compose_command():
    standalone = shutil.which("docker-compose")
    if standalone is None and os.name == "nt":
        candidate = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe")
        standalone = str(candidate) if candidate.exists() else None
    if standalone:
        return [standalone]
    if shutil.which("docker"):
        return ["docker", "compose"]
    pytest.skip("本机未安装Compose；部署配置解析未验证")


@pytest.fixture(scope="module")
def rendered_compose():
    command = compose_command()
    env = {key: value for key, value in os.environ.items() if not key.startswith("WB_")}
    response = subprocess.run(
        command + ["--file", str(ROOT / "deploy" / "compose.yaml"),
                   "--profile", "bundled-db", "--profile", "admin",
                   "config", "--format", "json"],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
    )
    return json.loads(response.stdout)


@pytest.fixture(scope="module")
def rendered_external_compose():
    env = {key: value for key, value in os.environ.items() if not key.startswith("WB_")}
    env["WB_DATABASE_NETWORK"] = "synthetic-existing-postgres"
    response = subprocess.run(
        compose_command() + [
            "--file", str(ROOT / "deploy" / "compose.yaml"),
            "--file", str(ROOT / "deploy" / "compose.external-db.yaml"),
            "--profile", "admin", "config", "--format", "json",
        ],
        check=True, capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
    )
    return json.loads(response.stdout)


@pytest.fixture(scope="module")
def rendered_server_compose():
    env = {key: value for key, value in os.environ.items() if not key.startswith("WB_")}
    env.update({
        "WB_DATABASE_NETWORK": "synthetic-existing-postgres",
        "WB_INGRESS_NETWORK": "synthetic-shared-nginx",
    })
    response = subprocess.run(
        compose_command() + [
            "--file", str(ROOT / "deploy" / "compose.yaml"),
            "--file", str(ROOT / "deploy" / "compose.external-db.yaml"),
            "--file", str(ROOT / "deploy" / "compose.shared-nginx.yaml"),
            "config", "--format", "json",
        ],
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
    assert services["db"]["profiles"] == ["bundled-db"]
    assert not services["init-db"].get("depends_on")
    for name in ("api", "worker"):
        assert services[name]["depends_on"]["init-db"]["condition"] == "service_completed_successfully"
    assert services["init-admin"]["profiles"] == ["admin"]
    assert services["init-admin"]["command"][-1] == "init-admin"


def test_external_database_mode_does_not_start_bundled_database(rendered_external_compose):
    services = rendered_external_compose["services"]
    assert "db" not in services
    for name in ("init-db", "api", "worker", "init-admin"):
        assert set(services[name]["networks"]) == {"workbench", "database"}
    assert rendered_external_compose["networks"]["database"]["name"] == (
        "synthetic-existing-postgres"
    )


def test_server_mode_connects_only_api_to_shared_nginx(rendered_server_compose):
    services = rendered_server_compose["services"]
    assert "db" not in services
    assert set(services["api"]["networks"]) == {"workbench", "database", "ingress"}
    assert services["api"]["networks"]["ingress"]["aliases"] == [
        "automation-workbench-api"
    ]
    for name in ("init-db", "worker"):
        assert "ingress" not in services[name]["networks"]
    assert rendered_server_compose["networks"]["ingress"]["name"] == (
        "synthetic-shared-nginx"
    )


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


def test_retention_and_capacity_are_wired_to_compose_and_production_template(rendered_compose):
    for name in ("api", "worker", "init-db"):
        env = rendered_compose["services"][name]["environment"]
        assert env["WB_INPUT_RETENTION_DAYS"] == "45"
        assert env["WB_ARTIFACT_RETENTION_DAYS"] == "45"
        assert env["WB_STORAGE_WARNING_BYTES"] == "48318382080"
        assert env["WB_STORAGE_MAX_BYTES"] == "64424509440"
    production = {}
    for raw_line in (ROOT / "deploy" / ".env.production.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            production[key] = value
    assert production["WB_INPUT_RETENTION_DAYS"] == "45"
    assert production["WB_ARTIFACT_RETENTION_DAYS"] == "45"
    assert production["WB_STORAGE_MIN_FREE_BYTES"] == "32212254720"
    assert production["WB_MAX_PROJECT_INPUT_BYTES"] == "1073741824"
    assert production["WB_MAX_PROJECT_PROGRAM_BYTES"] == "536870912"


def test_finalshell_delivery_builder_is_reproducible_and_contains_no_secrets(tmp_path):
    image = tmp_path / "image.tar"
    image.write_bytes(b"synthetic immutable image archive")
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({
        "release": "v0.6.0-rc.4",
        "git_commit": "825e310fd567023542140471e31a147bd0f45b1d",
        "image": "automation-workbench:0.6.0-rc.4",
        "image_id": "sha256:" + "1" * 64,
        "platform": "linux/amd64",
        "archive_sha256": delivery_builder.digest(image),
    }), encoding="utf-8")
    output = tmp_path / "delivery.tar.gz"
    result = delivery_builder.build(image, manifest, output)
    assert result["sha256"] == delivery_builder.digest(output)
    checksum_path = Path(str(output) + ".sha256")
    assert checksum_path.read_text().startswith(result["sha256"])
    assert checksum_path.read_bytes().endswith(b"\n")
    assert b"\r" not in checksum_path.read_bytes()
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
        sums_name = next(name for name in names if name.endswith("/SHA256SUMS"))
        sums_content = archive.extractfile(sums_name).read()
        assert sums_content.endswith(b"\n")
        assert b"\r" not in sums_content
        assert any(name.endswith("/scripts/install.sh") for name in names)
        assert any(name.endswith("/payload/.env.production") for name in names)
        assert not any("secret" in Path(name).name.lower() for name in names)
        environment_name = next(name for name in names if name.endswith("/payload/.env.production"))
        environment = archive.extractfile(environment_name).read().decode()
        assert "825e310fd567023542140471e31a147bd0f45b1d" in environment
        assert "REPLACE_WITH" not in environment
    with pytest.raises(ValueError, match="拒绝覆盖"):
        delivery_builder.build(image, manifest, output)


def test_nginx_patcher_targets_only_https_server_and_is_idempotent(tmp_path):
    target = tmp_path / "site.conf"
    target.write_text("""
server { listen 80; server_name ai.muyaju.net; return 308 https://$host$request_uri; }
server {
    listen 443 ssl;
    server_name ai.muyaju.net;
    location /data-service/ { proxy_pass http://data-api; }
}
""", encoding="utf-8")
    snippet = ROOT / "deploy" / "server" / "nginx-location.conf"
    backup = tmp_path / "site.conf.bak"
    assert nginx_patcher.patch(target, snippet, backup) == "patched"
    content = target.read_text(encoding="utf-8")
    assert content.count("automation-workbench-managed:start") == 1
    assert content.index("automation-workbench-managed:start") > content.index("listen 443 ssl")
    assert backup.read_text(encoding="utf-8").count("automation-workbench-managed:start") == 0
    assert nginx_patcher.patch(target, snippet, backup) == "unchanged"


def test_server_shell_scripts_have_valid_bash_syntax():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("当前环境没有bash，服务器脚本语法待Linux CI验证")
    probe = subprocess.run([bash, "--version"], capture_output=True, timeout=10)
    if probe.returncode != 0:
        pytest.skip("当前bash入口不可执行，服务器脚本语法待Linux CI验证")
    scripts = sorted((ROOT / "deploy" / "server").glob("*.sh"))
    assert scripts
    for script in scripts:
        subprocess.run([bash, "-n", str(script)], check=True, timeout=10)


def test_dispatch_enable_has_fail_closed_recovery():
    script = (ROOT / "deploy" / "server" / "enable-dispatch.sh").read_text(encoding="utf-8")
    trap_index = script.index("trap rollback_dispatch ERR")
    enable_index = script.index("WB_DISPATCH_ENABLED true")
    assert trap_index < enable_index
    assert "WB_DISPATCH_ENABLED false" in script
    assert "grep -Fxq 'WB_DISPATCH_ENABLED=false'" in script
    assert "compose stop worker" in script
    assert "--force-recreate api worker" in script


def test_nginx_rollback_refuses_changed_shared_config_and_restores_on_invalid_backup():
    install = (ROOT / "deploy" / "server" / "install.sh").read_text(encoding="utf-8")
    rollback = (ROOT / "deploy" / "server" / "rollback-first-install.sh").read_text(
        encoding="utf-8"
    )
    assert "nginx_installed_sha256" in install and "nginx_config_path" in install
    assert "共享Nginx配置在首次安装后已变化，拒绝重写回退基线" in install
    assert 'if [[ "${nginx_state_files}" -eq 0 ]]; then' in install
    digest_check = rollback.index('"${current_digest}" == "${expected_digest}"')
    restore_backup = rollback.index('cp -p "${nginx_backup}" "${nginx_config}"')
    assert digest_check < restore_backup
    assert 'cp -p "${rollback_snapshot}" "${nginx_config}"' in rollback
    assert "Nginx配置在部署后已发生变化，拒绝自动覆盖" in rollback


def test_server_readme_requires_outer_hash_and_declares_side_effect_limit():
    readme = (ROOT / "deploy" / "server" / "README-FIRST.txt").read_text(encoding="utf-8")
    assert "sha256sum -c automation-workbench-server-v0.6.0-rc.4.tar.gz.sha256" in readme
    assert "side_effects=false" in readme


def test_external_database_secret_url_encodes_password(tmp_path):
    directory = tmp_path / "external-private"
    password = "synthetic:/?#[]@!$&'()*+,;=% password"
    bootstrap.initialize(
        directory,
        database_host="shared-postgres",
        database_port=5432,
        database_name="automation_workbench",
        database_user="automation_user",
        database_password=password,
    )
    url = (directory / "database_url").read_text(encoding="utf-8").strip()
    assert "shared-postgres:5432/automation_workbench" in url
    assert password not in url
    assert "%2F" in url and "%40" in url and "%20" in url
    assert (directory / "database_password").read_text(encoding="utf-8").strip() == password


@pytest.mark.parametrize(
    "settings",
    [
        {"database_host": "bad host"},
        {"database_port": 0},
        {"database_name": "bad-name"},
        {"database_user": "9invalid"},
        {"database_password": ""},
        {"database_password": "line\nbreak"},
    ],
)
def test_external_database_secret_rejects_invalid_connection_settings(tmp_path, settings):
    options = {
        "database_host": "shared-postgres",
        "database_port": 5432,
        "database_name": "automation_workbench",
        "database_user": "automation_user",
        "database_password": "synthetic-password",
    }
    options.update(settings)
    with pytest.raises(ValueError):
        bootstrap.initialize(tmp_path / "private", **options)
    assert not (tmp_path / "private").exists()


def test_external_database_password_file_rejects_multiple_lines(tmp_path):
    source = tmp_path / "password"
    source.write_text("first-line\nsecond-line\n", encoding="utf-8")
    with pytest.raises(ValueError, match="只有一行"):
        bootstrap._read_password_file(source)


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


def test_operations_external_database_mode_refuses_before_any_io(monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("外部数据库模式不得构造或执行自带数据库命令")

    monkeypatch.setattr(operations, "command", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "operations.py", "backup", "--project", "isolated-test",
            "--directory", str(tmp_path / "backup"), "--note", "synthetic",
            "--database-mode", "external",
        ],
    )
    with pytest.raises(SystemExit) as error:
        operations.main()
    assert error.value.code == 1
    assert not (tmp_path / "backup").exists()
    assert "操作未完成" in capsys.readouterr().err


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
