"""文件控制通道：独立配置、非阻塞浏览及可重传的另存状态。"""

import hashlib
import json
from pathlib import Path
import threading
import time
import sys

import httpx
from jsonschema import Draft202012Validator

from .config import AgentError
from .directory_input import validate_roots
from .node_files import browse_directory, export_artifacts
from .storage import atomic_json


class NodeFileService:
    def __init__(self, agent, transport):
        self.agent = agent
        self.transport = transport
        self.roots: list[str] = []
        self.revision = 0
        self.config_hash = ""
        self.available = False
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.path = agent.root / "file-configuration.json"
        if self.path.exists():
            if self.path.is_symlink():
                raise AgentError("文件权限配置不能是链接")
            self._apply(json.loads(self.path.read_text(encoding="utf-8")), persist=False)

    def _apply(self, payload: dict, *, persist: bool = True) -> None:
        if not isinstance(payload, dict) or set(payload) != {"revision", "output_roots", "config_hash"}:
            raise AgentError("文件权限配置格式错误")
        revision = payload["revision"]
        if type(revision) is not int or revision < 1:
            raise AgentError("文件权限配置修订无效")
        roots = validate_roots(payload["output_roots"])
        digest = hashlib.sha256(json.dumps({"revision": revision, "output_roots": roots}, ensure_ascii=False,
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != payload["config_hash"] or revision < self.revision:
            raise AgentError("文件权限摘要不符或修订回退")
        if revision == self.revision and digest != self.config_hash:
            raise AgentError("文件权限相同修订内容冲突")
        if persist:
            atomic_json(self.path, payload)
        self.roots, self.revision, self.config_hash = roots, revision, digest

    def sync(self) -> bool:
        response = self.agent.client.get(self.agent.api_path("/api/v1/agent/file-configuration"))
        if response.status_code == 404:
            self.available = False
            return True
        response.raise_for_status()
        self.available = True
        if response.status_code == 204:
            return self.revision == 0
        payload = response.json()
        try:
            self._apply(payload)
        except (AgentError, OSError, ValueError, TypeError):
            if isinstance(payload, dict) and type(payload.get("revision")) is int and isinstance(payload.get("config_hash"), str):
                self.agent._post("/api/v1/agent/file-configuration/ack", {"revision": payload["revision"],
                    "config_hash": payload["config_hash"], "status": "failed", "error": "文件权限配置未通过本机校验"})
            return False
        self.agent._post("/api/v1/agent/file-configuration/ack", {"revision": self.revision,
            "config_hash": self.config_hash, "status": "applied", "error": ""})
        return True

    def start(self) -> None:
        if self.available and self.thread is None:
            self.thread = threading.Thread(target=self._browse_loop, name="node-files", daemon=True)
            self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def _browse_loop(self) -> None:
        # SMB访问可能受操作系统网络超时影响；单个守护线程隔离，始终不阻塞心跳/取消。
        agent = self.agent
        with httpx.Client(base_url=agent.origin, headers={"Authorization": f"Bearer {agent.token}"},
                          trust_env=agent.client.trust_env, timeout=agent.config.request_timeout,
                          follow_redirects=False, transport=self.transport) as client:
            while not self.stop.is_set():
                try:
                    self.browse_once(client)
                except (httpx.HTTPError, AgentError, OSError, ValueError, TypeError, KeyError) as error:
                    # 浏览请求有服务端到期状态；连接失败不终止业务执行器。
                    observed = time.monotonic()
                    if observed - getattr(self, "last_browse_warning", 0) > 60:
                        self.last_browse_warning = observed
                        print(f"文件浏览通道暂不可用：{type(error).__name__}；稍后重试", file=sys.stderr)
                self.stop.wait(agent.config.poll_seconds)

    def browse_once(self, client: httpx.Client) -> None:
        from .runner import validate
        response = client.post(self.agent.api_path("/api/v1/agent/file-requests/claim"))
        if response.status_code in {204, 404}:
            return
        response.raise_for_status()
        command = response.json()
        validate(command, "file-request")
        payload = {"config_revision": command["config_revision"],
                   "output_config_revision": command["output_config_revision"],
                   "entries": [], "truncated": False, "next_offset": None, "error": ""}
        try:
            if command["expires_at"] <= time.time():
                raise AgentError("浏览请求已过期")
            if command["config_revision"] != self.agent.config_revision:
                raise AgentError("读取权限尚未同步，请稍后重试")
            roots = self.agent.input_roots
            if command["purpose"] == "output":
                if command["output_config_revision"] != self.revision:
                    raise AgentError("结果目录权限尚未同步")
                roots = self.roots
            payload.update(browse_directory(command["path"], roots, command["allowed_roots"],
                                            command["limit"], command.get("offset", 0)))
        except (AgentError, OSError, ValueError, TypeError):
            payload["error"] = "目录不可用、权限已变更、存在不安全文件或超过浏览上限"
        response = client.post(self.agent.api_path(f"/api/v1/agent/file-requests/{command['id']}/complete"), json=payload)
        response.raise_for_status()

    def verify_options(self, options: dict) -> None:
        from .runner import SCHEMAS
        schema = json.loads((SCHEMAS / "claim.schema.json").read_text(encoding="utf-8"))["$defs"]["node_files"]
        if next(Draft202012Validator(schema).iter_errors(options), None):
            raise AgentError("任务文件配置协议无效")
        if options["config_revision"] != self.agent.config_revision:
            raise AgentError("任务读取授权修订与本机不一致")
        if options.get("output_dir") and options["output_config_revision"] != self.revision:
            raise AgentError("任务结果目录授权修订与本机不一致")

    def save(self, directory: Path, state: dict, options: dict, retry_number: int = 0) -> None:
        payload = {"attempt_id": state["attempt_id"], "retry_number": retry_number,
                   "status": "failed", "files": [], "error": "另存中断，保留现场，请发起独立重试"}
        state["file_save"] = payload
        state["file_save_delivered"] = False
        self.agent._save(directory, state)

        def saved(row: dict) -> None:
            payload["files"].append(row)
            self.agent._save(directory, state)

        def progress() -> None:
            try:
                self.agent.heartbeat()
            except httpx.HTTPError:
                pass
            if state["run_id"] in self.agent.cancel_ids:
                raise AgentError("收到取消请求，停止另存并保留现场")

        try:
            # 业务运行可能持续数小时；导出前重新同步，不能沿用运行开始时的写授权。
            if not self.sync() or not self.available:
                raise AgentError("结果目录授权暂无法确认")
            self.verify_options(options)
            if not state.get("completion", {}).get("result"):
                raise AgentError("没有有效结果报告，不能确认声明产物")
            names = [row["name"] for row in state["completion"]["result"].get("artifacts", [])]
            export_artifacts(directory / "output", names, options["output_dir"],
                             state["run_id"], self.roots, options["allowed_output_roots"],
                             self.agent.config.max_artifact_bytes, progress, saved, retry_number)
            payload.update(status="succeeded", error="")
        except (AgentError, OSError, ValueError, TypeError, httpx.HTTPError):
            payload["error"] = "结果另存失败：请检查目录授权、磁盘空间、文件存在性或目录是否已存在；原始产物保留"
        self.agent._save(directory, state)

    def deliver(self, directory: Path, state: dict) -> None:
        if state.get("completion") and state.get("node_files", {}).get("output_dir") and not state.get("file_save"):
            state["file_save"] = {"attempt_id": state["attempt_id"], "retry_number": 0,
                "status": "failed", "files": [], "error": "运行结束但另存未开始，可能发生执行器重启，请独立重试另存"}
            self.agent._save(directory, state)
        if state.get("file_save") and not state.get("file_save_delivered"):
            try:
                self.agent._post(f"/api/v1/agent/runs/{state['run_id']}/file-save", state["file_save"])
            except httpx.HTTPError:
                return
            state["file_save_delivered"] = True
            self.agent._save(directory, state)

    def retry_once(self) -> None:
        if not self.available:
            return
        from .runner import validate
        response = self.agent._post("/api/v1/agent/file-save-retries/claim", {})
        if response.status_code == 204:
            return
        payload = response.json()
        validate(payload, "file-save-retry")
        directory = self.agent.run_directory(payload)
        state_path = directory / "state.json"
        if not state_path.is_file():
            self.agent._post(f"/api/v1/agent/runs/{payload['run_id']}/file-save", {
                "attempt_id": payload["attempt_id"], "retry_number": payload["retry_number"],
                "status": "failed", "files": [], "error": "本机原始产物缓存不存在，不能自动重跑脚本"})
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("run_id") != payload["run_id"] or state.get("attempt_id") != payload["attempt_id"]
                or not state.get("completion") or state.get("phase") != "terminal"):
            raise AgentError("另存重试与本机运行状态不一致")
        previous = state.get("file_save", {}).get("retry_number", -1)
        if previous < payload["retry_number"]:
            self.save(directory, state, payload["node_files"], payload["retry_number"])
        elif previous > payload["retry_number"]:
            raise AgentError("结果另存重试序号回退")
        self.deliver(directory, state)
