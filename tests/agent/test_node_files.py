"""合成临时文件验证授权、分页、清单和只复制重试，不连接真实外部系统。"""

import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager

import httpx
import pytest

from agent.config import AgentError, Config
from agent.node_files import authorized_path, browse_directory, export_artifacts, selected_manifest
from agent.runner import Agent
from agent.filesystem_guard import GuardedDirectory


@pytest.fixture
def folders(tmp_path):
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.txt").write_text("a", encoding="utf-8")
    (inputs / "b.txt").write_text("bb", encoding="utf-8")
    return inputs, outputs


def test_browse_is_sorted_bounded_and_paginated(folders):
    inputs, _ = folders
    first = browse_directory(str(inputs), [str(inputs)], [str(inputs)], 1)
    assert first["entries"][0]["name"] == "a.txt"
    assert first["next_offset"] == 1 and first["truncated"]
    second = browse_directory(str(inputs), [str(inputs)], [str(inputs)], 1, 1)
    assert second["entries"][0]["name"] == "b.txt"
    assert not second["truncated"] and second["next_offset"] is None


@pytest.mark.parametrize("kind", ["ungranted", "outside", "traversal", "missing", "device"])
def test_authorization_refuses_unsafe_paths(folders, kind):
    inputs, outputs = folders
    path = {"ungranted": inputs, "outside": outputs, "traversal": inputs / ".." / "inputs",
            "missing": inputs / "missing", "device": Path("\\\\?\\C:\\Windows")}[kind]
    with pytest.raises((AgentError, OSError)):
        authorized_path(str(path), [str(inputs)], [] if kind == "ungranted" else [str(inputs)], directory=True)


def test_hardlink_and_symlink_not_browsable(folders):
    inputs, outputs = folders
    os.link(inputs / "a.txt", outputs / "hard.txt")
    with pytest.raises(AgentError, match="硬链接"):
        browse_directory(str(inputs), [str(inputs)], [str(inputs)], 10)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction专项")
def test_junction_is_rejected(folders):
    import subprocess
    inputs, outputs = folders
    junction = inputs / "jump"
    completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outputs)], capture_output=True)
    assert completed.returncode == 0
    try:
        with pytest.raises(AgentError, match="重解析"):
            authorized_path(str(junction), [str(inputs)], [str(inputs)], directory=True)
    finally:
        junction.rmdir()


def test_selected_files_digest_and_directory_type(folders):
    inputs, _ = folders
    selected = {"files": [str(inputs / "a.txt"), str(inputs / "b.txt")]}
    rows = selected_manifest(selected, selected, [str(inputs)], [str(inputs)], 100, lambda: None, {"files": "files"})
    assert len(rows) == 2
    assert rows[0]["sha256"] == hashlib.sha256(b"a").hexdigest()
    with pytest.raises(AgentError):
        selected_manifest({"file": str(inputs)}, {"file": str(inputs)}, [str(inputs)], [str(inputs)], 100,
                          lambda: None, {"file": "file"})
    with pytest.raises(AgentError):
        selected_manifest({"dir": str(inputs / "a.txt")}, {"dir": str(inputs / "a.txt")}, [str(inputs)],
                          [str(inputs)], 100, lambda: None, {"dir": "directory"})


@pytest.mark.parametrize("kind", ["size", "mismatch", "missing", "cancel"])
def test_selected_input_rejected_before_execution(folders, kind):
    inputs, _ = folders
    selected = {"source": str(inputs / ("missing.txt" if kind == "missing" else "b.txt"))}
    def progress():
        if kind == "cancel":
            raise AgentError("cancel")
    with pytest.raises((AgentError, OSError)):
        selected_manifest(selected, {} if kind == "mismatch" else selected, [str(inputs)], [str(inputs)],
                          1 if kind == "size" else 100, progress, {"source": "file"})


def test_export_only_declared_files_never_overwrites_and_retry_is_independent(folders):
    inputs, outputs = folders
    rows = export_artifacts(inputs, ["a.txt"], str(outputs), "run-1", [str(outputs)], [str(outputs)],
                            100, lambda: None, lambda row: None)
    assert rows == [{"name": "a.txt", "path": str(outputs / "run-1" / "a.txt")}]
    assert not (outputs / "run-1" / "b.txt").exists()
    with pytest.raises(FileExistsError):
        export_artifacts(inputs, ["a.txt"], str(outputs), "run-1", [str(outputs)], [str(outputs)],
                         100, lambda: None, lambda row: None)
    export_artifacts(inputs, ["a.txt"], str(outputs), "run-1", [str(outputs)], [str(outputs)],
                     100, lambda: None, lambda row: None, 1)
    assert (outputs / "run-1" / "retry-1" / "a.txt").read_bytes() == b"a"


