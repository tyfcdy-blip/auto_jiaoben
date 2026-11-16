"""导入一次性节点连接文件；只生成本机配置，不联网、不安装运行环境。"""

import json
import os
from pathlib import Path
import re
import subprocess

from .config import AgentError, Config


FIELDS = {"format", "node_id", "name", "base_url", "token", "runtimes", "capabilities"}


def protect_directory(directory: Path) -> None:
    if os.name != "nt":
        directory.chmod(0o700)
        return
    # Windows chmod 不会收紧 DACL；对新建空目录先移除继承，再仅授予当前身份。
    system = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    identity = subprocess.run([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
                              capture_output=True, timeout=10, check=False)
    matches = re.findall(rb'S-1-[0-9-]+', identity.stdout)
    if identity.returncode != 0 or len(matches) != 1:
        raise AgentError("无法确认本机身份，未写入节点凭据")
    sid = matches[0].decode("ascii")
    protection = subprocess.run([str(system / "icacls.exe"), str(directory), "/inheritance:r", "/grant:r",
                                 f"*{sid}:(OI)(CI)F"], capture_output=True, timeout=10, check=False)
    if protection.returncode != 0:
        raise AgentError("无法限制连接目录访问权限，未写入节点凭据")


def import_connection(source: Path, destination: Path | None = None) -> Path:
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 65536:
        raise AgentError("连接文件不存在、为链接或超过大小限制")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if (not isinstance(raw, dict) or not FIELDS.issubset(raw)
            or set(raw) - FIELDS - {"input_roots", "config_revision", "config_hash"}
            or raw.get("format") != "workbench-connection-v1"):
        raise AgentError("连接文件格式或字段不受支持")
    node_id = raw["node_id"]
    if not isinstance(node_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", node_id):
        raise AgentError("连接文件中的节点标识无效")
    if not isinstance(raw["name"], str) or not 1 <= len(raw["name"]) <= 100:
        raise AgentError("连接文件中的电脑名称无效")
    token = raw["token"]
    if not isinstance(token, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{32,4096}", token):
        raise AgentError("连接文件中的节点凭据无效")
    runtimes = raw["runtimes"]
    if not isinstance(raw["base_url"], str):
        raise AgentError("连接文件中的工作台地址无效")
    if not isinstance(runtimes, dict) or not runtimes or len(runtimes) > 30 or any(
            not isinstance(alias, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", alias) for alias in runtimes):
        raise AgentError("连接文件中的运行时名称无效")
    directory = (destination or Path.home() / ".automation-workbench" / "connections" / node_id).absolute()
    if directory.resolve() != directory or directory.is_symlink():
        raise AgentError("连接目录不能经过符号链接或越界段")
    config_payload = {"base_url": raw["base_url"], "token_file": "node-token.txt", "state_dir": "state",
                      "runtimes": runtimes, "capabilities": raw["capabilities"], "isolation": "development"}
    if "input_roots" in raw:
        config_payload["input_roots"] = raw["input_roots"]
    if "config_revision" in raw:
        config_payload["config_revision"] = raw["config_revision"]
    if "config_hash" in raw:
        config_payload["config_hash"] = raw["config_hash"]
    # 复用原配置规则：远端 HTTPS、本机运行时存在、开发身份限制均不可绕过。
    Config(**(config_payload | {"token_file": directory / "node-token.txt", "state_dir": directory / "state"}))
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise AgentError("此连接目录已存在，拒绝覆盖；请使用原配置启动或选择新的空目录") from error
    created = []
    try:
        try:
            protect_directory(directory)
        except subprocess.SubprocessError as error:
            raise AgentError("设置连接目录权限超时或失败，未写入节点凭据") from error
        for name, content in (("node-token.txt", token), ("agent.json", json.dumps(config_payload, ensure_ascii=False, indent=2))):
            path = directory / name
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                path.chmod(0o600)
        Config.load(directory / "agent.json").read_token()
    except Exception:
        # 只清理由本次创建的文件；不递归删除、不触碰任何已有配置。
        for path in reversed(created):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return directory / "agent.json"
