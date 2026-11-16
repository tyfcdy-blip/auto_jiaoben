import hmac
import json
import time
from urllib.parse import urlsplit
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Request, Query, Response
from sqlalchemy import select, func
from . import schemas as S
from .security import write_lock
from .execution import ACTIVE
from .models import (Alert, Audit, Channel, ChannelAlertPolicy, Delivery, FileRetention, Gate,
                     LoopStatus, Member, Node, NodeProjectGrant, ResultNotification, Run, Schedule,
                     Task, Version)
from .notification_policy import ALERT_KINDS, DEFAULT_KINDS, alert_severity
from .notifications import send_test_message
from .security import Problem, audit, authorize, get_db, human, require
from .scheduling import next_fire, resolve_dates
from .storage import validate_parameters
from .retention import storage_health
from .input_sources import roots_are_subset, uses_directory, validate_directory_run
from .node_config import grant_for
from .views import listing, project_filter, public, run_view

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "alive", "version": "0.1.0"}


@router.get("/health/ready")
def ready(request: Request, db=Depends(get_db, scope="function")):
    gate = db.get(Gate, 1)
    if not gate or gate.schema_version != 1:
        raise Problem(503, "not_ready", "数据库未就绪")
    directory = request.app.state.settings.data_dir
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / (".probe-" + request.state.request_id)
    try:
        with probe.open("xb") as stream:
            stream.write(b"1")
    finally:
        probe.unlink(missing_ok=True)
    return {"status": "ready", "version": "0.1.0"}


@router.get("/health/components")
def components(request: Request, db=Depends(get_db, scope="function")):
    secret = request.app.state.settings.monitor_token
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secret or not hmac.compare_digest(supplied, secret):
        raise Problem(401, "unauthorized", "需要监控凭据")
    values = {row.name: row.last_success for row in db.scalars(select(LoopStatus))}
    now = time.time()
    healthy = (all(now-values.get(key, 0) < 60 for key in ("scheduler", "reconciler", "notifier"))
               and now-values.get("storage_cleanup", 0)
               < request.app.state.settings.cleanup_interval_seconds + 60)
    if not healthy:
        raise Problem(503, "component_stale", "后台循环未就绪或停止推进")
    storage = storage_health(request.app.state.settings)
    cleanup_counts = dict(db.execute(select(
        FileRetention.deletion_state, func.count(),
    ).group_by(FileRetention.deletion_state)).all())
    cleanup_counts["persistent_failures"] = db.scalar(select(func.count()).select_from(
        FileRetention).where(FileRetention.deletion_state == "failed", FileRetention.attempts >= 3)) or 0
    storage["cleanup"] = cleanup_counts
    if not storage["healthy"]:
        raise Problem(503, "storage_pressure", "平台文件容量已达到保护阈值")
    if storage["warning"]:
        raise Problem(503, "storage_warning", "平台文件容量已达到预警阈值")
    if cleanup_counts["persistent_failures"]:
        raise Problem(503, "storage_cleanup_failed", "平台文件持续清理失败，请检查存储权限")
    return {"status": "healthy", "loops": values, "storage": storage}


@router.get("/overview")
def overview(request: Request, project_id: str | None = None, who=Depends(human), db=Depends(get_db, scope="function")):
    def count(model, *conditions):
        query = project_filter(db, who, select(model.id).where(*conditions), model, project_id)
        return db.scalar(select(func.count()).select_from(query.subquery()))
    node_query = select(Node.id).join(NodeProjectGrant, NodeProjectGrant.node_id == Node.id).where(
        NodeProjectGrant.enabled.is_(True), Node.enabled.is_(True),
        Node.last_seen > time.time()-request.app.state.settings.node_timeout)
    if project_id:
        authorize(db, who, project_id)
        node_query = node_query.where(NodeProjectGrant.project_id == project_id)
    elif not who.record.is_admin:
        node_query = node_query.join(Member, Member.project_id == NodeProjectGrant.project_id).where(
            Member.user_id == who.record.id)
    online_nodes = db.scalar(select(func.count()).select_from(node_query.distinct().subquery()))
    query = project_filter(db, who, select(Run).order_by(Run.created_at.desc()), Run, project_id).limit(8)
    return {"total_tasks": count(Task), "active_runs": count(Run, Run.state.in_(ACTIVE)),
            "queued_runs": count(Run, Run.state.in_(["queued", "waiting_input"])),
            "failed_runs": count(Run, Run.state.in_(["failed", "partial", "unknown"])),
            "online_nodes": online_nodes,
            "open_alerts": count(Alert, Alert.status == "open"),
            "recent_runs": [run_view(db, item) for item in db.scalars(query)]}


