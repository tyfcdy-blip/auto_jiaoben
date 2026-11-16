import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import zipfile

import httpx
import pytest

from agent.config import AgentError, Config
from agent.runner import Agent
from agent.storage import StateLock, atomic_json, extract_bundle, safe_name

ROOT = Path(__file__).resolve().parents[2]
PRELUDE = """import argparse, json, os, pathlib, sys, time
p = argparse.ArgumentParser()
p.add_argument('--context-file')
c = json.loads(pathlib.Path(p.parse_args().context_file).read_text(encoding='utf-8'))
out = pathlib.Path(c['output_dir'])
"""
RESULT = """r = {'protocol_version':'1', 'run_id':c['run_id'], 'attempt_id':c['attempt_id'], 'status':'success', 'summary':'成功'}
(out/'result.json').write_text(json.dumps(r), encoding='utf-8')
"""


class Control:
    def __init__(self, source: str, prefix: str = ""):
        self.prefix = prefix.rstrip("/")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("main.py", source)
        self.bundle = archive.getvalue()
        self.claim = {"run_id": "run-1", "attempt_id": "attempt-1", "protocol_version": "1",
                      "command": ["python", "main.py"], "bundle_sha256": hashlib.sha256(self.bundle).hexdigest(),
                      "bundle_url": self.prefix + "/api/v1/bundle", "parameters": {"greeting": "参数可达"}, "operation": "run",
                      "timeout_seconds": 10, "side_effects": False, "result_required": True,
                      "input_files": [], "resource_keys": []}
        self.requests = []
        self.completions = []
        self.logs = []
        self.artifacts = {}
        self.cancel = False
        self.fail_complete = False
        self.fail_started = False
        self.claimed = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("authorization") == "Bearer test-node-token"
        path = request.url.path
        assert path.startswith(self.prefix + "/api/v1/")
        path = path[len(self.prefix):]
        if path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancel_run_ids": []})
        if path.endswith("/claim"):
            if self.claimed:
                return httpx.Response(204)
            self.claimed = True
            return httpx.Response(200, json=self.claim)
        if path == "/api/v1/bundle":
            return httpx.Response(200, content=self.bundle)
        if path == "/api/v1/input":
            return httpx.Response(200, content=b"input-content")
        if path.endswith("/control"):
            return httpx.Response(200, json={"cancel_requested": self.cancel})
        if path.endswith("/started"):
            if self.fail_started:
                raise httpx.ReadTimeout("lost started reply", request=request)
            return httpx.Response(200, json={})
        if path.endswith("/logs"):
            self.logs.extend(json.loads(request.content)["entries"])
            return httpx.Response(200, json={})
        if "/artifacts/" in path:
            self.artifacts[path.rsplit("/", 1)[-1]] = request.content
            return httpx.Response(200, json={})
        if path.endswith("/complete"):
            self.completions.append(json.loads(request.content))
            if self.fail_complete:
                raise httpx.ReadTimeout("lost complete reply", request=request)
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def agent_factory(tmp_path):
    agents = []
    def factory(source=PRELUDE + RESULT, **overrides):
        token = tmp_path / "token.txt"
        token.write_text("test-node-token", encoding="utf-8")
        base_url = overrides.pop("base_url", "http://127.0.0.1")
        config = Config(base_url=base_url, token_file=token, state_dir=tmp_path / "state",
                        runtimes={"python": sys.executable}, poll_seconds=0.05, heartbeat_seconds=0.1,
                        **overrides)
        control = Control(source, urlsplit(base_url).path)
        agent = Agent(config, transport=httpx.MockTransport(control.handle))
        agents.append(agent)
        return agent, control
    yield factory
    for agent in agents:
        agent.close()


def test_real_success_input_artifact_and_parameter(agent_factory):
    source = (ROOT / "examples" / "safe-job" / "main.py").read_text(encoding="utf-8")
    agent, control = agent_factory(source)
    control.claim["input_files"] = [{"name": "input.txt", "sha256": hashlib.sha256(b"input-content").hexdigest(), "url": "/api/v1/input"}]
    assert agent.run_once()
    result = control.completions[-1]
    assert result["exit_code"] == 0
    assert result["result"]["status"] == "success"
    assert control.artifacts["summary.txt"].decode() == "参数可达"
    assert result["artifact_state"] == "complete"
    assert control.logs
    assert not agent.run_once()


def test_nonzero_exit_and_stderr_are_captured(agent_factory):
    agent, control = agent_factory(PRELUDE + "print('真实错误', file=sys.stderr)\nsys.exit(7)\n")
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 7
    assert any(entry["stream"] == "stderr" and "真实错误" in entry["text"] for entry in control.logs)


