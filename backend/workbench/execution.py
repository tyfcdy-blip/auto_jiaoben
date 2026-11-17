"""统一受理、持久领取和终态核对。调用方须在同一事务持有 system_gate。"""
import time
from sqlalchemy import select, func, delete, or_
from .models import (Alert, Artifact, Batch, Caller, Channel, ChannelAlertPolicy, Delivery, Gate, Member,
                     Node, NodeConfigState, NodeProjectGrant, ResourceLock, Run,
                     ResultNotification, Task, TaskResultNotificationConfig, TriggerEvent, User, Version, uid)
from .redaction import redact
from .security import Problem, authorize
from .storage import canonical, content_hash, validate_parameters
from .input_sources import (DIRECTORY_CAPABILITY, roots_are_subset, uses_directory,
                            validate_directory_run)
from .node_config import CONFIG_CAPABILITY, active_grants, configuration_ready, grant_for
from .retention import batch_has_expired_files
from .node_files import task_options, validate_options, run_file_payload, file_fields
from .models import RunFileOptions

ACTIVE = {"leased", "running", "cancel_requested", "unknown"}
TERMINAL = {"succeeded", "failed", "partial", "canceled"}


def node_dispatch_status(db, settings, node, *, state=None, active_runs=None) -> dict:
    """给人看的电脑接单状态；在线只表示心跳正常，不能代表一定能领取任务。"""
    if active_runs is None:
        active_runs = db.scalar(select(func.count()).select_from(Run).where(
            Run.node_id == node.id, Run.state.in_(ACTIVE))) or 0
    online = bool(node.last_seen and time.time() - node.last_seen <= settings.node_timeout)
    if state is None:
        state = db.get(NodeConfigState, node.id)
    if not node.enabled:
        code, detail = "disabled", "电脑已停用，不会领取新任务"
    elif node.maintenance:
        code, detail = "maintenance", "电脑处于维护状态，不会领取新任务"
    elif not online:
        code, detail = "offline", "执行器心跳已超时"
    elif not node.agent_version.startswith("0.1."):
        code, detail = "agent_unsupported", "执行器版本不受当前工作台支持"
    elif state and state.desired_revision > 0 and not configuration_ready(state):
        if state.apply_status == "failed":
            code, detail = "config_failed", "配置同步失败，请由平台管理员查看详情"
        elif CONFIG_CAPABILITY not in node.capabilities:
            code, detail = "config_pending", "执行器版本过旧，不能同步平台配置；受管任务暂停"
        else:
            code, detail = "config_pending", "等待执行器同步平台配置；受管任务暂停"
    elif active_runs >= node.slots:
        code, detail = "busy", f"正在运行 {active_runs}/{node.slots} 个任务"
    elif not state or state.desired_revision <= 0 or CONFIG_CAPABILITY not in node.capabilities:
        code, detail = "legacy_unmanaged", "旧版执行器仅支持所属项目的普通任务"
    else:
        code, detail = "idle", f"空闲，可接 {max(0, node.slots-active_runs)} 个任务"
    return {"dispatch_status": code, "dispatch_detail": detail,
            "capacity_status": "busy" if active_runs >= node.slots else "idle",
            "active_runs": active_runs, "available_slots": max(0, node.slots-active_runs)}


def _configuration_blocker(state, node) -> tuple[str, str]:
    if state and state.apply_status == "failed":
        return "config_failed", "配置同步失败，请联系平台管理员"
    if state and state.desired_revision > 0:
        versions = f"电脑 v{state.applied_revision}，平台 v{state.desired_revision}"
        if CONFIG_CAPABILITY not in node.capabilities:
            return "config_pending", f"执行器版本过旧，不能同步平台配置（{versions}）"
        return "config_pending", f"等待执行电脑同步平台配置（{versions}）"
    return "legacy_unmanaged", "执行器尚未接入平台配置管理"