def test_partial_export_failure_preserves_existing_files(folders):
    inputs, outputs = folders
    saved = []
    with pytest.raises(FileNotFoundError):
        export_artifacts(inputs, ["a.txt", "missing.txt"], str(outputs), "run-1", [str(outputs)], [str(outputs)],
                         100, lambda: None, saved.append)
    assert len(saved) == 1
    assert (outputs / "run-1" / "a.txt").read_bytes() == b"a"


def test_disk_failure_is_visible(folders, monkeypatch):
    inputs, outputs = folders
    def fail(_):
        raise OSError("synthetic disk full")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk full"):
        export_artifacts(inputs, ["a.txt"], str(outputs), "run-1", [str(outputs)], [str(outputs)],
                         100, lambda: None, lambda row: None)


@pytest.fixture
def managed(tmp_path, folders):
    inputs, outputs = folders
    token = tmp_path / "token"
    token.write_text("synthetic-token")
    config = Config(base_url="http://127.0.0.1", token_file=token, state_dir=tmp_path / "state",
                    runtimes={"python": sys.executable}, input_roots=[str(inputs)], config_revision=1,
                    config_hash=Agent._configuration_hash(1, [str(inputs)]), poll_seconds=0.05)
    config_path = tmp_path / "agent.json"
    config_path.write_text("{}")
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/file-configuration"):
            return httpx.Response(200, json={"revision": agent.file_service.revision,
                "output_roots": agent.file_service.roots, "config_hash": agent.file_service.config_hash})
        if request.url.path.endswith("/file-requests/claim") or request.url.path.endswith("/file-save-retries/claim"):
            return httpx.Response(204)
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"cancel_run_ids": []})
        return httpx.Response(200, json={"accepted": True})
    agent = Agent(config, config_path=config_path, transport=httpx.MockTransport(handler))
    roots = [str(outputs)]
    config_hash = hashlib.sha256(json.dumps({"revision": 1, "output_roots": roots}, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    agent.file_service._apply({"revision": 1, "output_roots": roots, "config_hash": config_hash})
    options = {"config_revision": 1, "output_config_revision": 1, "allowed_input_roots": [str(inputs)],
               "allowed_output_roots": roots, "output_dir": str(outputs), "inputs": {}, "input_kinds": {}}
    yield agent, options, requests
    agent.close()


def test_export_failure_does_not_change_script_completion_and_recovery_is_idempotent(managed):
    agent, options, requests = managed
    claim = {"run_id": "run-1", "attempt_id": "attempt-1"}
    directory = agent.run_directory(claim)
    (directory / "output").mkdir(parents=True)
    completion = {"result": {"status": "success", "artifacts": [{"name": "missing.txt", "required": True}]}}
    state = {**claim, "phase": "terminal", "completion": completion, "node_files": options}
    agent.file_service.save(directory, state, options)
    assert state["completion"] == completion
    assert state["file_save"]["status"] == "failed"
    agent.file_service.deliver(directory, state)
    agent.file_service.deliver(directory, state)
    assert len([request for request in requests if request.url.path.endswith("/file-save")]) == 1
    (directory / "output" / "missing.txt").write_text("recovered")
    agent.file_service.save(directory, state, options, 1)
    assert state["file_save"]["status"] == "succeeded"
    assert state["file_save"]["files"][0]["path"].endswith("missing.txt")


def test_interrupted_save_is_failed_and_not_rerun(managed):
    agent, options, requests = managed
    directory = agent.root / "synthetic"
    directory.mkdir()
    state = {"run_id": "run-1", "attempt_id": "attempt-1", "completion": {"result": None}, "node_files": options}
    agent.file_service.deliver(directory, state)
    assert state["file_save"]["status"] == "failed"
    assert not list(Path(options["output_dir"]).iterdir())


def test_old_hash_unchanged_and_new_config_rollback_rejected(managed):
    agent, options, _ = managed
    before = agent.config_hash
    service = agent.file_service
    with pytest.raises(AgentError):
        service._apply({"revision": 1, "output_roots": [], "config_hash": "0" * 64})
    assert agent.config_hash == before
    assert service.roots == options["allowed_output_roots"]


def test_browse_service_uses_same_origin_and_strict_command(managed):
    agent, options, _ = managed
    command = {"id": "browse-1", "project_id": "project-1", "purpose": "input", "path": agent.input_roots[0],
               "limit": 1, "offset": 0, "config_revision": 1, "output_config_revision": 1,
               "allowed_roots": agent.input_roots, "expires_at": time.time() + 60}
    completed = []
    def handler(request):
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=command)
        completed.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": True})
    with httpx.Client(base_url=agent.origin, transport=httpx.MockTransport(handler)) as client:
        agent.file_service.browse_once(client)
        assert completed[0]["next_offset"] == 1 and not completed[0]["error"]
        command["path"] = options["output_dir"]
        agent.file_service.browse_once(client)
        assert completed[1]["error"] and not completed[1]["entries"]
        command["extra"] = True
        with pytest.raises(AgentError):
            agent.file_service.browse_once(client)


def test_browse_block_does_not_block_heartbeat(managed, monkeypatch):
    agent, _, requests = managed
    entered, release = threading.Event(), threading.Event()
    def wait(_):
        entered.set()
        release.wait(3)
    monkeypatch.setattr(agent.file_service, "browse_once", wait)
    agent.file_service.available = True
    agent.file_service.start()
    assert entered.wait(2)
    try:
        start = time.monotonic()
        agent.heartbeat(force=True)
        assert time.monotonic() - start < 0.5
        assert any(request.url.path.endswith("/heartbeat") for request in requests)
    finally:
        release.set()


def test_save_retry_claim_never_invokes_business_execution(managed, monkeypatch):
    agent, options, requests = managed
    claim = {"run_id": "run-1", "attempt_id": "attempt-1", "retry_number": 1, "node_files": options}
    directory = agent.run_directory(claim)
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "result.txt").write_text("synthetic", encoding="utf-8")
    state = {"run_id": "run-1", "attempt_id": "attempt-1", "phase": "terminal",
             "completion": {"result": {"status": "success", "artifacts": [{"name": "result.txt"}]}},
             "file_save": {"retry_number": 0, "status": "failed"}}
    agent._save(directory, state)
    original = agent._post
    def post(path, payload):
        if path.endswith("/file-save-retries/claim"):
            return httpx.Response(200, json=claim)
        return original(path, payload)
    def forbidden(*args):
        raise AssertionError("must never execute business script on save retry")
    monkeypatch.setattr(agent, "_post", post)
    monkeypatch.setattr(agent, "execute", forbidden)
    agent.file_service.available = True
    agent.file_service.retry_once()
    agent.file_service.retry_once()
    assert (Path(options["output_dir"]) / "run-1" / "retry-1" / "result.txt").read_text() == "synthetic"
    assert len([request for request in requests if request.url.path.endswith("/file-save")]) == 1


