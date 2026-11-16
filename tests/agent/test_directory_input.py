import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from agent.config import AgentError, Config
from agent.connection import import_connection
import agent.directory_input as directory_input
from agent.directory_input import CAPABILITY, checked_directory, directory_manifest


spec = importlib.util.spec_from_file_location("directory_agent_support", Path(__file__).with_name("test_agent.py"))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
agent_factory = support.agent_factory


@pytest.fixture
def input_folder(tmp_path):
    root = tmp_path / "allow"
    folder = root / "batch"
    folder.mkdir(parents=True)
    (folder / "first.txt").write_bytes(b"first-data")
    (folder / "sub").mkdir()
    (folder / "sub/second.txt").write_bytes(b"second-data")
    return root, folder


def assign_directory(control, folder):
    control.claim["input_directory"] = str(folder)
    control.claim["parameters"]["input_dir"] = str(folder)


def test_windows_path_and_handle_ctime_difference_is_not_content_change(monkeypatch, input_folder):
    root, folder = input_folder
    original = directory_input.os.fstat

    def different_ctime(file_descriptor):
        observed = original(file_descriptor)
        fields = {name: getattr(observed, name) for name in (
            "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")}
        fields["st_ctime_ns"] += 1
        return SimpleNamespace(**fields)

    monkeypatch.setattr(directory_input.os, "fstat", different_ctime)
    manifest = directory_manifest(str(folder), [str(root)], 100 * 1024 * 1024)
    assert {entry["name"] for entry in manifest} == {"first.txt", "sub/second.txt"}


def test_real_process_reads_original_nested_directory_without_copy(agent_factory, input_folder):
    root, folder = input_folder
    source = support.PRELUDE + """
assert pathlib.Path(c['parameters']['input_dir']).is_absolute()
assert {row['name'] for row in c['input_manifest']} == {'first.txt', 'sub/second.txt'}
for row in c['input_manifest']:
    original = pathlib.Path(c['parameters']['input_dir']) / row['name']
    assert row['path'] == str(original)
    assert original.read_bytes() in (b'first-data', b'second-data')
assert not list((out.parent/'input').iterdir())
""" + support.RESULT
    agent, control = agent_factory(source, input_roots=[str(root)])
    assign_directory(control, folder)
    assert agent.run_once()
    completion = control.completions[-1]
    assert completion["observation"] == "exited" and completion["exit_code"] == 0
    context = json.loads(next(agent.root.glob("*/context.json")).read_text(encoding="utf-8"))
    assert context["parameters"]["input_dir"] == str(folder)
    assert context["input_manifest"][0]["sha256"] == hashlib.sha256(b"first-data").hexdigest()
    assert (folder / "first.txt").read_bytes() == b"first-data"
    assert not any("/input" == request.url.path for request in control.requests)
    heartbeats = [json.loads(request.content) for request in control.requests if request.url.path.endswith("/heartbeat")]
    assert all(CAPABILITY in heartbeat["capabilities"] for heartbeat in heartbeats)
    assert not agent.run_once()  # 已交付终态不重复启动。


@pytest.mark.parametrize("kind", ["no-roots", "outside", "missing", "mismatch", "mixed", "empty", "large"])
def test_invalid_directory_is_startup_failed_before_process(agent_factory, input_folder, kind):
    root, folder = input_folder
    overrides = {"input_roots": [] if kind == "no-roots" else [str(folder if kind == "outside" else root)]}
    if kind == "large":
        overrides["max_expanded_bytes"] = 1024
        (folder / "large.bin").write_bytes(b"x" * 1025)
    agent, control = agent_factory(**overrides)
    selected = root if kind == "outside" else folder
    if kind in {"missing", "empty"}:
        selected = root / kind
        if kind == "empty":
            selected.mkdir()
    assign_directory(control, selected)
    if kind == "mismatch":
        control.claim["parameters"]["input_dir"] = str(root)
    if kind == "mixed":
        control.claim["input_files"] = [{"name": "x.txt", "sha256": "0" * 64, "url": "/api/v1/input"}]
    agent.run_once()
    assert control.completions[-1]["observation"] == "startup_failed"
    assert not any(request.url.path.endswith("/started") for request in control.requests)


def test_no_roots_cannot_advertise_directory_capability(agent_factory):
    agent, control = agent_factory(capabilities=["python", CAPABILITY])
    agent.heartbeat(force=True)
    assert json.loads(control.requests[-1].content)["capabilities"] == ["python"]


def test_directory_capability_counts_toward_heartbeat_limit(agent_factory, input_folder):
    root, _ = input_folder
    with pytest.raises(AgentError, match="30 项"):
        agent_factory(input_roots=[str(root)], capabilities=[f"cap-{index}" for index in range(30)])
    agent, control = agent_factory(input_roots=[str(root)], capabilities=[f"cap-{index}" for index in range(29)])
    agent.heartbeat(force=True)
    assert len(json.loads(control.requests[-1].content)["capabilities"]) == 30


@pytest.mark.parametrize("value", [None, "C:/", ["relative"], [""], ["missing/path"], [12], ["\\\\.\\pipe\\demo"]])
def test_invalid_roots_rejected_at_config_load(tmp_path, value):
    with pytest.raises((AgentError, OSError)):
        Config(base_url="http://localhost", token_file=tmp_path / "token", state_dir=tmp_path / "state",
               runtimes={"python": sys.executable}, input_roots=value)