def queued_run_reason(db, settings, run, cache: dict | None = None) -> dict | None:
    """解释运行为什么仍在队列中，不改变调度结果。"""
    if run.state == "waiting_input":
        return {"code": "waiting_input", "message": "等待输入文件批次就绪"}
    if run.state != "queued":
        return None
    cache = cache if cache is not None else {}
    task_cache, version_cache = cache.setdefault("tasks", {}), cache.setdefault("versions", {})
    if run.task_id not in task_cache:
        task_cache[run.task_id] = db.get(Task, run.task_id)
    if run.version_id not in version_cache:
        version_cache[run.version_id] = db.get(Version, run.version_id)
    task, version = task_cache[run.task_id], version_cache[run.version_id]
    if "gate" not in cache:
        cache["gate"] = db.get(Gate, 1)
    gate = cache["gate"]
    if not settings.dispatch_enabled or not gate or not gate.dispatch_enabled:
        return {"code": "dispatch_paused", "message": "平台派发已暂停"}
    if not task.enabled:
        return {"code": "task_disabled", "message": "任务已停用，不能开始新的运行"}
    if not version.published:
        return {"code": "version_unavailable", "message": "任务版本当前不可用"}
    authorization_cache = cache.setdefault("authorization", {})
    authorization_key = (run.actor, run.project_id, run.task_id)
    if authorization_key not in authorization_cache:
        authorization_cache[authorization_key] = still_authorized(db, run)
    if not authorization_cache[authorization_key]:
        return {"code": "authorization_expired", "message": "触发身份已失效或不再具有运行权限"}
    try:
        run_file_payload(db, run)
    except Problem as problem:
        return {"code": problem.code, "message": problem.message}
    candidate_key = (run.project_id, task.node_id)
    candidate_cache = cache.setdefault("candidates", {})
    if candidate_key not in candidate_cache:
        query = select(Node, NodeProjectGrant).join(
            NodeProjectGrant, NodeProjectGrant.node_id == Node.id).where(
                NodeProjectGrant.project_id == run.project_id,
                NodeProjectGrant.enabled.is_(True),
            )
        if task.node_id:
            query = query.where(Node.id == task.node_id)
        candidate_cache[candidate_key] = db.execute(
            query.order_by(Node.created_at, Node.id)).all()
    candidates = candidate_cache[candidate_key]
    if not candidates:
        message = ("任务指定的执行电脑未授权给当前项目" if task.node_id
                   else "当前项目没有可用的执行电脑授权")
        return {"code": "no_authorized_node", "message": message}
    blockers: list[tuple[str, str]] = []
    for node, grant in candidates:
        if not node.enabled:
            blockers.append(("node_disabled", f"执行电脑“{node.name}”已停用"))
            continue
        if node.maintenance:
            blockers.append(("node_maintenance", f"执行电脑“{node.name}”正在维护"))
            continue
        if not node.last_seen or time.time() - node.last_seen > settings.node_timeout:
            blockers.append(("node_offline", f"执行电脑“{node.name}”离线"))
            continue
        if not node.agent_version.startswith("0.1."):
            blockers.append(("agent_unsupported", f"执行电脑“{node.name}”版本不受支持"))
            continue
        occupied_cache = cache.setdefault("occupied", {})
        if node.id not in occupied_cache:
            occupied_cache[node.id] = db.scalar(select(func.count()).select_from(Run).where(
                Run.node_id == node.id, Run.state.in_(ACTIVE))) or 0
        occupied = occupied_cache[node.id]
        if occupied >= node.slots:
            blockers.append(("node_busy", f"执行电脑“{node.name}”繁忙（{occupied}/{node.slots}）"))
            continue
        state_cache = cache.setdefault("states", {})
        if node.id not in state_cache:
            state_cache[node.id] = db.get(NodeConfigState, node.id)
        state = state_cache[node.id]
        managed = bool(state and configuration_ready(state)
                       and CONFIG_CAPABILITY in node.capabilities)
        directory = uses_directory(version.parameter_schema)
        if (grant.project_id != node.project_id or directory) and not managed:
            code, message = _configuration_blocker(state, node)
            blockers.append((code, f"执行电脑“{node.name}”{message}"))
            continue
        if directory and DIRECTORY_CAPABILITY not in node.capabilities:
            blockers.append(("directory_capability_missing",
                             f"执行电脑“{node.name}”不支持文件夹输入"))
            continue
        if directory and not grant.input_roots:
            blockers.append(("directory_grant_missing",
                             f"当前项目尚未配置执行电脑“{node.name}”的可读目录"))
            continue
        missing = sorted(set(version.required_capabilities) - set(node.capabilities))
        if missing:
            blockers.append(("capability_mismatch",
                             f"执行电脑“{node.name}”缺少运行能力：{'、'.join(missing)}"))
            continue
        if version.side_effects and (node.isolation != "os_user" or not settings.allow_side_effects):
            blockers.append(("side_effects_blocked", "外部写入任务未通过执行环境门禁"))
            continue
        keys = [f"{run.project_id}:{key}" for key in task.resource_keys]
        resource_cache = cache.setdefault("resources", {})
        resource_key = tuple(keys)
        if keys and resource_key not in resource_cache:
            resource_cache[resource_key] = bool(db.scalar(
                select(ResourceLock.key).where(ResourceLock.key.in_(keys))))
        if keys and resource_cache[resource_key]:
            blockers.append(("resource_busy", "任务所需的共享资源正在被其他运行占用"))
            continue
        return {"code": "waiting_for_poll", "message": f"执行电脑“{node.name}”空闲，等待下次轮询领取"}
    if len(blockers) == 1:
        return {"code": blockers[0][0], "message": blockers[0][1]}
    unique = list(dict.fromkeys(message for _, message in blockers))
    return {"code": "no_eligible_node", "message": "；".join(unique[:3])}


