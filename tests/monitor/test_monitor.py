import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from workbench.monitor import load_config, main, run_once, state_lock


def configuration(tmp_path, **extra):
    (tmp_path / "token.txt").write_text("synthetic-monitor-token", encoding="utf-8")
    raw = {"target_base_url": "http://127.0.0.1:8888/workbench", "monitor_token_file": "token.txt",
           "state_file": "state.json", "interval_seconds": 1, **extra}
    path = tmp_path / "monitor.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def fake_client(health, posts=None, delivery=None):
    def respond(request):
        if request.method == "POST":
            posts.append(request)
            return delivery(request) if delivery else httpx.Response(200, json={"errcode": 0})
        assert request.headers["authorization"] == "Bearer synthetic-monitor-token"
        assert request.url.path.startswith("/workbench/api/v1/health/")
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if callable(health[endpoint]):
            return health[endpoint](request)
        if health[endpoint] != 200:
            return httpx.Response(health[endpoint], text="private response must never be logged")
        return httpx.Response(200, json={"status": "ready" if endpoint == "ready" else "healthy"})
    return httpx.Client(transport=httpx.MockTransport(respond))


def test_first_fault_restart_dedup_recovery_and_new_incident(tmp_path, capsys):
    config = load_config(configuration(tmp_path))
    health = {"ready": 200, "components": 200}
    with fake_client(health) as client:
        assert run_once(config, client, 1) == 0
        assert capsys.readouterr().out == ""
        health["components"] = 503
        assert run_once(config, client, 2) == 1
        fault = json.loads(capsys.readouterr().out)
        assert fault["kind"] == "fault" and fault["reason"] == "components:HTTP_503"
        assert run_once(load_config(tmp_path / "monitor.json"), client, 3) == 1
        assert capsys.readouterr().out == ""
        health["components"] = 200
        assert run_once(config, client, 4) == 0
        recovery = json.loads(capsys.readouterr().out)
        assert recovery["kind"] == "recovery" and recovery["event_id"] != fault["event_id"]
        assert run_once(config, client, 5) == 0
        assert capsys.readouterr().out == ""
        health["ready"] = 503
        assert run_once(config, client, 6) == 1
        assert json.loads(capsys.readouterr().out)["event_id"] != fault["event_id"]


@pytest.mark.parametrize("failure,reason", [
    (lambda request: httpx.Response(401), "HTTP_401"),
    (lambda request: httpx.Response(302, headers={"location": "https://example.invalid"}), "HTTP_302"),
    (lambda request: httpx.Response(200, text="broken"), "invalid_response"),
    (lambda request: httpx.Response(200, json={"status": "stale"}), "invalid_response"),
    (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("secret-url")), "timeout"),
    (lambda request: (_ for _ in ()).throw(httpx.ConnectError("secret-url")), "unreachable"),
])
def test_fault_types_do_not_leak_response_or_exception(tmp_path, capsys, failure, reason):
    config = load_config(configuration(tmp_path))
    with fake_client({"ready": failure, "components": 200}) as client:
        assert run_once(config, client, 1) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["reason"] == "ready:" + reason
    assert "secret-url" not in captured.out + captured.err


def notification_config(tmp_path, **extra):
    (tmp_path / "webhook.txt").write_text("https://oapi.dingtalk.com/robot/send?access_token=fake", encoding="utf-8")
    (tmp_path / "sign.txt").write_text("fake-signing-secret", encoding="utf-8")
    return load_config(configuration(tmp_path, webhook_file="webhook.txt", signing_secret_file="sign.txt", **extra))


def test_delivery_budget_persisted_before_send_no_recursion_and_recovery(tmp_path, capsys):
    config = notification_config(tmp_path, notification_budget=2)
    posts = []
    health = {"ready": 503, "components": 503}
    def delivery(request):
        state = json.loads(config.state_file.read_text(encoding="utf-8"))
        assert state["event"]["attempts"] == len(posts)
        raise httpx.ReadTimeout("private-webhook-token")
    with fake_client(health, posts, delivery) as client:
        assert run_once(config, client, 1) == 1
        assert run_once(config, client, 1.1) == 1
        assert len(posts) == 1
        assert run_once(load_config(tmp_path / "monitor.json"), client, 2) == 1
        assert run_once(config, client, 3) == 1
        assert len(posts) == 2
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["event"]["attempts"] == 2 and not state["event"]["delivered"]
    content = [json.loads(request.content)["text"]["content"] for request in posts]
    assert content[0] == content[1]
    query = parse_qs(urlsplit(str(posts[0].url)).query)
    assert query["timestamp"] == ["1000"] and query["sign"]
    captured = capsys.readouterr()
    assert "monitor_notification_exhausted" in captured.err and captured.out == ""
    assert "private-webhook-token" not in captured.err and "fake-signing-secret" not in config.state_file.read_text()
    health.update(ready=200, components=200)
    with fake_client(health, posts) as client:
        assert run_once(config, client, 4) == 0
        assert run_once(config, client, 5) == 0
    assert len(posts) == 3
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["event"]["kind"] == "recovery" and state["event"]["delivered"]


