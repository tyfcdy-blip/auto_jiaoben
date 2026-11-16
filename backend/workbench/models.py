"""持久实体；运行状态、投递状态和审计分别保存。"""
import time
import uuid
from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Gate(Base):
    __tablename__ = "system_gate"
    id: Mapped[int] = mapped_column(primary_key=True)
    schema_version: Mapped[int] = mapped_column(default=1)
    dispatch_enabled: Mapped[bool] = mapped_column(default=True)


class User(Entity, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(default=0)
    blocked_until: Mapped[float] = mapped_column(default=0)


class LoginSession(Entity, Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[float] = mapped_column(Float)


class Project(Entity, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Member(Entity, Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(30))


class Program(Entity, Base):
    __tablename__ = "programs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")


class Version(Entity, Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("program_id", "name"),
                      Index("ix_retention_versions_bundle_sha256", "bundle_sha256"))
    program_id: Mapped[str] = mapped_column(ForeignKey("programs.id"))
    name: Mapped[str] = mapped_column(String(100))
    command: Mapped[list] = mapped_column(JSON)
    parameter_schema: Mapped[dict] = mapped_column(JSON)
    side_effects: Mapped[bool] = mapped_column(default=False)
    result_required: Mapped[bool] = mapped_column(default=True)
    required_capabilities: Mapped[list] = mapped_column(JSON, default=list)
    protocol_version: Mapped[str] = mapped_column(String(10), default="1")
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    published: Mapped[bool] = mapped_column(default=False)


class Node(Entity, Base):
    __tablename__ = "nodes"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    slots: Mapped[int] = mapped_column(default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    maintenance: Mapped[bool] = mapped_column(default=False)
    last_seen: Mapped[float | None] = mapped_column(Float)
    isolation: Mapped[str] = mapped_column(String(30), default="development")
    agent_version: Mapped[str] = mapped_column(String(30), default="")


class NodeProjectGrant(Entity, Base):
    __tablename__ = "node_project_grants"
    __table_args__ = (UniqueConstraint("node_id", "project_id"),)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    input_roots: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_claimed_at: Mapped[float | None] = mapped_column(Float)
    updated_by: Mapped[str] = mapped_column(String(100), default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class NodeConfigState(Base):
    __tablename__ = "node_config_state"
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    desired_revision: Mapped[int] = mapped_column(default=0)
    desired_hash: Mapped[str] = mapped_column(String(64), default="")
    desired_input_roots: Mapped[list] = mapped_column(JSON, default=list)
    applied_revision: Mapped[int] = mapped_column(default=0)
    applied_hash: Mapped[str] = mapped_column(String(64), default="")
    apply_status: Mapped[str] = mapped_column(String(30), default="legacy_unmanaged")
    apply_error: Mapped[str] = mapped_column(Text, default="")
    reported_input_roots: Mapped[list] = mapped_column(JSON, default=list)
    root_status: Mapped[list] = mapped_column(JSON, default=list)
    reported_at: Mapped[float | None] = mapped_column(Float)


class Task(Entity, Base):
    __tablename__ = "tasks"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    default_parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"))
    timeout_seconds: Mapped[int] = mapped_column(default=300)
    resource_keys: Mapped[list] = mapped_column(JSON, default=list)
    business_key_fields: Mapped[list] = mapped_column(JSON, default=list)
    requires_input: Mapped[bool] = mapped_column(default=False)
    operation: Mapped[str] = mapped_column(String(50), default="run")
    enabled: Mapped[bool] = mapped_column(default=True)


class Batch(Entity, Base):
    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("project_id", "label"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    ready: Mapped[bool] = mapped_column(default=False)


class BatchFile(Entity, Base):
    __tablename__ = "batch_files"
    __table_args__ = (UniqueConstraint("batch_id", "name"),
                      Index("ix_retention_batch_files_sha256", "sha256"),
                      Index("ix_retention_batch_files_created", "created_at", "id"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"))
    name: Mapped[str] = mapped_column(String(180))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)


class Caller(Entity, Base):
    __tablename__ = "callers"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    task_ids: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(default=True)
    allow_input_upload: Mapped[bool] = mapped_column(default=False)


class Run(Entity, Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_retention_runs_input_batch_state", "input_batch_id", "state"),
        Index("ix_retention_runs_expected_input_state", "project_id", "expected_input_label", "state"),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    actor: Mapped[str] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(20))
    parameters: Mapped[dict] = mapped_column(JSON)
    input_batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"))
    expected_input_label: Mapped[str | None] = mapped_column(String(100))
    business_key: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(30), index=True, default="queued")
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    leased_at: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    pid: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSON)
    observation: Mapped[str | None] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(Text)
    artifact_state: Mapped[str] = mapped_column(String(20), default="none")
    completion_hash: Mapped[str | None] = mapped_column(String(64))
    late_evidence: Mapped[dict | None] = mapped_column(JSON)
    log_chars: Mapped[int] = mapped_column(default=0)
    logs_truncated: Mapped[bool] = mapped_column(default=False)


class TriggerEvent(Entity, Base):
    __tablename__ = "trigger_events"
    __table_args__ = (UniqueConstraint("actor", "event_id"),)
    actor: Mapped[str] = mapped_column(String(100))
    event_id: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))


class ResourceLock(Base):
    __tablename__ = "resource_locks"
    key: Mapped[str] = mapped_column(String(240), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)


class LogEntry(Entity, Base):
    __tablename__ = "logs"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)


class Artifact(Entity, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("run_id", "name"),
                      Index("ix_retention_artifacts_sha256", "sha256"),
                      Index("ix_retention_artifacts_created", "created_at", "id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    name: Mapped[str] = mapped_column(String(180))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)


class FileRetention(Base):
    """文件引用的逻辑过期记录；业务元数据保留，物理对象可独立回收。"""
    __tablename__ = "file_retention"
    __table_args__ = (Index("ix_retention_file_cleanup_queue", "deletion_state", "next_try"),)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size: Mapped[int] = mapped_column(Integer)
    expired_at: Mapped[float] = mapped_column(Float)
    deletion_state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_try: Mapped[float] = mapped_column(Float, default=0)
    deleted_at: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(String(100))


class Schedule(Entity, Base):
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_retention_schedules_input_batch", "input_batch_id"),
        Index("ix_retention_schedules_input_label", "project_id", "input_label"),
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(100))
    cron: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(50))
    parameters: Mapped[dict] = mapped_column(JSON)
    input_batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id"))
    input_label: Mapped[str | None] = mapped_column(String(100))
    misfire_policy: Mapped[str] = mapped_column(String(10))
    enabled: Mapped[bool] = mapped_column(default=False)
    next_fire: Mapped[float] = mapped_column(Float, index=True)
    revision: Mapped[int] = mapped_column(default=1)
    last_error: Mapped[str | None] = mapped_column(Text)


class Channel(Entity, Base):
    __tablename__ = "channels"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(100))
    encrypted_config: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=False)


class ChannelAlertPolicy(Base):
    __tablename__ = "channel_alert_policies"
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), primary_key=True)
    enabled_kinds: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)


class TaskResultNotificationConfig(Base):
    __tablename__ = "task_result_notification_configs"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id"))
    enabled: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(default=1)


class Alert(Entity, Base):
    __tablename__ = "alerts"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"))
    event_key: Mapped[str] = mapped_column(String(150), unique=True)
    kind: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    delivery_state: Mapped[str] = mapped_column(String(20), default="disabled")


class Delivery(Entity, Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("alert_id", "channel_id"),)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_try: Mapped[float] = mapped_column(default=time.time)
    leased_until: Mapped[float] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class ResultNotification(Entity, Base):
    __tablename__ = "result_notifications"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"))
    content: Mapped[str] = mapped_column(Text)
    delivery_state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_try: Mapped[float] = mapped_column(Float, default=time.time)
    leased_until: Mapped[float] = mapped_column(Float, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Audit(Entity, Base):
    __tablename__ = "audit"
    actor: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="")


class LoopStatus(Base):
    __tablename__ = "loop_status"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_success: Mapped[float] = mapped_column(Float)