def trigger_authorized(db, who, task):
    if who.kind == "caller":
        if who.record.project_id != task.project_id or task.id not in who.record.task_ids:
            raise Problem(404, "not_found", "资源不存在或无权访问")
    elif who.kind == "user":
        authorize(db, who, task.project_id, "operator")
    else:
        raise Problem(403, "forbidden", "节点身份不能触发任务")


def admit(db, settings, task, actor, event_id, parameters, batch_id, source, expected_input_label=None,
          file_options=None):
    try:
        request_hash = content_hash({"task_id": task.id, "parameters": parameters, "batch_id": batch_id,
                                     "expected_input_label": expected_input_label,
                                     **({"file_options": file_options} if file_options is not None else {})})
    except (ValueError, TypeError):
        raise Problem(422, "invalid_parameters", "参数必须是标准 JSON，不能包含非有限数字") from None
    event = db.scalar(select(TriggerEvent).where(TriggerEvent.actor == actor, TriggerEvent.event_id == event_id))
    if event:
        if event.request_hash != request_hash:
            raise Problem(409, "event_conflict", "同一事件编号的内容不一致")
        return db.get(Run, event.run_id), True
    if not task.enabled:
        raise Problem(409, "task_disabled", "任务已暂停")
    version = db.get(Version, task.version_id)
    if not version.published:
        raise Problem(409, "version_disabled", "任务版本不可用")
    if version.side_effects and not settings.allow_side_effects:
        raise Problem(409, "side_effects_disabled", "外部写入尚未通过授权门禁")
    resolved = {**task.default_parameters, **parameters}
    defaults_files = task_options(db, task.id)
    selected = defaults_files if file_options is None else file_options
    if file_options is not None:
        # 本次选择是完整覆盖；清除选择不能又从任务默认参数悄悄恢复旧文件路径。
        for name in defaults_files["inputs"]:
            resolved.pop(name, None)
    declared_files = file_fields(version.parameter_schema)
    selections = dict(selected.get("inputs", {}))
    if file_options is None:
        selections.update({name: parameters[name] for name in declared_files if name in parameters})
    resolved.update(selections)
    # 外部系统可直接传普通业务参数；带文件注解的值仍必须经过同一授权边界。
    selections.update({name: resolved[name] for name in declared_files if name in resolved})
    selected = {"inputs": selections, "output_dir": selected.get("output_dir")}
    selected, file_revision, output_revision = validate_options(db, task, selected)
    resolved.update(selected["inputs"])
    validate_parameters(version.parameter_schema, resolved)
    validate_directory_run(version.parameter_schema, task, resolved, batch_id, expected_input_label)
    if uses_directory(version.parameter_schema):
        grant = grant_for(db, task.node_id, task.project_id)
        if not grant or not roots_are_subset([resolved["input_dir"]], grant.input_roots):
            raise Problem(422, "directory_outside_project_grant",
                          "输入文件夹不在该项目获准使用的电脑目录内")
    batch = None
    if batch_id:
        batch = db.get(Batch, batch_id)
        if not batch or batch.project_id != task.project_id:
            raise Problem(422, "invalid_batch", "输入批次不存在或不属于该项目")
    elif expected_input_label:
        batch = db.scalar(select(Batch).where(Batch.project_id == task.project_id, Batch.label == expected_input_label))
        if batch:
            batch_id = batch.id
    elif task.requires_input:
        raise Problem(422, "missing_batch", "任务需要明确输入批次")
    if batch and batch_has_expired_files(db, batch.id):
        raise Problem(410, "file_expired", "输入批次已超过保留期限，请上传新批次")
    fields = task.business_key_fields
    if any(field not in resolved for field in fields):
        raise Problem(422, "missing_business_key", "业务去重字段必须有值")
    business = {"project_id": task.project_id, "program_id": version.program_id, "operation": task.operation}
    if source == "manual":
        # 每次人工点击都代表明确的新执行意图；event_id 只用于同一次提交的网络重试幂等。
        business.update(actor=actor, event_id=event_id, task_id=task.id)
    elif batch_id or expected_input_label or fields:
        business.update(input_label=batch.label if batch else expected_input_label,
                        fields={field: resolved[field] for field in fields})
    else:
        # 无业务键的无文件任务按事件去重，不能把每日无参健康检查永久合并成一次。
        business.update(actor=actor, event_id=event_id, task_id=task.id)
    business_key = content_hash(business)
    existing = db.scalar(select(Run).where(Run.business_key == business_key))
    if existing and (existing.parameters != resolved or existing.version_id != version.id):
        raise Problem(409, "business_conflict", "业务批次已受理，参数或版本不同，需要核对或修订批次")
    if existing:
        previous_files = db.get(RunFileOptions, existing.id)
        previous_options = previous_files.options if previous_files else {"inputs": {}, "output_dir": None}
        if previous_options != selected:
            raise Problem(409, "business_conflict", "业务批次已受理，输入或结果目录不同，需要核对批次")
    if existing:
        run = existing
    else:
        queued = db.scalar(select(func.count()).select_from(Run).where(Run.state.in_(["queued", "waiting_input"])))
        if queued >= settings.max_queue:
            raise Problem(429, "queue_full", "受理容量已满，请保留原事件编号稍后重试")
        run = Run(project_id=task.project_id, task_id=task.id, version_id=version.id, actor=actor,
                  source=source, parameters=resolved, input_batch_id=batch_id, business_key=business_key,
                  expected_input_label=expected_input_label,
                  state="waiting_input" if (batch and not batch.ready) or (expected_input_label and not batch) else "queued")
        db.add(run)
        db.flush()
        if selected["inputs"] or selected["output_dir"]:
            db.add(RunFileOptions(run_id=run.id, node_id=task.node_id, options=selected,
                                 config_revision=file_revision, output_config_revision=output_revision,
                                 save_state="pending" if selected["output_dir"] else "not_requested"))
    db.add(TriggerEvent(actor=actor, event_id=event_id, request_hash=request_hash, run_id=run.id))
    db.flush()
    return run, bool(existing)


