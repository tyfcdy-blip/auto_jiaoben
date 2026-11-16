from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


class AgentError(Exception):
    """可向运维人员报告的受控执行错误。"""


@dataclass(frozen=True)
class Config:
    base_url: str
    token_file: Path
    state_dir: Path
    runtimes: dict[str, str]
    capabilities: list[str] = field(default_factory=lambda: ["python"])
    isolation: str = "development"
    poll_seconds: float = 2.0
    heartbeat_seconds: float = 15.0
    request_timeout: float = 5.0
    max_download_bytes: int = 50 * 1024 * 1024
    max_expanded_bytes: int = 100 * 1024 * 1024
    max_log_bytes: int = 5 * 1024 * 1024
    max_artifact_bytes: int = 20 * 1024 * 1024
    input_roots: list[str] = field(default_factory=list)
    config_revision: int = 0
    config_hash: str = ""

    def __post_init__(self) -> None:
        from .directory_input import CAPABILITY, validate_roots
        object.__setattr__(self, "input_roots", validate_roots(self.input_roots))
        origin = urlsplit(self.base_url)
        if origin.scheme not in {"http", "https"} or not origin.netloc or origin.username:
            raise AgentError("base_url 必须是合法 HTTP(S) 源站")
        if origin.query or origin.fragment:
            raise AgentError("base_url 不允许查询参数或片段")
        prefix = origin.path.rstrip("/")
        if origin.path.endswith("//") or (prefix and any(part in {"", ".", ".."} for part in prefix[1:].split("/"))):
            raise AgentError("应用前缀包含空段或越界段")
        if re.search(r"[%\\\s\x00-\x1f]", prefix):
            raise AgentError("应用前缀不能包含编码、反斜线或空白字符")
        if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise AgentError("明文 HTTP 仅允许本机开发测试；远端连接必须使用 HTTPS")
        if self.isolation != "development":
            raise AgentError("生产独立执行身份尚未验证，拒绝 os_user 模式；禁止冒用开发身份")
        if not isinstance(self.runtimes, dict) or not self.runtimes:
            raise AgentError("必须登记至少一个运行时别名")
        if not isinstance(self.capabilities, list) or len(self.capabilities) > 100 or not all(isinstance(value, str) and 0 < len(value) <= 100 for value in self.capabilities):
            raise AgentError("capabilities 必须是有界字符串列表")
        if self.input_roots and len([value for value in self.capabilities if value != CAPABILITY]) >= 30:
            raise AgentError("目录输入能力计入最多 30 项心跳能力，请减少额外能力")
        if (type(self.config_revision) is not int or self.config_revision < 0
                or not isinstance(self.config_hash, str)
                or (self.config_revision > 0 and not re.fullmatch(r"[a-f0-9]{64}", self.config_hash))
                or (self.config_revision == 0 and self.config_hash)):
            raise AgentError("本机配置修订号或摘要无效")
        for alias, executable in self.runtimes.items():
            if not isinstance(alias, str) or not alias or not isinstance(executable, str) or not Path(executable).is_absolute():
                raise AgentError("运行时必须映射到绝对可执行文件路径")
            if not Path(executable).is_file():
                raise AgentError(f"运行时不可用：{alias}")
        for name in ("poll_seconds", "heartbeat_seconds", "request_timeout"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.05 <= value <= 60:
                raise AgentError(f"{name} 必须在 0.05 至 60 秒之间")
        for name in ("max_download_bytes", "max_expanded_bytes", "max_log_bytes", "max_artifact_bytes"):
            if type(getattr(self, name)) is not int or not 1024 <= getattr(self, name) <= 1024 ** 3:
                raise AgentError(f"{name} 必须是有界正整数配额")

    @classmethod
    def load(cls, path: Path) -> "Config":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise AgentError("执行器配置存在未知字段或格式错误")
        for name in ("state_dir", "token_file"):
            value = Path(raw[name])
            raw[name] = (path.resolve().parent / value).resolve() if not value.is_absolute() else value
        return cls(**raw)

    def read_token(self) -> str:
        if self.token_file.is_symlink():
            raise AgentError("令牌文件不能是符号链接")
        token = self.token_file.read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token or len(token) > 4096:
            raise AgentError("节点令牌文件无效")
        return token
