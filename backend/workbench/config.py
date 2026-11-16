"""平台配置：秘密只从运行环境或专用文件加载。"""
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WB_", extra="ignore", hide_input_in_errors=True)
    env: str = "development"
    database_url: str = "sqlite:///./runtime/workbench.db"
    database_url_file: str | None = None
    database_connect_attempts: int = Field(default=30, ge=1, le=300)
    database_connect_interval_seconds: float = Field(default=2.0, ge=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=120)
    data_dir: Path = Path("runtime/storage")
    secret_key: str = ""
    secret_key_file: str | None = None
    monitor_token: str = ""
    monitor_token_file: str | None = None
    base_path: str = ""
    public_base_url: str = ""
    session_seconds: int = Field(default=28800, ge=300, le=86400)
    node_timeout: int = Field(default=90, ge=15)
    queue_timeout: int = Field(default=3600, ge=10)
    input_timeout: int = Field(default=3600, ge=10)
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_batch_files: int = Field(default=200, ge=1, le=10000)
    max_batch_bytes: int = Field(default=500 * 1024 * 1024, ge=1024)
    max_project_input_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=1024)
    max_project_open_batches: int = Field(default=200, ge=1)
    max_project_program_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=1024)
    max_queue: int = Field(default=1000, ge=1)
    max_log_chars: int = Field(default=2_000_000, ge=1000)
    dispatch_enabled: bool = True
    allow_side_effects: bool = False

    @model_validator(mode="after")
    def validate_runtime(self):
        for name in ("database_url", "secret_key", "monitor_token"):
            filename = getattr(self, name + "_file")
            if filename:
                setattr(self, name, Path(filename).read_text(encoding="utf-8").strip())
        if self.env not in {"development", "test", "production"}:
            raise ValueError("WB_ENV 无效")
        if self.allow_side_effects:
            raise ValueError("正式副作用执行尚未完成全局资源、业务确认和独立身份验收，当前版本不能启用")
        if (self.max_batch_bytes < self.max_upload_bytes
                or self.max_project_input_bytes < self.max_batch_bytes
                or self.max_project_program_bytes < self.max_upload_bytes):
            raise ValueError("容量限制必须满足单文件 ≤ 单批次/项目程序包 ≤ 对应项目配额")
        if self.secret_key:
            try:
                Fernet(self.secret_key.encode())
            except (ValueError, TypeError):
                raise ValueError("凭据加密密钥格式无效") from None
        if self.env == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("生产环境必须使用 PostgreSQL")
            if not self.secret_key or len(self.monitor_token) < 32:
                raise ValueError("生产环境必须配置独立秘密和监控令牌")
        if self.base_path and (not self.base_path.startswith("/") or self.base_path.endswith("/")
                               or ".." in self.base_path or "?" in self.base_path):
            raise ValueError("WB_BASE_PATH 必须为空或无尾斜线的绝对路径前缀")
        if self.public_base_url:
            if any(char.isspace() for char in self.public_base_url):
                raise ValueError("WB_PUBLIC_BASE_URL 必须是工作台的 HTTPS 站点地址")
            parts = urlsplit(self.public_base_url)
            try:
                valid_port = parts.port is None or 1 <= parts.port <= 65535
            except ValueError:
                valid_port = False
            local_http = (self.env != "production" and parts.scheme == "http"
                          and parts.hostname in {"localhost", "127.0.0.1", "::1"})
            if (not parts.hostname or not valid_port or parts.scheme != "https" and not local_http
                    or parts.username or parts.password or parts.path not in {"", "/"}
                    or parts.query or parts.fragment):
                raise ValueError("WB_PUBLIC_BASE_URL 必须是站点 HTTPS 根地址；本地开发可用本机 HTTP")
            self.public_base_url = self.public_base_url.rstrip("/")
        return self