def test_no_token_or_parent_secret_in_child_environment(agent_factory, monkeypatch):
    monkeypatch.setenv("PARENT_TEST_SECRET", "do-not-inherit")
    source = PRELUDE + "assert 'PARENT_TEST_SECRET' not in os.environ\nassert not any('test-node-token' in v for v in os.environ.values())\n" + RESULT
    agent, control = agent_factory(source)
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    context = next(agent.root.glob("*/context.json")).read_text(encoding="utf-8")
    assert "test-node-token" not in context


@pytest.mark.parametrize("source,reason", [
    (PRELUDE + "print('no result')\n", "必要"),
    (PRELUDE + "(out/'result.json').write_text('{}')\n", "协议"),
    (PRELUDE + RESULT + "r['run_id']='another'\n(out/'result.json').write_text(json.dumps(r))\n", "当前运行"),
    (PRELUDE + RESULT + "r['artifacts']=[{'name':'missing.txt','required':True}]\n(out/'result.json').write_text(json.dumps(r))\n", "必要产物"),
    (PRELUDE + RESULT + "r['artifacts']=[{'name':'../token.txt','required':False}]\n(out/'result.json').write_text(json.dumps(r))\n", "越界"),
])
def test_invalid_or_missing_result_not_reported_as_success(agent_factory, source, reason):
    agent, control = agent_factory(source)
    agent.run_once()
    complete = control.completions[-1]
    assert complete["result"] is None
    assert reason in complete["error"]


def test_optional_result_can_be_absent(agent_factory):
    agent, control = agent_factory("print('done')")
    control.claim["result_required"] = False
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    assert control.completions[-1]["error"] is None


@pytest.mark.parametrize("canceled", [False, True])
def test_timeout_and_cancel_terminate_grandchild(agent_factory, canceled):
    source = PRELUDE + "import subprocess\nsubprocess.Popen([sys.executable, '-c', \"import time,pathlib; time.sleep(2.5); pathlib.Path(\"+repr(str(out/'escaped.txt'))+\").write_text('escaped')\"])\nprint('tree started', flush=True)\ntime.sleep(30)\n"
    agent, control = agent_factory(source)
    control.claim["timeout_seconds"] = 1
    if canceled:
        original = control.handle
        start = time.monotonic()
        def delayed(request):
            if time.monotonic() - start > 0.5:
                control.cancel = True
            return original(request)
        agent.client.close()
        agent.client = httpx.Client(base_url=agent.config.base_url, headers={"Authorization": "Bearer test-node-token"}, transport=httpx.MockTransport(delayed))
    agent.run_once()
    assert control.completions[-1]["observation"] == ("canceled" if canceled else "timeout")
    time.sleep(2.5)
    assert not list(agent.root.glob("*/output/escaped.txt"))


def test_log_flood_is_bounded_without_deadlock(agent_factory):
    source = PRELUDE + "for i in range(5000):\n print('x'*1000)\n print('y'*1000, file=sys.stderr)\n" + RESULT
    agent, control = agent_factory(source, max_log_bytes=4096)
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    assert next(agent.root.glob("*/logs.jsonl")).stat().st_size < 15000
    assert any("截断" in entry["text"] for entry in control.logs)


def test_early_parent_exit_with_live_child_is_not_silent_success(agent_factory):
    source = PRELUDE + "import subprocess\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n" + RESULT
    agent, control = agent_factory(source)
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    assert "活动子进程" in control.completions[-1]["error"]


def test_lost_complete_reply_retransmits_without_reexecution(agent_factory):
    source = PRELUDE + "with (out/'count.txt').open('a') as h: h.write('once')\n" + RESULT
    agent, control = agent_factory(source)
    control.fail_complete = True
    with pytest.raises(httpx.ReadTimeout):
        agent.run_once()
    original = control.completions[-1]
    control.fail_complete = False
    agent.run_once()
    assert control.completions[-1] == original
    assert next(agent.root.glob("*/output/count.txt")).read_text() == "once"


def test_unknown_start_intent_is_never_restarted(agent_factory):
    agent, control = agent_factory()
    directory = agent.run_directory(control.claim)
    directory.mkdir()
    atomic_json(directory / "state.json", {"run_id": "run-1", "attempt_id": "attempt-1", "phase": "start_intent", "sequence": 0, "ack_sequence": 0})
    agent.run_once()
    assert control.completions[-1]["observation"] == "unknown"
    assert not any(request.url.path.endswith("/started") for request in control.requests)