def alert(db, project_id, kind, message, event_key, run_id=None, node_id=None):
    existing = db.scalar(select(Alert).where(Alert.event_key == event_key))
    if existing:
        return existing
    item = Alert(project_id=project_id, kind=kind, message=redact(message)[:2000], event_key=event_key,
                 run_id=run_id, node_id=node_id)
    db.add(item)
    db.flush()
    channels = db.scalars(select(Channel).where(Channel.project_id == project_id, Channel.enabled.is_(True))).all()
    if kind == "recovery" and event_key.startswith("recovery:"):
        original_id = event_key.removeprefix("recovery:")
        original_channels = set(db.scalars(select(Delivery.channel_id).where(
            Delivery.alert_id == original_id,
        )))
        channels = [channel for channel in channels if channel.id in original_channels]
    else:
        channel_ids = [channel.id for channel in channels]
        policies = {policy.channel_id: policy for policy in db.scalars(select(ChannelAlertPolicy).where(
            ChannelAlertPolicy.channel_id.in_(channel_ids),
        ))} if channel_ids else {}
        channels = [channel for channel in channels if (
            channel.id not in policies or kind in policies[channel.id].enabled_kinds
        )]
    if channels:
        item.delivery_state = "pending"
        for channel in channels:
            db.add(Delivery(alert_id=item.id, channel_id=channel.id))
    elif db.scalar(select(Channel.id).where(Channel.project_id == project_id, Channel.enabled.is_(True)).limit(1)):
        item.delivery_state = "filtered"
    return item


def release(db, run):
    db.execute(delete(ResourceLock).where(ResourceLock.run_id == run.id))


