from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .node_file_schemas import FileOptions


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(Login):
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=100)
    is_admin: bool = False


class Active(Strict):
    active: bool


class Name(Strict):
    name: str = Field(min_length=1, max_length=100)


class MemberCreate(Strict):
    user_id: str
    role: Literal["project_admin", "developer", "operator", "viewer"]


class ProjectName(Name):
    project_id: str


class ProgramCreate(ProjectName):
    description: str = Field(default="", max_length=2000)


class VersionCreate(Name):
    command: list[str] = Field(min_length=2, max_length=30)
    parameter_schema: dict
    side_effects: bool = False
    result_required: bool = True
    required_capabilities: list[str] = Field(default_factory=list, max_length=30)
    protocol_version: Literal["1"] = "1"


class TaskCreate(ProjectName):
    file_options: FileOptions | None = None
    version_id: str
    default_parameters: dict = Field(default_factory=dict)
    node_id: str | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    resource_keys: list[str] = Field(default_factory=list, max_length=30)
    business_key_fields: list[str] = Field(default_factory=list, max_length=20)
    requires_input: bool = False
    operation: str = Field(default="run", min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")


class TaskUpdate(Name):
    file_options: FileOptions | None = None
    base_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    version_id: str
    default_parameters: dict = Field(default_factory=dict)
    node_id: str | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    resource_keys: list[str] = Field(default_factory=list, max_length=30)
    business_key_fields: list[str] = Field(default_factory=list, max_length=20)
    requires_input: bool = False
    operation: str = Field(default="run", min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9_-]+$")


class Enabled(Strict):
    enabled: bool


class BatchCreate(Strict):
    project_id: str
    label: str = Field(min_length=1, max_length=100)


class NodeCreate(ProjectName):
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    slots: int = Field(default=1, ge=1, le=16)
    input_roots: list[str] = Field(default_factory=list, max_length=30)


class NodePatch(Strict):
    enabled: bool | None = None
    maintenance: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slots: int | None = Field(default=None, ge=1, le=16)
    input_roots: list[str] | None = Field(default=None, max_length=30)
    base_revision: int | None = Field(default=None, ge=0)


class NodeProjectGrantUpdate(Strict):
    base_revision: int = Field(ge=0)
    enabled: bool = True
    input_roots: list[str] = Field(default_factory=list, max_length=30)


class NodeProjectGrantSpec(Strict):
    project_id: str
    enabled: bool = True
    input_roots: list[str] = Field(default_factory=list, max_length=30)


class NodeConfigurationUpdate(Strict):
    base_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=100)
    slots: int = Field(ge=1, le=16)
    input_roots: list[str] = Field(default_factory=list, max_length=30)
    grants: list[NodeProjectGrantSpec] = Field(min_length=1, max_length=500)


class RootStatus(Strict):
    path: str = Field(min_length=1, max_length=2000)
    status: Literal["unknown", "available", "unavailable"]
    checked_at: float | None = None


class NodeConfigAck(Strict):
    revision: int = Field(ge=1)
    config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["applied", "failed"]
    error: str = Field(default="", max_length=1000)


class CallerCreate(ProjectName):
    task_ids: list[str] = Field(min_length=1, max_length=100)
    allow_input_upload: bool = False


class RunCreate(Strict):
    file_options: FileOptions | None = None
    event_id: str = Field(min_length=1, max_length=180)
    input_batch_id: str | None = None
    parameters: dict = Field(default_factory=dict)


class Reconcile(Strict):
    outcome: Literal["failed", "succeeded", "canceled"]
    note: str = Field(min_length=10, max_length=2000)


class ScheduleCreate(Name):
    task_id: str
    cron: str = Field(min_length=9, max_length=100)
    timezone: str = "Asia/Shanghai"
    parameters: dict = Field(default_factory=dict)
    input_batch_id: str | None = None
    input_label: str | None = Field(default=None, min_length=1, max_length=100)
    misfire_policy: Literal["skip", "once"] = "skip"
    enabled: bool = False


class ScheduleUpdate(Name):
    base_revision: int = Field(ge=1)
    cron: str = Field(min_length=9, max_length=100)
    timezone: str = "Asia/Shanghai"
    parameters: dict = Field(default_factory=dict)
    input_batch_id: str | None = None
    input_label: str | None = Field(default=None, min_length=1, max_length=100)
    misfire_policy: Literal["skip", "once"] = "skip"


class Note(Strict):
    note: str = Field(min_length=1, max_length=2000)


class ChannelCreate(ProjectName):
    webhook_url: str = Field(min_length=1, max_length=2000)
    signing_secret: str | None = Field(default="", max_length=500)


class ChannelAlertPolicyUpdate(Strict):
    base_revision: int = Field(ge=0)
    enabled_kinds: list[str] = Field(max_length=8)


class TaskResultNotificationUpdate(Strict):
    base_revision: int = Field(ge=0)
    channel_id: str | None = None
    enabled: bool = False


class Heartbeat(Strict):
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    isolation: Literal["development", "os_user"]
    agent_version: str = Field(max_length=30)
    config_revision: int | None = Field(default=None, ge=0)
    config_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reported_input_roots: list[str] | None = Field(default=None, max_length=30)
    root_status: list[RootStatus] = Field(default_factory=list, max_length=30)


class Attempt(Strict):
    attempt_id: str


class Started(Attempt):
    pid: int = Field(gt=0)


class LogLine(Strict):
    sequence: int = Field(ge=1)
    stream: Literal["stdout", "stderr", "system"]
    text: str = Field(max_length=4096)


class Logs(Attempt):
    entries: list[LogLine] = Field(max_length=100)


class Completion(Attempt):
    exit_code: int | None = None
    result: dict | None = None
    error: str | None = Field(default=None, max_length=2000)
    observation: Literal["exited", "timeout", "canceled", "startup_failed", "unknown"]
    artifact_state: Literal["none", "complete", "incomplete"] = "none"
