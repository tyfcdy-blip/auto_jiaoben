import codecs
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit, urlunsplit
import zipfile

import httpx
from jsonschema import Draft202012Validator

from . import __version__
from .config import AgentError, Config
from .directory_input import CAPABILITY, directory_manifest
from .node_files import CAPABILITY as FILE_CAPABILITY, authorized_path, selected_manifest
from .node_file_service import NodeFileService
from .process import ProcessTree
from .storage import StateLock, atomic_json, extract_bundle, safe_name

SCHEMAS = Path(__file__).resolve().parent.parent / "contracts" / "runtime"
CONFIG_CAPABILITY = "node-config-v1"


def validate(payload: dict, name: str) -> None:
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    if errors:
        # 校验错误可能包含业务参数或秘密，向平台只返回字段位置。
        location = ".".join(map(str, errors[0].absolute_path)) or "root"
        raise AgentError(f"{name} 协议校验失败：{location}")


class Agent:
    def __init__(self, config: Config, *, config_path: Path | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.config = config
        self.config_path = config_path.resolve() if config_path else None
        self.token = config.read_token()
        self.root = config.state_dir.resolve()
        if config.state_dir.is_symlink():
            raise AgentError("状态目录不能是链接")
        self.root.mkdir(parents=True, exist_ok=True)
        origin = urlsplit(config.base_url)
        self.application_prefix = origin.path.rstrip("/")
        self.api_prefix = self.application_prefix + "/api/v1"
        self.origin = urlunsplit((origin.scheme, origin.netloc, "", "", ""))
        self.client = httpx.Client(base_url=self.origin,
                                  headers={"Authorization": f"Bearer {self.token}"},
                                  trust_env=origin.hostname not in {"127.0.0.1", "localhost", "::1"},
                                  timeout=config.request_timeout, follow_redirects=False, transport=transport)
        self.last_heartbeat = 0.0
        self.cancel_ids: set[str] = set()
        self.input_roots = list(config.input_roots)
        self.config_revision, self.config_hash = config.config_revision, config.config_hash
        self.root_status = {root: {"path": root, "status": "unknown", "checked_at": None}
                            for root in self.input_roots}
        self.file_service = NodeFileService(self, transport) if self.config_path else None
        if self.config_path:
            advertised = [value for value in config.capabilities if value not in {CAPABILITY, CONFIG_CAPABILITY, FILE_CAPABILITY}]
            if len(advertised) + 2 + (1 if self.input_roots else 0) > 30:
                raise AgentError("配置同步和目录输入能力计入最多30项心跳能力")

    def close(self) -> None:
        if self.file_service:
            self.file_service.close()
        self.client.close()

    def _post(self, path: str, payload: dict) -> httpx.Response:
        response = self.client.post(self.api_path(path), json=payload)
        response.raise_for_status()
        return response

    def api_path(self, path: str) -> str:
        if not path.startswith("/api/v1/"):
            raise AgentError("执行器内部接口路径无效")
        return self.application_prefix + path

    @staticmethod
    def _configuration_hash(revision: int, input_roots: list[str]) -> str:
        payload = {"revision": revision, "input_roots": input_roots}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _capabilities(self) -> list[str]:
        values = [value for value in self.config.capabilities if value not in {CAPABILITY, CONFIG_CAPABILITY, FILE_CAPABILITY}]
        if self.config_path:
            values.append(CONFIG_CAPABILITY)
            values.append(FILE_CAPABILITY)
        if self.input_roots:
            values.append(CAPABILITY)
        return values

    def _persist_configuration(self, revision: int, config_hash: str, roots: list[str]) -> None:
        if not self.config_path or self.config_path.is_symlink() or not self.config_path.is_file():
            raise AgentError("本机配置文件不可安全更新")
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise AgentError("本机配置文件格式无效")
        payload.update(input_roots=roots, config_revision=revision, config_hash=config_hash)
        atomic_json(self.config_path, payload)

    def _ack_configuration(self, revision: int, config_hash: str, status: str, error: str = "") -> None:
        self._post("/api/v1/agent/configuration/ack", {
            "revision": revision, "config_hash": config_hash,
            "status": status, "error": error[:1000],
        })

    def sync_configuration(self) -> bool:
        if not self.config_path:
            return True
        response = self.client.get(self.api_path("/api/v1/agent/configuration"))
        if response.status_code == 404:
            return True  # 兼容尚未提供受管配置接口的旧工作台。
        response.raise_for_status()
        if response.status_code == 204:
            return self.config_revision == 0
        payload = response.json()
        if (not isinstance(payload, dict)
                or set(payload) != {"revision", "input_roots", "config_hash"}
                or type(payload["revision"]) is not int or payload["revision"] < 1
                or not isinstance(payload["input_roots"], list)
                or not isinstance(payload["config_hash"], str)):
            raise AgentError("工作台配置响应格式无效")
        revision, config_hash = payload["revision"], payload["config_hash"]
        try:
            from .directory_input import validate_roots
            roots = validate_roots(payload["input_roots"])
            expected = self._configuration_hash(revision, roots)
            if expected != config_hash:
                raise AgentError("工作台配置摘要不匹配")
            if revision < self.config_revision:
                raise AgentError("工作台配置修订发生回退")
            if revision == self.config_revision and config_hash != self.config_hash:
                raise AgentError("相同配置修订的摘要不一致")
            if revision > self.config_revision:
                self._persist_configuration(revision, config_hash, roots)
                self.input_roots = roots
                self.config_revision, self.config_hash = revision, config_hash
                self.root_status = {root: self.root_status.get(root, {
                    "path": root, "status": "unknown", "checked_at": None}) for root in roots}
            self._ack_configuration(revision, config_hash, "applied")
            return True
        except (AgentError, OSError, ValueError, TypeError) as error:
            reason = str(error) if isinstance(error, AgentError) else type(error).__name__
            self._ack_configuration(revision, config_hash, "failed", reason)
            return False

    def heartbeat(self, *, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_heartbeat < self.config.heartbeat_seconds:
            return
        body = {"capabilities": self._capabilities(),
                "isolation": "development", "agent_version": __version__}
        if self.config_path:
            body.update(config_revision=self.config_revision,
                        config_hash=self.config_hash or None,
                        reported_input_roots=self.input_roots,
                        root_status=list(self.root_status.values()))
        response = self._post("/api/v1/agent/heartbeat", body)
        cancellation = response.json().get("cancel_run_ids", [])
        if not isinstance(cancellation, list) or not all(isinstance(value, str) for value in cancellation):
            raise AgentError("心跳响应格式无效")
        self.cancel_ids = set(cancellation)
        self.last_heartbeat = time.monotonic()

    def download(self, url: str, destination: Path, digest: str) -> None:
        parsed = urlsplit(url)
        decoded = unquote(parsed.path)
        if (parsed.scheme or parsed.netloc or not parsed.path.startswith(self.api_prefix + "/")
                or parsed.fragment or "\\" in url or decoded != parsed.path
                or any(part in {"", ".", ".."} for part in parsed.path[1:].split("/"))
                or any(ord(character) < 32 or character.isspace() for character in url)):
            raise AgentError("拒绝跨源或非协议下载地址")
        temporary = destination.with_suffix(destination.suffix + ".part")
        hasher = hashlib.sha256()
        count = 0
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                with temporary.open("xb") as handle:
                    for chunk in response.iter_bytes():
                        count += len(chunk)
                        if count > self.config.max_download_bytes:
                            raise AgentError("下载文件超过容量上限")
                        hasher.update(chunk)
                        handle.write(chunk)
                        try:
                            self.heartbeat()
                        except httpx.HTTPError:
                            pass
                    handle.flush()
                    os.fsync(handle.fileno())
            if hasher.hexdigest() != digest:
                raise AgentError("下载文件 SHA256 与已发布清单不一致")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _save(self, directory: Path, state: dict) -> None:
        atomic_json(directory / "state.json", state)

    def _completion(self, claim: dict, observation: str, *, code: int | None = None,
                    result: dict | None = None, error: str | None = None, artifacts: str = "none") -> dict:
        return {"attempt_id": claim["attempt_id"], "exit_code": code, "result": result,
                "error": error.replace(self.token, "[REDACTED]")[:2000] if error else None,
                "observation": observation, "artifact_state": artifacts}

    def _deliver(self, directory: Path, state: dict) -> None:
        if self.file_service:
            self.file_service.deliver(directory, state)
        try:
            self._upload_logs(directory, state)
        except httpx.HTTPError:
            # 日志端点异常不能隐藏业务终态；日志仍留在本地等待下轮重传。
            pass
        completion = state.get("completion")
        if completion is None or state.get("delivered"):
            return
        run_id = state["run_id"]
        artifact_paths = state.get("artifact_paths", [])
        for name in artifact_paths if not state.get("delivery_prepared") else []:
            if name in state.get("uploaded_artifacts", []):
                continue
            path = directory / "output" / safe_name(name)
            try:
                self._check_artifact(path)
                with path.open("rb") as handle:
                    response = self.client.post(self.api_path(f"/api/v1/agent/runs/{run_id}/artifacts/{name}"),
                                                params={"attempt_id": state["attempt_id"]},
                                                content=handle, headers={"Content-Type": "application/octet-stream"})
                response.raise_for_status()
            except (AgentError, OSError, httpx.HTTPError):
                completion["artifact_state"] = "incomplete"
                continue
            state.setdefault("uploaded_artifacts", []).append(name)
            self._save(directory, state)
        # 第一次终态发送前固定归档观测；丢响应后的重传不能改变终态载荷。
        state["delivery_prepared"] = True
        self._save(directory, state)
        self._post(f"/api/v1/agent/runs/{run_id}/complete", completion)
        state["delivered"] = True
        self._save(directory, state)

    def _check_artifact(self, path: Path) -> None:
        if path.resolve() != path.absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > self.config.max_artifact_bytes:
            raise AgentError("产物不存在、为链接或超过配额")

    def recover(self) -> None:
        for state_path in sorted(self.root.glob("*/state.json")):
            directory = state_path.parent
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not state.get("completion"):
                state["completion"] = self._completion(state, "unknown", error="执行器重启后不能证明原进程及外部结果，禁止重复启动")
                state["phase"] = "terminal"
                self._save(directory, state)
            self._deliver(directory, state)

    def run_once(self) -> bool:
        with StateLock(self.root / "agent.lock"):
            self.heartbeat(force=True)
            self.recover()
            if not self.sync_configuration():
                return False
            if self.file_service:
                if not self.file_service.sync():
                    return False
                self.file_service.start()
                self.file_service.retry_once()
            response = self._post("/api/v1/agent/claim", {})
            if response.status_code == 204:
                return False
            claim = response.json()
            validate(claim, "claim")
            self.execute(claim)
            return True

    def execute(self, claim: dict) -> None:
        validate(claim, "claim")
        directory = self.run_directory(claim)
        if directory.exists():
            # 即使控制台重发领取，也不以目录存在且结果未送达为重新启动依据。
            if not (directory / "state.json").is_file():
                raise AgentError("发现无状态运行目录，需人工核对")
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
            if not state.get("completion"):
                state["completion"] = self._completion(claim, "unknown", error="重复领取但原启动状态未确认")
                self._save(directory, state)
            self._deliver(directory, state)
            return
        directory.mkdir()
        state = {"run_id": claim["run_id"], "attempt_id": claim["attempt_id"], "phase": "preparing",
                 "sequence": 0, "ack_sequence": 0, "log_bytes": 0, "delivered": False}
        if claim.get("node_files"):
            state["node_files"] = claim["node_files"]
        self._save(directory, state)
        try:
            if claim["side_effects"]:
                raise AgentError("development 节点禁止运行有副作用任务")
            context, command = self._prepare(directory, claim)
            state["phase"] = "start_intent"
            self._save(directory, state)
            completion = self._run(directory, state, claim, context, command)
            state["completion"] = completion
        except (AgentError, OSError, ValueError, zipfile.BadZipFile, NotImplementedError, httpx.HTTPError) as error:
            observation = "unknown" if state["phase"] in {"start_intent", "running"} else "startup_failed"
            state["completion"] = self._completion(claim, observation, error=f"{type(error).__name__}: {error}")
        state["phase"] = "terminal"
        self._save(directory, state)
        if claim.get("node_files", {}).get("output_dir") and self.file_service:
            self.file_service.save(directory, state, claim["node_files"])
        self._deliver(directory, state)

    def run_directory(self, claim: dict) -> Path:
        identity = f"{claim['run_id']}\0{claim['attempt_id']}".encode("utf-8")
        return self.root / hashlib.sha256(identity).hexdigest()[:32]

    def _prepare(self, directory: Path, claim: dict) -> tuple[dict, list[str]]:
        runtime = self.config.runtimes.get(claim["command"][0])
        if not runtime:
            raise AgentError("节点未登记请求的运行时别名")
        entry = safe_name(claim["command"][1], nested=True)
        for name in ("package", "input", "output", "business-state"):
            (directory / name).mkdir()
        self.download(claim["bundle_url"], directory / "bundle.zip", claim["bundle_sha256"])
        extract_bundle(directory / "bundle.zip", directory / "package", self.config.max_expanded_bytes)
        entry_path = directory / "package" / entry
        if not entry_path.is_file():
            raise AgentError("程序入口不存在")
        manifest = []
        options = claim.get("node_files")
        if options:
            if not self.file_service:
                raise AgentError("执行器尚未启用受管文件能力")
            self.file_service.verify_options(options)
            if options.get("output_dir"):
                authorized_path(options["output_dir"], self.file_service.roots,
                                options["allowed_output_roots"], directory=True)
            def file_progress() -> None:
                try:
                    self.heartbeat()
                except httpx.HTTPError:
                    pass
                if claim["run_id"] in self.cancel_ids:
                    raise AgentError("准备本机输入期间收到停止请求")
            manifest = selected_manifest(options["inputs"], claim["parameters"], self.input_roots,
                                         options["allowed_input_roots"], self.config.max_expanded_bytes, file_progress,
                                         options["input_kinds"])
        if "input_directory" in claim:
            if claim["input_files"] or claim["parameters"].get("input_dir") != claim["input_directory"]:
                raise AgentError("目录输入参数与领取清单不一致，或同时指定平台输入文件")
            def progress() -> None:
                try:
                    self.heartbeat()
                except httpx.HTTPError:
                    pass
                if claim["run_id"] in self.cancel_ids:
                    raise AgentError("准备目录输入期间收到停止请求，未启动业务程序")
            project_roots = claim.get("allowed_input_roots")
            if project_roots is not None and claim.get("config_revision") != self.config_revision:
                raise AgentError("任务要求的电脑配置修订与本机不一致")
            try:
                directory_rows = directory_manifest(claim["input_directory"], self.input_roots,
                                              self.config.max_expanded_bytes, progress,
                                              project_roots=project_roots)
                existing_paths = {row["path"] for row in manifest}
                manifest.extend(row for row in directory_rows if row["path"] not in existing_paths)
            except (AgentError, OSError):
                self._mark_root_status(claim["input_directory"], "unavailable")
                raise
            self._mark_root_status(claim["input_directory"], "available")
        names = {row["name"].casefold() for row in manifest}
        if len(names) != len(manifest):
            raise AgentError("本机输入清单名称冲突")
        input_bytes = sum(Path(row["path"]).stat().st_size for row in manifest)
        if len(manifest) > 1000 or input_bytes > self.config.max_expanded_bytes:
            raise AgentError("输入清单总容量或数量超过上限")
        for input_file in claim["input_files"]:
            name = safe_name(input_file["name"])
            if name.casefold() in names:
                raise AgentError("输入清单名称冲突")
            names.add(name.casefold())
            path = directory / "input" / name
            self.download(input_file["url"], path, input_file["sha256"])
            input_bytes += path.stat().st_size
            if input_bytes > self.config.max_expanded_bytes:
                raise AgentError("输入批次总容量超过上限")
            manifest.append({"name": name, "path": str(path), "sha256": input_file["sha256"]})
            if len(manifest) > 1000:
                raise AgentError("输入清单总数量超过上限")
        context = {"protocol_version": "1", "run_id": claim["run_id"], "attempt_id": claim["attempt_id"],
                   "operation": claim["operation"], "parameters": claim["parameters"], "input_manifest": manifest,
                   "output_dir": str(directory / "output"), "state_dir": str(directory / "business-state"),
                   "cancel_file": str(directory / "cancel.requested")}
        validate(context, "context")
        atomic_json(directory / "context.json", context)
        command = [runtime, str(entry_path), *claim["command"][2:], "--context-file", str(directory / "context.json")]
        return context, command

    def _mark_root_status(self, directory: str, status: str) -> None:
        selected = Path(directory)
        for root in self.input_roots:
            candidate = Path(root)
            try:
                within = selected == candidate or selected.is_relative_to(candidate)
            except (OSError, ValueError):
                within = False
            if within:
                self.root_status[root] = {"path": root, "status": status, "checked_at": time.time()}

    def _append_log(self, directory: Path, state: dict, stream: str, text: str) -> None:
        if state.get("log_truncated"):
            return
        text = text.replace(self.token, "[REDACTED]")[:4096]
        size = len(text.encode("utf-8"))
        if state["log_bytes"] + size > self.config.max_log_bytes:
            stream, text = "system", "日志达到配置上限，后续输出已截断；业务进程继续排空输出"
            state["log_truncated"] = True
        state["sequence"] += 1
        entry = {"sequence": state["sequence"], "stream": stream, "text": text}
        with (directory / "logs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        state["log_bytes"] += size

    def _upload_logs(self, directory: Path, state: dict) -> None:
        path = directory / "logs.jsonl"
        if not path.exists():
            return
        entries = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                entry = json.loads(line)
                if entry["sequence"] <= state["ack_sequence"]:
                    continue
                entries.append(entry)
                if len(entries) == 100:
                    break
        if entries:
            self._post(f"/api/v1/agent/runs/{state['run_id']}/logs", {"attempt_id": state["attempt_id"], "entries": entries})
            state["ack_sequence"] = entries[-1]["sequence"]
            self._save(directory, state)

    def _run(self, directory: Path, state: dict, claim: dict, context: dict, command: list[str]) -> dict:
        events: queue.Queue = queue.Queue(maxsize=256)
        dropped = threading.Event()
        environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "LANG") if key in os.environ}
        environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                            "HOME": str(directory), "USERPROFILE": str(directory), "TEMP": str(directory), "TMP": str(directory)})
        process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("launcher.py"))],
                                   cwd=directory / "package", env=environment, shell=False,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name != "nt")
        tree = None
        readers = []

        def read_stream(handle, stream: str) -> None:
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            try:
                while chunk := handle.read1(4096):
                    text = decoder.decode(chunk)
                    try:
                        events.put_nowait((stream, text))
                    except queue.Full:
                        dropped.set()
                tail = decoder.decode(b"", final=True)
                if tail:
                    try:
                        events.put_nowait((stream, tail))
                    except queue.Full:
                        dropped.set()
            finally:
                handle.close()

        try:
            tree = ProcessTree(process)
            state.update({"phase": "running", "pid": process.pid})
            self._save(directory, state)
            # 必须建立树约束且服务端确认后才能解锁真正业务入口。
            self._post(f"/api/v1/agent/runs/{claim['run_id']}/started", {"attempt_id": claim["attempt_id"], "pid": process.pid})
            for stream in ("stdout", "stderr"):
                thread = threading.Thread(target=read_stream, args=(getattr(process, stream), stream), daemon=True)
                thread.start()
                readers.append(thread)
            process.stdin.write((json.dumps(command) + "\n").encode("utf-8"))
            process.stdin.close()
            deadline = time.monotonic() + claim["timeout_seconds"]
            next_control = 0.0
            observation = "exited"
            while process.poll() is None:
                self._drain(events, directory, state)
                if time.monotonic() >= deadline:
                    observation = "timeout"
                    tree.stop()
                    break
                if time.monotonic() >= next_control:
                    next_control = time.monotonic() + self.config.poll_seconds
                    try:
                        self.heartbeat()
                        response = self.client.get(self.api_path(f"/api/v1/agent/runs/{claim['run_id']}/control"), params={"attempt_id": claim["attempt_id"]})
                        response.raise_for_status()
                        if response.json().get("cancel_requested") or claim["run_id"] in self.cancel_ids:
                            Path(context["cancel_file"]).write_text("cancel", encoding="utf-8")
                            observation = "canceled"
                            tree.stop()
                            break
                        self._upload_logs(directory, state)
                    except httpx.HTTPError:
                        # 离线不重新启动；本地时限及有界日志继续生效。
                        pass
                time.sleep(0.05)
            orphaned = observation == "exited" and tree.has_live_members()
            tree.stop()  # 主入口结束后清除仍持有管道的后台子进程。
            for thread in readers:
                thread.join(timeout=5)
            self._drain(events, directory, state)
            if dropped.is_set():
                self._append_log(directory, state, "system", "日志突发超过内存队列容量，部分输出已丢弃")
            result, artifact_state, error = self._result(directory, state, claim)
            if orphaned:
                error = "主入口退出后仍有活动子进程，已终止进程树，不能确认业务完整完成"
            return self._completion(claim, observation, code=process.returncode, result=result, error=error, artifacts=artifact_state)
        finally:
            if tree:
                try:
                    tree.stop()
                finally:
                    tree.close()
            elif process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for handle in (process.stdin, process.stdout, process.stderr):
                if handle and not handle.closed:
                    handle.close()

    def _drain(self, events: queue.Queue, directory: Path, state: dict) -> None:
        for _ in range(256):
            try:
                stream, text = events.get_nowait()
            except queue.Empty:
                return
            self._append_log(directory, state, stream, text)

    def _result(self, directory: Path, state: dict, claim: dict) -> tuple[dict | None, str, str | None]:
        path = directory / "output" / "result.json"
        if not path.exists():
            return None, "none", "程序未产生必要的结果报告" if claim["result_required"] else None
        try:
            if path.resolve() != path.absolute() or path.is_symlink() or path.stat().st_size > 64000:
                raise AgentError("结果报告不是安全有界文件")
            result = json.loads(path.read_text(encoding="utf-8"))
            validate(result, "result")
            if len(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)) > 64000:
                raise AgentError("结果报告超过协议容量上限")
            if result["run_id"] != claim["run_id"] or result["attempt_id"] != claim["attempt_id"]:
                raise AgentError("结果报告不属于当前运行尝试")
            artifact_state = "none"
            paths = []
            for artifact in result.get("artifacts", []):
                name = safe_name(artifact["name"])
                if name == "result.json" or name in paths:
                    raise AgentError("产物名称重复或使用保留名称")
                try:
                    self._check_artifact(directory / "output" / name)
                    paths.append(name)
                    if artifact_state != "incomplete":
                        artifact_state = "complete"
                except AgentError:
                    artifact_state = "incomplete"
                    if artifact["required"]:
                        raise AgentError("必要产物不存在或不安全")
            state["artifact_paths"] = paths
            return result, artifact_state, None
        except (AgentError, OSError, ValueError) as error:
            return None, "incomplete", str(error)[:2000]