def test_export_cancel_is_independent_failed_state(managed):
    agent, options, _ = managed
    claim = {"run_id": "run-1", "attempt_id": "attempt-1"}
    directory = agent.run_directory(claim)
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "result.txt").write_text("synthetic")
    state = {**claim, "completion": {"result": {"status": "success", "artifacts": [{"name": "result.txt"}]}}}
    # 保持心跳节流，模拟已领取的取消指令。
    agent.last_heartbeat = time.monotonic()
    agent.cancel_ids.add("run-1")
    agent.file_service.save(directory, state, options)
    assert state["file_save"]["status"] == "failed"
    assert state["completion"]["result"]["status"] == "success"


def test_revoked_output_revision_never_creates_directory(managed):
    agent, options, _ = managed
    options["output_config_revision"] = 0
    directory = agent.root / "synthetic"
    directory.mkdir()
    state = {"run_id": "run-1", "attempt_id": "attempt-1", "completion": {"result": {"status": "success"}}}
    agent.file_service.save(directory, state, options)
    assert state["file_save"]["status"] == "failed"
    assert not list(Path(options["output_dir"]).iterdir())


def test_save_receipt_network_failure_does_not_lose_business_completion(managed, monkeypatch):
    agent, options, _ = managed
    directory = agent.root / "synthetic"
    directory.mkdir()
    state = {"run_id": "run-1", "attempt_id": "attempt-1", "completion": {"result": {"status": "success"}},
             "file_save": {"status": "failed", "retry_number": 0}, "node_files": options}
    def fail(*args):
        raise httpx.ReadTimeout("synthetic response lost")
    monkeypatch.setattr(agent, "_post", fail)
    agent.file_service.deliver(directory, state)
    assert not state.get("file_save_delivered")
    assert state["completion"]["result"]["status"] == "success"