def test_started_response_loss_does_not_unlock_business_process(agent_factory):
    agent, control = agent_factory(PRELUDE + "(out/'should-not-run.txt').write_text('bad')\n" + RESULT)
    control.fail_started = True
    agent.run_once()
    assert control.completions[-1]["observation"] == "unknown"
    assert not list(agent.root.glob("*/output/should-not-run.txt"))


def test_side_effects_and_bad_hash_rejected_before_start(agent_factory):
    agent, control = agent_factory()
    control.claim["side_effects"] = True
    agent.run_once()
    assert control.completions[-1]["observation"] == "startup_failed"
    assert not any(request.url.path.endswith("/started") for request in control.requests)


def test_digest_mismatch(agent_factory):
    agent, control = agent_factory()
    control.claim["bundle_sha256"] = "0" * 64
    agent.run_once()
    assert "SHA256" in control.completions[-1]["error"]


def test_corrupt_archive_reports_startup_failure(agent_factory):
    agent, control = agent_factory()
    control.bundle = b"not-a-zip"
    control.claim["bundle_sha256"] = hashlib.sha256(control.bundle).hexdigest()
    agent.run_once()
    assert control.completions[-1]["observation"] == "startup_failed"


def test_oversized_result_report_is_not_accepted(agent_factory):
    source = PRELUDE + RESULT + "r['metrics']={'oversized':'x'*65000}\n(out/'result.json').write_text(json.dumps(r))\n"
    agent, control = agent_factory(source)
    agent.run_once()
    assert control.completions[-1]["result"] is None
    assert "有界" in control.completions[-1]["error"]


@pytest.mark.parametrize("url", ["https://other.invalid/api/v1/bundle", "//other.invalid/api/v1/bundle", "/private/bundle"])
def test_download_origin_restricted(agent_factory, tmp_path, url):
    agent, control = agent_factory()
    with pytest.raises(AgentError):
        agent.download(url, tmp_path / "download", "0" * 64)
    assert not control.requests


@pytest.mark.parametrize("name", ["../bad", "/bad", "C:/bad", "x\\bad", "CON.txt", "name.", "a:b", "a//b"])
def test_unsafe_paths_rejected(name):
    with pytest.raises(AgentError):
        safe_name(name, nested=True)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "collision", "expanded"])
def test_zip_rejects_dangerous_contents(tmp_path, kind):
    archive = tmp_path / "bundle.zip"
    destination = tmp_path / "package"
    destination.mkdir()
    with zipfile.ZipFile(archive, "w") as bundle:
        if kind == "traversal":
            bundle.writestr("../escape", "x")
        elif kind == "symlink":
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            bundle.writestr(info, "../secret")
        elif kind == "collision":
            bundle.writestr("A.py", "x")
            bundle.writestr("a.py", "x")
        else:
            bundle.writestr("big", "x" * 2000)
    with pytest.raises(AgentError):
        extract_bundle(archive, destination, 1024)
    assert not list(destination.iterdir())


def test_state_lock_rejects_second_agent(tmp_path):
    with StateLock(tmp_path / "lock"):
        with pytest.raises(AgentError):
            with StateLock(tmp_path / "lock"):
                pass


def test_production_isolation_rejected(agent_factory):
    with pytest.raises(AgentError, match="生产"):
        agent_factory(isolation="os_user")


def test_cli_help_and_invalid_config(tmp_path):
    help_result = subprocess.run([sys.executable, "-m", "agent", "--help"], cwd=ROOT, capture_output=True)
    assert help_result.returncode == 0
    config = tmp_path / "invalid.json"
    config.write_text('{"unexpected": true}')
    result = subprocess.run([sys.executable, "-m", "agent", "--config", str(config), "--once"], cwd=ROOT, capture_output=True)
    assert result.returncode == 2