def recover_alert(db, record, message):
    """恢复是系统证据，不能替代人员确认；恢复通知有独立事件和投递记录。"""
    if record.status not in {"open", "acknowledged"}:
        return
    record.status = "recovered"
    recovered = alert(db, record.project_id, "recovery", message, "recovery:" + record.id,
                      run_id=record.run_id, node_id=record.node_id)
    recovered.status = "recovered"


def fail_without_start(db, run, reason):
    run.state, run.error, run.finished_at = "failed", reason, time.time()
    alert(db, run.project_id, "start_rejected", reason, "run:"+run.id, run_id=run.id)


def still_authorized(db, run):
    kind, identifier = run.actor.split(":", 1)
    if kind == "schedule":
        return True  # 计划以项目服务身份运行；其启停另受项目授权约束。
    if kind == "caller":
        caller = db.get(Caller, identifier)
        return bool(caller and caller.active and run.task_id in caller.task_ids)
    user = db.get(User, identifier)
    if not user or not user.active:
        return False
    if user.is_admin:
        return True
    member = db.scalar(select(Member).where(Member.user_id == user.id, Member.project_id == run.project_id))
    return bool(member and member.role in {"operator", "developer", "project_admin"})


def node_grant_authorized(db, node_id: str, project_id: str) -> bool:
    return db.scalar(select(NodeProjectGrant.id).where(
        NodeProjectGrant.node_id == node_id,
        NodeProjectGrant.project_id == project_id,
        NodeProjectGrant.enabled.is_(True),
    )) is not None


def node_run_grant_authorized(db, node_id: str, run) -> bool:
    try:
        run_file_payload(db, run)
    except Problem:
        return False
    grant = grant_for(db, node_id, run.project_id)
    if not grant:
        return False
    version = db.get(Version, run.version_id)
    return (not uses_directory(version.parameter_schema)
            or roots_are_subset([run.parameters["input_dir"]], grant.input_roots))


def claim(db, settings, node):
    if (node.maintenance or not node.last_seen or time.time()-node.last_seen > settings.node_timeout
            or not node.agent_version.startswith("0.1.")):
        return None
    occupied = db.scalar(select(func.count()).select_from(Run).where(Run.node_id == node.id, Run.state.in_(ACTIVE)))
    if occupied >= node.slots:
        return None
    state = db.get(NodeConfigState, node.id)
    managed = bool(state and configuration_ready(state) and CONFIG_CAPABILITY in node.capabilities)
    for grant in active_grants(db, node.id):
        if grant.project_id != node.project_id and not managed:
            continue
        candidates = db.scalars(select(Run).join(Task, Task.id == Run.task_id).where(
            Run.project_id == grant.project_id,
            Run.state == "queued",
            or_(Task.node_id.is_(None), Task.node_id == node.id),
        ).order_by(Run.created_at, Run.id)).yield_per(200)
        for run in candidates:
            task, version = db.get(Task, run.task_id), db.get(Version, run.version_id)
            if not task.enabled or not version.published or not still_authorized(db, run):
                fail_without_start(db, run, "启动前授权或任务状态已失效")
                continue
            try:
                files = run_file_payload(db, run)
                if files and db.get(RunFileOptions, run.id).node_id != node.id:
                    continue
            except Problem:
                continue
            directory = uses_directory(version.parameter_schema)
            if directory and (DIRECTORY_CAPABILITY not in node.capabilities or not managed or not grant.input_roots):
                continue
            if not set(version.required_capabilities).issubset(node.capabilities):
                continue
            if version.side_effects and (node.isolation != "os_user" or not settings.allow_side_effects):
                continue
            keys = sorted(f"{run.project_id}:{key}" for key in task.resource_keys)
            if keys and db.scalar(select(ResourceLock.key).where(ResourceLock.key.in_(keys))):
                continue
            now = time.time()
            run.state, run.node_id, run.attempt_id, run.leased_at = "leased", node.id, uid(), now
            # 公平游标必须严格递增，不能受宿主机墙钟回拨影响。
            last_turn = db.scalar(select(func.max(NodeProjectGrant.last_claimed_at)).where(
                NodeProjectGrant.node_id == node.id)) or 0
            grant.last_claimed_at = last_turn + 1
            for key in keys:
                db.add(ResourceLock(key=key, run_id=run.id))
            db.flush()
            return run, grant, state
    return None


def owned_run(db, node, run_id, attempt_id):
    run = db.get(Run, run_id)
    if not run or run.node_id != node.id or run.attempt_id != attempt_id:
        raise Problem(404, "attempt_not_found", "运行尝试不存在或不属于该节点")
    return run


