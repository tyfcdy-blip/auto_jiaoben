import hashlib
import json
import os
import sys

import httpx
import pytest

from agent.config import Config
from agent.runner import Agent


def digest(revision: int, roots: list[str]) -> str:
    encoded = json.dumps({"revision": revision, "input_roots": roots}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def managed_agent(tmp_path, response: dict, *, current_revision: int = 1):
    token = tmp_path / "token.txt"
    token.write_text("synthetic-node-token", encoding="utf-8")
    roots = [str(tmp_path / "old-offline-root")]
    current_hash = digest(current_revision, roots)
    payload = {
        "base_url": "http://127.0.0.1", "token_file": str(token),
        "state_dir": str(tmp_path / "state"), "runtimes": {"python": sys.executable},
        "capabilities": ["python"], "input_roots": roots,
        "config_revision": current_revision, "config_hash": current_hash,
    }
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    acknowledgements = []

    def handler(request: httpx.Request):
        if request.method == "GET" and request.url.path.endswith("/configuration"):
            return httpx.Response(200, json=response)
        if request.method == "POST" and request.url.path.endswith("/configuration/ack"):
            acknowledgements.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = Config.load(path)
    return Agent(config, config_path=path, transport=httpx.MockTransport(handler)), path, acknowledgements


@pytest.mark.skipif(os.name != "nt", reason="验证 Windows 路径斜杠摘要一致性")
def test_managed_agent_atomically_applies_offline_share_configuration(tmp_path):
    roots = ["D:/SyntheticInputs"]
    response = {"revision": 2, "input_roots": roots, "config_hash": digest(2, roots)}
    agent, path, acknowledgements = managed_agent(tmp_path, response)
    try:
        assert agent.sync_configuration()
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["input_roots"] == roots
        assert saved["config_revision"] == 2
        assert acknowledgements == [{"revision": 2, "config_hash": response["config_hash"],
                                     "status": "applied", "error": ""}]
    finally:
        agent.close()


def test_managed_agent_rejects_hash_mismatch_without_overwriting_local_config(tmp_path):
    roots = [str(tmp_path / "new-root")]
    response = {"revision": 2, "input_roots": roots, "config_hash": "0" * 64}
    agent, path, acknowledgements = managed_agent(tmp_path, response)
    before = path.read_bytes()
    try:
        assert agent.sync_configuration() is False
        assert path.read_bytes() == before
        assert acknowledgements[0]["status"] == "failed"
        assert "摘要" in acknowledgements[0]["error"]
    finally:
        agent.close()


def test_managed_agent_rejects_revision_rollback(tmp_path):
    roots = [str(tmp_path / "rollback-root")]
    response = {"revision": 1, "input_roots": roots, "config_hash": digest(1, roots)}
    agent, path, acknowledgements = managed_agent(tmp_path, response, current_revision=2)
    before = path.read_bytes()
    try:
        assert agent.sync_configuration() is False
        assert path.read_bytes() == before
        assert acknowledgements[0]["status"] == "failed"
        assert "回退" in acknowledgements[0]["error"]
    finally:
        agent.close()