def test_traversal_and_file_instead_of_directory_rejected(input_folder):
    root, folder = input_folder
    with pytest.raises(AgentError):
        checked_directory(str(folder / "../batch"))
    with pytest.raises(AgentError):
        checked_directory(str(folder / "first.txt"))


def test_count_depth_and_directory_limits(input_folder):
    root, folder = input_folder
    for index in range(999):
        (folder / f"extra-{index}.txt").touch()
    with pytest.raises(AgentError, match="数量"):
        directory_manifest(str(folder), [str(root)], 100 * 1024 * 1024)
    deep = root / "deep"
    cursor = deep
    for _ in range(34):
        cursor.mkdir()
        cursor = cursor / "a"
    with pytest.raises(AgentError, match="层级"):
        directory_manifest(str(deep), [str(root)], 100 * 1024 * 1024)
    many = root / "many"
    many.mkdir()
    for index in range(1000):
        (many / str(index)).mkdir()
    with pytest.raises(AgentError, match="目录数量"):
        directory_manifest(str(many), [str(root)], 100 * 1024 * 1024)


def test_hardlink_rejected(input_folder):
    root, folder = input_folder
    os.link(folder / "first.txt", folder / "hard.txt")
    with pytest.raises(AgentError, match="硬链接"):
        directory_manifest(str(folder), [str(root)], 100 * 1024 * 1024)


def test_real_junction_or_symlink_rejected_in_content_and_root(input_folder):
    root, folder = input_folder
    target = root.parent / "synthetic-outside"
    target.mkdir()
    (target / "other.txt").write_text("synthetic", encoding="utf-8")
    link = folder / "link"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(AgentError, match="链接|重解析"):
            directory_manifest(str(folder), [str(root)], 100 * 1024 * 1024)
        with pytest.raises(AgentError, match="链接|重解析"):
            checked_directory(str(link))
        with pytest.raises(AgentError, match="链接|重解析"):
            checked_directory(str(link / "nested"))
    finally:
        if os.name == "nt":
            link.rmdir()  # 仅移除本测试创建的 junction，不递归删除目标目录。
        else:
            link.unlink()


@pytest.mark.parametrize("change", ["grow-file", "add-file"])
def test_real_change_during_hash_rejected(tmp_path, change):
    folder = tmp_path / "source"
    folder.mkdir()
    file = folder / "data.bin"
    file.write_bytes(b"x" * 131072)
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
        if ticks == 2:  # 第一次遍历，第二次已经实读第一块文件内容。
            if change == "grow-file":
                with file.open("ab") as handle:
                    handle.write(b"changed")
            else:
                (folder / "new.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(AgentError, match="变动|增长"):
        directory_manifest(str(folder), [str(folder)], 100 * 1024 * 1024, progress)


def test_directory_access_failure_reports_existing_completion(agent_factory, input_folder, monkeypatch):
    root, folder = input_folder
    agent, control = agent_factory(input_roots=[str(root)])
    assign_directory(control, folder)
    original = os.scandir
    def denied(path):
        if Path(path) == folder:
            raise PermissionError("synthetic permission failure")
        return original(path)
    monkeypatch.setattr(os, "scandir", denied)
    agent.run_once()
    assert control.completions[-1]["observation"] == "startup_failed"


def test_unrelated_offline_root_does_not_block_available_directory(input_folder):
    root, folder = input_folder
    offline = root.parent / "offline-share"
    manifest = directory_manifest(str(folder), [str(offline), str(root)], 100 * 1024 * 1024)
    assert {entry["name"] for entry in manifest} == {"first.txt", "sub/second.txt"}


def test_project_root_limits_shared_node_directory(input_folder):
    root, folder = input_folder
    other = root / "other-project"
    other.mkdir()
    (other / "other.txt").write_text("other", encoding="utf-8")
    with pytest.raises(AgentError, match="不在执行电脑白名单"):
        directory_manifest(str(other), [str(root)], 100 * 1024 * 1024,
                           project_roots=[str(folder)])
    assert directory_manifest(str(folder), [str(root)], 100 * 1024 * 1024,
                              project_roots=[str(folder)])


def test_similar_sibling_name_is_not_treated_as_authorized(tmp_path):
    root = tmp_path / "Data"
    sibling = tmp_path / "Data2"
    root.mkdir()
    sibling.mkdir()
    (sibling / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(AgentError, match="不在执行电脑白名单"):
        directory_manifest(str(sibling), [str(root)], 100 * 1024 * 1024)


def test_connection_optional_roots_import_and_old_config_compatibility(tmp_path, input_folder):
    root, _ = input_folder
    payload = {"format": "workbench-connection-v1", "node_id": "synthetic-node", "name": "合成目录电脑",
               "base_url": "https://workbench.example.invalid", "token": "SYNTHETIC_NODE_TOKEN_ONLY_0123456789",
               "runtimes": {"python": sys.executable}, "capabilities": ["python"], "input_roots": [str(root)]}
    source = tmp_path / "connection.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    imported = import_connection(source, tmp_path / "imported")
    assert Config.load(imported).input_roots == [str(root.resolve())]
    old = json.loads(imported.read_text(encoding="utf-8"))
    del old["input_roots"]
    imported.write_text(json.dumps(old), encoding="utf-8")
    assert Config.load(imported).input_roots == []