def evaluate_result(db, run, payload):
    version = db.get(Version, run.version_id)
    report = payload.result
    if payload.observation == "unknown":
        return "unknown", payload.error or "进程或业务结果待核对"
    if payload.observation == "startup_failed":
        return "failed", payload.error or "程序未能启动"
    if payload.observation in {"timeout", "canceled"}:
        if version.side_effects:
            return "unknown", "停止进程不代表外部写入回滚，请核对"
        return ("canceled" if payload.observation == "canceled" else "failed"), payload.error
    if payload.error:
        return "unknown" if version.side_effects else "failed", payload.error
    if report is not None:
        if (len(canonical(report)) > 64000 or report.get("protocol_version") != "1"
                or report.get("run_id") != run.id or report.get("attempt_id") != run.attempt_id
                or report.get("status") not in {"success", "failed", "partial", "unknown", "canceled"}
                or not isinstance(report.get("summary"), str) or len(report["summary"]) > 2000
                or ("notification_text" in report and
                    (not isinstance(report["notification_text"], str)
                     or not report["notification_text"].strip()
                     or len(report["notification_text"]) > 2000))):
            return "unknown" if version.side_effects else "failed", "结果协议或运行编号不匹配"
        status = report["status"]
        if status == "unknown":
            return "unknown", report["summary"]
        if status == "partial":
            return "partial", report["summary"]
        if status == "failed":
            return "failed", report["summary"]
        if status == "canceled":
            return "unknown" if version.side_effects else "canceled", report["summary"]
        artifacts = report.get("artifacts", [])
        if not isinstance(artifacts, list) or len(artifacts) > 100:
            return "unknown" if version.side_effects else "failed", "产物清单无效"
        for item in artifacts:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                return "unknown" if version.side_effects else "failed", "产物清单无效"
            if item.get("required") and not db.scalar(select(Artifact.id).where(Artifact.run_id == run.id, Artifact.name == item["name"])):
                return "unknown" if version.side_effects else "failed", "必需产物未归档，需核对业务完成证据"
    if payload.exit_code != 0:
        return "unknown" if version.side_effects else "failed", payload.error or "进程非正常成功退出"
    if version.result_required and report is None:
        return "unknown" if version.side_effects else "failed", "缺少必需结果报告"
    return "succeeded", None


def complete(db, run, payload, settings):
    hashed = content_hash(payload.model_dump())
    if run.completion_hash:
        if run.completion_hash != hashed:
            run.late_evidence = payload.model_dump()
            return False  # 路由返回409而不抛异常，保证冲突证据随事务提交。
        return
    if run.state in TERMINAL:
        run.late_evidence = payload.model_dump()
        return False
    if run.state == "unknown":
        run.late_evidence = payload.model_dump()
        return  # 失联后的证据供核对，不自动解除危险资源占用。
    state, error = evaluate_result(db, run, payload)
    run.state, run.error = state, error
    run.exit_code, run.result, run.observation = payload.exit_code, payload.result, payload.observation
    run.artifact_state, run.completion_hash = payload.artifact_state, hashed
    run.finished_at = time.time()
    if state != "unknown":
        release(db, run)
    if state in {"failed", "partial", "unknown"}:
        alert(db, run.project_id, state, error or "程序未完整完成", "run:"+run.id, run_id=run.id)
    if state == "succeeded" and isinstance(run.result, dict) and run.result.get("status") == "success":
        config = db.get(TaskResultNotificationConfig, run.task_id)
        if config and config.enabled:
            channel = db.get(Channel, config.channel_id)
            if channel and channel.project_id == run.project_id:
                task = db.get(Task, run.task_id)
                message = run.result.get("notification_text", run.result["summary"])
                summary = redact(message.strip())[:2000]
                content = (f"自动化工作台 · 运行结果\n任务：{task.name}\n运行：{run.id}\n"
                           f"结果：{summary or '运行成功，未提供摘要'}")
                if settings.public_base_url:
                    content += f"\n查看结果与文件：{settings.public_base_url}{settings.base_path}/#/runs/{run.id}"
                db.add(ResultNotification(project_id=run.project_id, task_id=run.task_id,
                                          run_id=run.id, channel_id=channel.id, content=content,
                                          delivery_state="pending" if channel.enabled else "disabled"))