@router.get("/schedules")
def schedules(project_id: str | None = None, task_id: str | None = None,
              q: str | None = Query(None, max_length=100),
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              who=Depends(human), db=Depends(get_db, scope="function")):
    query = project_filter(
        db, who, select(Schedule).order_by(Schedule.created_at.desc(), Schedule.id.desc()), Schedule, project_id
    )
    if task_id:
        query = query.where(Schedule.task_id == task_id)
    if q and q.strip():
        query = query.where(Schedule.name.contains(q.strip(), autoescape=True))
    return listing(db, query, limit, offset)


@router.get("/schedules/{schedule_id}")
def schedule_detail(schedule_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    schedule = require(db, Schedule, schedule_id)
    authorize(db, who, schedule.project_id)
    return public(schedule)


def validated_schedule_values(db, task, payload, scheduled):
    resolved = resolve_dates({**task.default_parameters, **payload.parameters}, scheduled, payload.timezone)
    schema = db.get(Version, task.version_id).parameter_schema
    validate_parameters(schema, resolved)
    validate_directory_run(schema, task, resolved, payload.input_batch_id, payload.input_label)
    if uses_directory(schema):
        grant = grant_for(db, task.node_id, task.project_id)
        if not grant or not roots_are_subset([resolved["input_dir"]], grant.input_roots):
            raise Problem(422, "directory_outside_project_grant",
                          "计划中的文件夹不在该项目获准使用的电脑目录内")
    if payload.input_batch_id and payload.input_label:
        raise Problem(422, "ambiguous_input", "固定批次和按标签等待只能选择一种")
    if payload.input_label:
        resolve_dates(payload.input_label, scheduled, payload.timezone)
    if payload.input_batch_id:
        from .models import Batch
        batch = require(db, Batch, payload.input_batch_id)
        if batch.project_id != task.project_id:
            raise Problem(422, "invalid_batch", "输入批次不属于任务项目")
    elif task.requires_input and not payload.input_label:
        raise Problem(422, "missing_batch", "计划需要固定批次或明确的输入标签")
    return payload.model_dump(exclude={"task_id", "enabled", "base_revision"})


@router.post("/schedules", status_code=201)
def create_schedule(payload: S.ScheduleCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, payload.task_id)
    authorize(db, who, task.project_id, "operator")
    scheduled = next_fire(payload.cron, payload.timezone, time.time())
    values = validated_schedule_values(db, task, payload, scheduled)
    schedule = Schedule(task_id=task.id, project_id=task.project_id, next_fire=scheduled,
                        enabled=payload.enabled, **values)
    db.add(schedule)
    db.flush()
    audit(db, who, "create_schedule", schedule.id, task.project_id)
    return public(schedule)


@router.patch("/schedules/{schedule_id}")
def toggle_schedule(schedule_id: str, payload: S.Enabled, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    schedule = require(db, Schedule, schedule_id)
    authorize(db, who, schedule.project_id, "operator")
    if payload.enabled != schedule.enabled:
        if payload.enabled:
            scheduled = next_fire(schedule.cron, schedule.timezone, time.time())
            task = require(db, Task, schedule.task_id)
            current = S.ScheduleUpdate(
                base_revision=schedule.revision,
                name=schedule.name,
                cron=schedule.cron,
                timezone=schedule.timezone,
                parameters=schedule.parameters,
                input_batch_id=schedule.input_batch_id,
                input_label=schedule.input_label,
                misfire_policy=schedule.misfire_policy,
            )
            validated_schedule_values(db, task, current, scheduled)
            schedule.next_fire = scheduled
        schedule.revision += 1
        schedule.enabled = payload.enabled
    audit(db, who, "toggle_schedule", schedule.id, schedule.project_id)
    return public(schedule)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: S.ScheduleUpdate,
                    who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    schedule = require(db, Schedule, schedule_id)
    authorize(db, who, schedule.project_id, "operator")
    if payload.base_revision != schedule.revision:
        raise Problem(409, "stale_config", "定时计划已被其他人修改，请刷新后重试")
    task = require(db, Task, schedule.task_id)
    scheduled = next_fire(payload.cron, payload.timezone, time.time())
    values = validated_schedule_values(db, task, payload, scheduled)
    for field, value in values.items():
        setattr(schedule, field, value)
    schedule.next_fire = scheduled
    schedule.revision += 1
    schedule.last_error = None
    audit(db, who, "update_schedule", schedule.id, schedule.project_id)
    return public(schedule)


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    schedule = require(db, Schedule, schedule_id)
    authorize(db, who, schedule.project_id, "operator")
    audit(db, who, "delete_schedule", schedule.id, schedule.project_id)
    db.delete(schedule)
    return Response(status_code=204)


@router.get("/alerts")
def alerts(project_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           who=Depends(human), db=Depends(get_db, scope="function")):
    query = project_filter(db, who, select(Alert).order_by(Alert.created_at.desc()), Alert, project_id)
    return listing(db, query, limit, offset,
                   transform=lambda record: {**public(record), "severity": alert_severity(record.kind)})


@router.get("/result-notifications")
def result_notifications(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0), who=Depends(human),
                         db=Depends(get_db, scope="function")):
    query = select(ResultNotification, Channel.name).join(
        Channel, Channel.id == ResultNotification.channel_id,
    ).order_by(ResultNotification.created_at.desc())
    query = project_filter(db, who, query, ResultNotification, project_id)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.execute(query.limit(limit).offset(offset)).all()
    return {"items": [{**public(record), "channel_name": channel_name}
                      for record, channel_name in rows], "total": total}


@router.get("/alert-deliveries")
def alert_deliveries(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0), who=Depends(human),
                     db=Depends(get_db, scope="function")):
    query = select(Delivery, Alert.project_id, Channel.name).join(
        Alert, Alert.id == Delivery.alert_id,
    ).join(Channel, Channel.id == Delivery.channel_id).order_by(Delivery.created_at.desc())
    query = project_filter(db, who, query, Alert, project_id)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.execute(query.limit(limit).offset(offset)).all()
    return {"items": [{**public(delivery), "project_id": owner_project_id, "channel_name": channel_name}
                      for delivery, owner_project_id, channel_name in rows], "total": total}