@pytest.mark.parametrize("prefix", ["", "/automation", "/nested/workbench/"])
@pytest.mark.parametrize("invalid_proxy", [False, True])
def test_cli_executes_against_real_local_http_server(tmp_path, prefix, invalid_proxy, monkeypatch):
    if invalid_proxy:
        # 即使用户设置了不可达代理且未配置本机例外，回环执行仍必须直连。
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
    control = Control(PRELUDE + RESULT, prefix)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.dispatch()

        def do_GET(self):
            self.dispatch()

        def log_message(self, *args):
            pass

        def dispatch(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            response = control.handle(httpx.Request(self.command, "http://localhost" + self.path,
                                                   headers=dict(self.headers), content=payload))
            self.send_response(response.status_code)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / "token.txt"
    token.write_text("test-node-token")
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}" + prefix,
                                         "token_file": str(token), "state_dir": str(tmp_path / "runtime"),
                                         "runtimes": {"python": sys.executable}}), encoding="utf-8")
    process_environment = os.environ.copy()
    process_environment["USERPROFILE"] = str(tmp_path / "synthetic-profile")
    process_environment["HOME"] = str(tmp_path / "synthetic-profile")
    try:
        completed = subprocess.run([sys.executable, "-m", "agent", "--config", str(configuration), "--once"],
                                   cwd=ROOT, capture_output=True, timeout=20, env=process_environment)
        assert completed.returncode == 0, completed.stderr
        assert control.completions[-1]["result"]["status"] == "success"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_same_attempt_repeated_claim_does_not_execute_twice(agent_factory):
    agent, control = agent_factory(PRELUDE + "with (out/'count.txt').open('a') as h: h.write('once')\n" + RESULT)
    agent.run_once()
    control.claimed = False
    agent.run_once()
    assert next(agent.root.glob("*/output/count.txt")).read_text() == "once"
    assert len([request for request in control.requests if request.url.path.endswith("/started")]) == 1


def test_optional_artifact_failure_does_not_hide_business_terminal(agent_factory):
    source = PRELUDE + RESULT + "r['artifacts']=[{'name':'missing.txt','required':False}]\n(out/'result.json').write_text(json.dumps(r))\n"
    agent, control = agent_factory(source)
    agent.run_once()
    assert control.completions[-1]["result"]["status"] == "success"
    assert control.completions[-1]["artifact_state"] == "incomplete"


def test_log_endpoint_failure_does_not_hide_terminal(agent_factory):
    agent, control = agent_factory(PRELUDE + "print('hello')\n" + RESULT)
    original = control.handle
    def unavailable_logs(request):
        if request.url.path.endswith("/logs"):
            return httpx.Response(503)
        return original(request)
    agent.client.close()
    agent.client = httpx.Client(base_url=agent.config.base_url, headers={"Authorization": "Bearer test-node-token"}, transport=httpx.MockTransport(unavailable_logs))
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    state = json.loads(next(agent.root.glob("*/state.json")).read_text(encoding="utf-8"))
    assert state["delivered"] is True and state["ack_sequence"] == 0


def test_unknown_runtime_rejected(agent_factory):
    agent, control = agent_factory()
    control.claim["command"][0] = "unknown-runtime"
    agent.run_once()
    assert control.completions[-1]["observation"] == "startup_failed"
    assert not any(request.url.path.endswith("/started") for request in control.requests)


def test_claim_directory_has_unambiguous_bounded_identity(agent_factory):
    agent, _ = agent_factory()
    assert agent.run_directory({"run_id": "a_b", "attempt_id": "c"}) != agent.run_directory({"run_id": "a", "attempt_id": "b_c"})


def test_prefixed_inputs_logs_and_artifact_endpoints(agent_factory):
    source = (ROOT / "examples" / "safe-job" / "main.py").read_text(encoding="utf-8")
    agent, control = agent_factory(source, base_url="http://127.0.0.1/automation/")
    control.claim["input_files"] = [{"name": "input.txt", "sha256": hashlib.sha256(b"input-content").hexdigest(), "url": "/automation/api/v1/input"}]
    agent.run_once()
    assert control.completions[-1]["exit_code"] == 0
    assert control.artifacts["summary.txt"].decode() == "参数可达"
    assert control.logs
    assert all(request.url.path.startswith("/automation/api/v1/") for request in control.requests)


@pytest.mark.parametrize("url", [
    "/api/v1/bundle", "/other/api/v1/bundle", "/automation-evil/api/v1/bundle",
    "/automation/api/v1/../../outside", "/automation/api/v1/%2e%2e/outside",
    "/automation/api/v1/%252e%252e/outside", "/automation/api/v1/inner%2foutside",
    "/automation/api/v1//bundle", "/automation/api/v1/bundle#fragment",
    "http://127.0.0.1/automation/api/v1/bundle", "//evil.invalid/automation/api/v1/bundle"
])
def test_prefixed_download_cannot_escape_application(agent_factory, tmp_path, url):
    agent, control = agent_factory(base_url="http://127.0.0.1/automation")
    with pytest.raises(AgentError):
        agent.download(url, tmp_path / "download", "0" * 64)
    assert not control.requests


@pytest.mark.parametrize("prefix", ["/a/../b", "/a//b", "/a/%2e%2e/b", "/a\\b", "/a b", "/a//"])
def test_invalid_application_prefix_rejected(agent_factory, prefix):
    with pytest.raises(AgentError):
        agent_factory(base_url="http://127.0.0.1" + prefix)