@pytest.mark.parametrize("response", [httpx.Response(429), httpx.Response(200, json={"errcode": 310000}),
                                      httpx.Response(200, text="bad-json"),
                                      httpx.Response(200, json={"errcode": False})])
def test_notification_non_success_is_never_marked_delivered(tmp_path, response):
    config = notification_config(tmp_path)
    with fake_client({"ready": 503, "components": 200}, [], lambda request: response) as client:
        assert run_once(config, client, 1) == 1
    assert not json.loads(config.state_file.read_text())["event"]["delivered"]


def test_recovery_supersedes_pending_fault(tmp_path):
    config = notification_config(tmp_path)
    posts = []
    health = {"ready": 503, "components": 200}
    with fake_client(health, posts, lambda request: httpx.Response(500)) as client:
        run_once(config, client, 1)
    health["ready"] = 200
    with fake_client(health, posts) as client:
        run_once(config, client, 2)
        run_once(config, client, 3)
    assert len(posts) == 2
    assert "恢复" in json.loads(posts[-1].content)["text"]["content"]


def test_crash_after_budget_reservation_does_not_reset_attempts(tmp_path, capsys):
    config = notification_config(tmp_path, notification_budget=1)
    posts = []
    def terminated(request):
        raise KeyboardInterrupt
    with fake_client({"ready": 503, "components": 200}, posts, terminated) as client:
        with pytest.raises(KeyboardInterrupt):
            run_once(config, client, 1)
    with fake_client({"ready": 503, "components": 200}, posts) as client:
        assert run_once(load_config(tmp_path / "monitor.json"), client, 2) == 1
    assert len(posts) == 1
    assert json.loads(config.state_file.read_text())["event"]["attempts"] == 1
    assert "monitor_notification_exhausted" in capsys.readouterr().err


@pytest.mark.parametrize("changes", [
    {"target_base_url": "http://example.com"}, {"target_base_url": "https://user:pass@example.com"},
    {"target_base_url": "https://example.com?secret=x"}, {"target_base_url": "https://example.com:bad"},
    {"notification_budget": True}, {"notification_budget": 11}, {"timeout_seconds": 0},
    {"interval_seconds": float("nan")}, {"state_file": "token.txt"}, {"unexpected": 1},
])
def test_configuration_rejects_insecure_or_invalid_values(tmp_path, changes):
    path = configuration(tmp_path, **changes)
    with pytest.raises(ValueError):
        load_config(path)


def test_invalid_notification_channel_and_unsigned_stdout_configuration(tmp_path):
    (tmp_path / "webhook.txt").write_text("http://localhost/collect", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(configuration(tmp_path, webhook_file="webhook.txt"))
    (tmp_path / "sign.txt").write_text("fake", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(configuration(tmp_path, signing_secret_file="sign.txt"))


def test_corrupt_state_and_changed_target_do_not_reset_budget(tmp_path):
    config = load_config(configuration(tmp_path))
    config.state_file.write_text("not-json", encoding="utf-8")
    with fake_client({"ready": 200, "components": 200}) as client, pytest.raises(ValueError):
        run_once(config, client)
    config.state_file.write_text(json.dumps({"version": 1, "healthy": True, "target_key": "other"}))
    with fake_client({"ready": 200, "components": 200}) as client, pytest.raises(ValueError):
        run_once(config, client)


def test_overlapping_invocations_rejected(tmp_path):
    config = load_config(configuration(tmp_path))
    with state_lock(config.state_file), fake_client({"ready": 200, "components": 200}) as client:
        with pytest.raises(OSError):
            run_once(config, client)


def test_cli_local_error_is_sanitized(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "private-path.json"), "--once"]) == 2
    assert "private-path" not in capsys.readouterr().err


def test_real_local_http_and_cli_without_database(tmp_path):
    class HealthServer(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer synthetic-monitor-token"
            assert self.path.startswith("/workbench/api/v1/health/")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ready" if self.path.endswith("ready") else "healthy"}).encode())

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = configuration(tmp_path, target_base_url=f"http://127.0.0.1:{server.server_port}/workbench")
    command = [sys.executable, "-m", "workbench.monitor", "--config", str(path), "--once"]
    import os
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "backend"),
                   "WB_DATABASE_URL": "postgresql://invalid:invalid@127.0.0.1:1/unavailable"}
    try:
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / "state.json").read_text())["healthy"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["kind"] == "fault"
    assert "unreachable" in json.loads(result.stdout)["reason"]