@router.post("/alerts/{alert_id}/ack")
def acknowledge(alert_id: str, payload: S.Note, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    record = require(db, Alert, alert_id)
    authorize(db, who, record.project_id, "operator")
    if record.status == "acknowledged":
        return public(record)
    if record.status != "open":
        raise Problem(409, "invalid_state", "只有待处理告警可确认")
    record.status = "acknowledged"
    audit(db, who, "acknowledge_alert", record.id, record.project_id, payload.note)
    return public(record)


@router.post("/alerts/{alert_id}/close")
def close_alert(alert_id: str, payload: S.Note, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    record = require(db, Alert, alert_id)
    authorize(db, who, record.project_id, "operator")
    if record.status == "closed":
        return public(record)
    if record.status not in {"open", "acknowledged", "recovered"}:
        raise Problem(409, "invalid_state", "告警状态不允许关闭")
    record.status = "closed"
    audit(db, who, "close_alert", record.id, record.project_id, payload.note)
    return public(record)


@router.get("/channels")
def channels(project_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
             who=Depends(human), db=Depends(get_db, scope="function")):
    query = project_filter(db, who, select(Channel).order_by(Channel.created_at.desc()), Channel, project_id)
    return listing(db, query, limit, offset)


@router.post("/channels", status_code=201)
def create_channel(payload: S.ChannelCreate, request: Request, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    authorize(db, who, payload.project_id, "project_admin")
    try:
        parsed = urlsplit(payload.webhook_url)
        valid = (parsed.scheme == "https" and parsed.hostname == "oapi.dingtalk.com" and parsed.port in {None, 443}
                 and not parsed.username and not parsed.fragment and parsed.path == "/robot/send")
    except ValueError:
        valid = False
    if not valid:
        raise Problem(422, "invalid_channel", "只接受钉钉群机器人 HTTPS 地址")
    key = request.app.state.settings.secret_key
    if not key:
        raise Problem(409, "missing_encryption_key", "平台尚未配置凭据加密密钥")
    encrypted = Fernet(key.encode()).encrypt(json.dumps({"url": payload.webhook_url,
                                                       "secret": payload.signing_secret or ""}).encode()).decode()
    channel = Channel(project_id=payload.project_id, name=payload.name, encrypted_config=encrypted, enabled=False)
    db.add(channel)
    db.flush()
    db.add(ChannelAlertPolicy(channel_id=channel.id, enabled_kinds=list(DEFAULT_KINDS)))
    audit(db, who, "create_channel", channel.id, channel.project_id)
    return public(channel)


@router.patch("/channels/{channel_id}")
def toggle_channel(channel_id: str, payload: S.Enabled, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    channel = require(db, Channel, channel_id)
    authorize(db, who, channel.project_id, "project_admin")
    channel.enabled = payload.enabled
    audit(db, who, "toggle_channel", channel.id, channel.project_id)
    return public(channel)


@router.post("/channels/{channel_id}/test-message")
def test_channel_message(channel_id: str, request: Request, who=Depends(human),
                         db=Depends(get_db, scope="function")):
    write_lock(db, who)
    channel = require(db, Channel, channel_id)
    authorize(db, who, channel.project_id, "project_admin")
    encrypted_config, project_id, actor = channel.encrypted_config, channel.project_id, who.actor
    test_id = request.state.request_id
    audit(db, who, "test_channel_requested", channel_id, project_id, f"test_id={test_id}")
    # 对外请求不占用数据库事务；请求审计先落盘，响应结果在另一事务记录。
    db.commit()
    result = send_test_message(encrypted_config, request.app.state.settings, test_id)
    note = f"test_id={test_id} reason={result.reason or 'accepted'}"
    if result.status_code is not None:
        note += f" http_status={result.status_code}"
    if result.dingtalk_code is not None:
        note += f" dingtalk_code={result.dingtalk_code}"
    with request.app.state.factory.begin() as audit_db:
        audit(audit_db, actor, "test_channel_result", channel_id, project_id, note)
    if result.sent:
        return {"sent": True, "test_id": test_id}
    if result.reason == "rejected":
        code = f"（错误码 {result.dingtalk_code}）" if result.dingtalk_code is not None else ""
        message = f"钉钉拒绝测试消息{code}；请检查机器人安全设置、关键词和加签"
    elif result.reason == "http_error":
        message = f"钉钉接口返回 HTTP {result.status_code}；请检查机器人地址与安全设置"
    else:
        message = "测试请求未获钉钉成功确认，送达情况未知；请先到群里核对再重试"
    raise Problem(502, "channel_test_failed", message)


@router.get("/channels/{channel_id}/policy")
def channel_alert_policy(channel_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    channel = require(db, Channel, channel_id)
    authorize(db, who, channel.project_id)
    policy = db.get(ChannelAlertPolicy, channel.id)
    return {"channel_id": channel.id, "enabled_kinds": list(policy.enabled_kinds) if policy else list(ALERT_KINDS),
            "legacy_all": policy is None, "revision": policy.revision if policy else 0}


@router.put("/channels/{channel_id}/policy")
def update_channel_alert_policy(channel_id: str, payload: S.ChannelAlertPolicyUpdate,
                                who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    channel = require(db, Channel, channel_id)
    authorize(db, who, channel.project_id, "project_admin")
    kinds = payload.enabled_kinds
    if len(set(kinds)) != len(kinds) or any(kind not in ALERT_KINDS for kind in kinds):
        raise Problem(422, "invalid_alert_policy", "告警类型无效或重复")
    policy = db.get(ChannelAlertPolicy, channel.id)
    revision = policy.revision if policy else 0
    if revision != payload.base_revision:
        raise Problem(409, "stale_policy", "通知规则已被其他人修改，请刷新后重试")
    if policy:
        policy.enabled_kinds = kinds
        policy.revision += 1
    else:
        policy = ChannelAlertPolicy(channel_id=channel.id, enabled_kinds=kinds)
        db.add(policy)
    db.flush()
    audit(db, who, "update_channel_alert_policy", channel.id, channel.project_id)
    return {"channel_id": channel.id, "enabled_kinds": list(policy.enabled_kinds),
            "legacy_all": False, "revision": policy.revision}


@router.get("/audit")
def audits(project_id: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
           who=Depends(human), db=Depends(get_db, scope="function")):
    if not who.record.is_admin:
        if not project_id:
            raise Problem(403, "forbidden", "项目审计需要指定项目")
        authorize(db, who, project_id, "project_admin")
    query = project_filter(db, who, select(Audit).order_by(Audit.created_at.desc()), Audit, project_id)
    return listing(db, query, limit, offset)