def test_save_rechecks_permission_changed_during_script_execution(managed, monkeypatch):
    agent, options, _ = managed
    directory = agent.root / "synthetic"
    (directory / "output").mkdir(parents=True)
    (directory / "output" / "result.txt").write_text("synthetic")
    state = {"run_id": "run-1", "attempt_id": "attempt-1",
             "completion": {"result": {"status": "success", "artifacts": [{"name": "result.txt"}]}}}
    digest = hashlib.sha256(json.dumps({"revision": 2, "output_roots": []}, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    original = agent.client.get
    def changed(url, **kwargs):
        if url.endswith("/file-configuration"):
            return httpx.Response(200, request=httpx.Request("GET", agent.origin + url),
                                  json={"revision": 2, "output_roots": [], "config_hash": digest})
        return original(url, **kwargs)
    monkeypatch.setattr(agent.client, "get", changed)
    agent.file_service.save(directory, state, options)
    assert agent.file_service.revision == 2
    assert state["file_save"]["status"] == "failed"
    assert state["completion"]["result"]["status"] == "success"
    assert not list(Path(options["output_dir"]).iterdir())


@pytest.mark.parametrize("replace_kind", ["target", "ancestor", "source"])
def test_export_replacement_race_cannot_write_outside(folders, tmp_path, monkeypatch, replace_kind):
    import subprocess
    inputs, outputs = folders
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not authorized")
    original_open = GuardedDirectory.open_file
    observed = {"attempted": False, "blocked": False, "replaced": None}

    @contextmanager
    def injected(guard, name, *, write=False):
        if not write and not observed["attempted"]:
            observed["attempted"] = True
            target = {"target": outputs / "run-race", "ancestor": outputs, "source": inputs}[replace_kind]
            moved = tmp_path / ("moved-" + replace_kind)
            try:
                target.rename(moved)
            except PermissionError:
                observed["blocked"] = True
            else:
                observed["replaced"] = target
                if os.name == "nt":
                    assert subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(outside)], capture_output=True).returncode == 0
                else:
                    target.symlink_to(outside, target_is_directory=True)
        with original_open(guard, name, write=write) as stream:
            yield stream

    monkeypatch.setattr(GuardedDirectory, "open_file", injected)
    try:
        try:
            export_artifacts(inputs, ["a.txt"], str(outputs), "run-race", [str(outputs)], [str(outputs)],
                             100, lambda: None, lambda row: None)
        except (AgentError, OSError):
            pass
        assert observed["attempted"]
        assert not (outside / "a.txt").exists()
        if os.name == "nt":
            assert observed["blocked"], "Windows目录句柄必须阻止被重命名或换为junction"
    finally:
        if observed["replaced"]:
            target = observed["replaced"]
            target.rmdir() if os.name == "nt" else target.unlink()


def test_selected_file_parent_replacement_cannot_read_outside(folders, tmp_path, monkeypatch):
    inputs, _ = folders
    outside = tmp_path / "outside-read"
    outside.mkdir()
    (outside / "a.txt").write_text("outside-secret")
    original_open = GuardedDirectory.open_file
    observations = []

    @contextmanager
    def injected(guard, name, *, write=False):
        if guard.path == inputs and not observations:
            try:
                inputs.rename(tmp_path / "old-inputs")
            except PermissionError:
                observations.append("blocked")
            else:
                observations.append("moved")
                inputs.symlink_to(outside, target_is_directory=True)
        with original_open(guard, name, write=write) as stream:
            yield stream

    monkeypatch.setattr(GuardedDirectory, "open_file", injected)
    selected = {"source": str(inputs / "a.txt")}
    try:
        try:
            rows = selected_manifest(selected, selected, [str(inputs)], [str(inputs)], 100, lambda: None,
                                     {"source": "file"})
        except (AgentError, OSError):
            rows = []
        assert observations
        assert all(row["sha256"] != hashlib.sha256(b"outside-secret").hexdigest() for row in rows)
        if os.name == "nt":
            assert observations == ["blocked"]
    finally:
        if observations == ["moved"]:
            inputs.unlink()
