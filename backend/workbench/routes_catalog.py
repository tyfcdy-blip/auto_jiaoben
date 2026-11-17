import time
import hashlib
from fastapi import APIRouter, Depends, Request, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from . import schemas as S
from .config import Settings
from .security import write_lock
from .models import (Program, Version, Task, TaskResultNotificationConfig, Channel, Node,
                     NodeConfigState, NodeProjectGrant, Project, Batch, BatchFile, Caller, Run, Schedule)
from .security import (Identity, Problem, administrator, audit, authorize, get_db,
                       human, identity, require, token_pair)
from .storage import (check_schema, validate_command, validate_parameters, validate_zip,
                      read_upload, store_bytes, safe_name, content_hash, object_path)
from .bundle_scan import scan_bundle
from .views import listing, public, project_filter
from .input_sources import (DIRECTORY_CAPABILITY, input_mode, roots_are_subset,
                            uses_directory, validate_directory_parameters, validate_root_list)
from .node_config import config_state, grant_for, publish_configuration
from .execution import ACTIVE, node_dispatch_status
from .agent_package import windows_agent_package
from .retention import (active_file_condition, batch_has_expired_files, batch_retention_state,
                        batch_retention_states, file_views)

router = APIRouter()


@router.get("/agent-packages/windows")
def download_windows_agent_package(who=Depends(human)):
    return Response(content=windows_agent_package(), media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="automation-workbench-windows-agent.zip"',
        "X-Content-Type-Options": "nosniff",
    })


def authorize_input(db, who, project_id, level="operator"):
    if who.kind == "caller":
        if not who.record.allow_input_upload or who.record.project_id != project_id:
            raise Problem(403, "input_forbidden", "调用方没有该项目输入交付权限")
    elif who.kind == "user":
        authorize(db, who, project_id, level)
    else:
        raise Problem(403, "input_forbidden", "当前身份不能交付批次")


def scoped_list(model, db, who, project_id, limit, offset, transform=None):
    query = project_filter(db, who, select(model).order_by(model.created_at.desc()), model, project_id)
    return listing(db, query, limit, offset, transform)


def batch_listing(db, query, limit, offset):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    records = db.scalars(query.limit(limit).offset(offset)).all()
    states = batch_retention_states(db, [record.id for record in records])
    return {"items": [{**public(record), "retention_state": states[record.id]} for record in records],
            "total": total}


@router.get("/programs")
def programs(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
             offset: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    return scoped_list(Program, db, who, project_id, limit, offset)


@router.post("/programs", status_code=201)
def create_program(payload: S.ProgramCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    require(db, Project, payload.project_id)
    authorize(db, who, payload.project_id, "developer")
    program = Program(**payload.model_dump())
    db.add(program)
    db.flush()
    audit(db, who, "create_program", program.id, program.project_id)
    return public(program)


@router.get("/programs/{program_id}/versions")
def versions(program_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    program = require(db, Program, program_id)
    authorize(db, who, program.project_id)
    return listing(db, select(Version).where(Version.program_id == program_id).order_by(Version.created_at.desc()), 200)


@router.post("/programs/{program_id}/versions", status_code=201)
def create_version(program_id: str, payload: S.VersionCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    program = require(db, Program, program_id)
    authorize(db, who, program.project_id, "developer")
    check_schema(payload.parameter_schema)
    validate_command(payload.command)
    if db.scalar(select(Version.id).where(Version.program_id == program_id, Version.name == payload.name)):
        raise Problem(409, "duplicate_version", "同名版本已存在")
    values = payload.model_dump()
    if uses_directory(payload.parameter_schema):
        values["required_capabilities"] = list(dict.fromkeys([*payload.required_capabilities, DIRECTORY_CAPABILITY]))
        if len(values["required_capabilities"]) > 30:
            raise Problem(422, "invalid_capabilities", "目录能力计入最多30项环境要求")
    version = Version(program_id=program_id, **values)
    db.add(version)
    db.flush()
    audit(db, who, "create_version", version.id, program.project_id)
    return public(version)


def enforce_bundle_scan(report: dict, accept_review: bool = False):
    if report["decision"] == "blocked":
        raise Problem(422, "bundle_scan_blocked", "程序包存在确定错误或禁止项，请修复后重新上传")
    if report["decision"] == "review" and not accept_review:
        raise Problem(409, "bundle_scan_review_required", "程序包包含高风险能力，需要人工确认后继续")


@router.post("/program-bundles/scan")
async def scan_program_bundle(project_id: str, runtime: str, entry: str, request: Request,
                              who=Depends(human), db=Depends(get_db, scope="function")):
    require(db, Project, project_id)
    authorize(db, who, project_id, "developer")
    validate_command([runtime, entry])
    payload = await read_upload(request, request.app.state.settings.max_upload_bytes)
    validate_zip(payload, entry)
    return await run_in_threadpool(scan_bundle, payload, entry, runtime)


@router.post("/versions/{version_id}/bundle")
async def upload_bundle(version_id: str, request: Request, accept_review: bool = False,
                        who=Depends(human), db=Depends(get_db, scope="function")):
    payload = await read_upload(request, request.app.state.settings.max_upload_bytes)
    # 行锁、ZIP校验和落盘都可能等待；同步依赖仍负责事务提交，事件循环只接收流并等待结果。
    return await run_in_threadpool(_store_bundle, version_id, payload, request.app.state.settings,
                                   accept_review, who, db)


def _store_bundle(version_id: str, payload: bytes, settings: Settings, accept_review: bool,
                  who: Identity, db: Session) -> dict:
    version = require(db, Version, version_id)
    program = require(db, Program, version.program_id)
    authorize(db, who, program.project_id, "developer")
    if version.published:
        raise Problem(409, "immutable_version", "已发布版本不可修改")
    validate_zip(payload, version.command[1])
    report = scan_bundle(payload, version.command[1], version.command[0])
    enforce_bundle_scan(report, accept_review)
    incoming_sha = hashlib.sha256(payload).hexdigest()
    # 静态扫描不占用全局写锁；真正写入前重新读取身份和版本状态，防止等待期间撤权或发布。
    write_lock(db, who)
    db.refresh(version)
    db.refresh(program)
    authorize(db, who, program.project_id, "developer")
    if version.published:
        raise Problem(409, "immutable_version", "已发布版本不可修改")
    if version.bundle_sha256:
        if version.bundle_sha256 == incoming_sha:
            return {**public(version), "bundle_scan": report}
        raise Problem(409, "immutable_bundle", "已保存的程序包不可替换，请创建新版本")
    project_shas = set(db.scalars(select(Version.bundle_sha256).join(
        Program, Program.id == Version.program_id).where(
            Program.project_id == program.project_id,
            Version.bundle_sha256.is_not(None))).all())
    stored_bytes = sum(object_path(settings, sha).stat().st_size for sha in project_shas)
    if incoming_sha not in project_shas and stored_bytes + len(payload) > settings.max_project_program_bytes:
        raise Problem(413, "project_program_quota", "项目程序包容量已达上限，请联系管理员处理历史版本")
    version.bundle_sha256 = store_bytes(settings, payload)
    audit(db, who, "upload_bundle", version.id, program.project_id)
    return {**public(version), "bundle_scan": report}


@router.get("/versions/{version_id}/bundle")
def download_bundle(version_id: str, request: Request, who=Depends(human),
                    db=Depends(get_db, scope="function")):
    version = require(db, Version, version_id)
    program = require(db, Program, version.program_id)
    authorize(db, who, program.project_id)
    if not version.bundle_sha256:
        raise Problem(404, "missing_bundle", "该版本尚无可下载的程序包")
    path = object_path(request.app.state.settings, version.bundle_sha256)
    audit(db, who, "download_bundle", version.id, program.project_id)
    return FileResponse(path, media_type="application/zip",
                        filename=f"program-package-{version.id}.zip",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.get("/versions/{version_id}/bundle-scan")
def inspect_stored_bundle(version_id: str, request: Request, who=Depends(human),
                          db=Depends(get_db, scope="function")):
    version = require(db, Version, version_id)
    program = require(db, Program, version.program_id)
    authorize(db, who, program.project_id, "developer")
    if not version.bundle_sha256:
        raise Problem(404, "missing_bundle", "该版本尚无可检查的程序包")
    payload = object_path(request.app.state.settings, version.bundle_sha256).read_bytes()
    return scan_bundle(payload, version.command[1], version.command[0])


@router.post("/versions/{version_id}/publish")
def publish(version_id: str, request: Request, accept_review: bool = False,
            who=Depends(human), db=Depends(get_db, scope="function")):
    version = require(db, Version, version_id)
    program = require(db, Program, version.program_id)
    authorize(db, who, program.project_id, "project_admin")
    if not version.bundle_sha256:
        raise Problem(409, "missing_bundle", "请先上传有效程序包")
    scanned_sha = version.bundle_sha256
    report = scan_bundle(object_path(request.app.state.settings, scanned_sha).read_bytes(),
                         version.command[1], version.command[0])
    enforce_bundle_scan(report, accept_review)
    write_lock(db, who)
    db.refresh(version)
    db.refresh(program)
    authorize(db, who, program.project_id, "project_admin")
    if version.bundle_sha256 != scanned_sha:
        raise Problem(409, "bundle_changed", "程序包已更新，请重新检查后发布")
    if version.side_effects and not request.app.state.settings.allow_side_effects:
        raise Problem(409, "side_effects_disabled", "外部写入任务尚未通过环境与业务授权门禁")
    version.published = True
    audit(db, who, "publish_version", version.id, program.project_id)
    return {**public(version), "bundle_scan": report}


@router.get("/tasks")
def tasks(project_id: str | None = None, q: str | None = Query(None, max_length=100), node_id: str | None = None,
          limit: int = Query(50, ge=1, le=200),
          offset: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    query = project_filter(db, who, select(Task).order_by(Task.created_at.desc()), Task, project_id)
    if node_id:
        query = query.where(Task.node_id == node_id)
    if q and q.strip():
        query = query.where(Task.name.contains(q.strip(), autoescape=True))
    page = listing(db, query, limit, offset)
    identifiers = {item["version_id"] for item in page["items"]}
    versions = {item.id: item for item in db.scalars(select(Version).where(Version.id.in_(identifiers))).all()} if identifiers else {}
    from .models import TaskFileOptions
    files = {record.task_id: record.options for record in db.scalars(select(TaskFileOptions).where(
        TaskFileOptions.task_id.in_([item["id"] for item in page["items"]])))}
    for item in page["items"]:
        version = versions[item["version_id"]]
        item["input_mode"] = input_mode(version.parameter_schema, item["requires_input"])
        item["program_id"] = version.program_id
        item["parameter_schema"] = version.parameter_schema
        item["config_hash"] = task_config_hash(item, files.get(item["id"]))
    return page


@router.get("/tasks/{task_id}")
def task_detail(task_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id)
    version = db.get(Version, task.version_id)
    return task_view(task, version, db)


@router.get("/task-notification-rules")
def task_notification_rules(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
                            offset: int = Query(0, ge=0), who=Depends(human),
                            db=Depends(get_db, scope="function")):
    query = select(Task.id, Task.project_id, Task.name, TaskResultNotificationConfig.enabled,
                   Channel.name.label("channel_name")).outerjoin(
                       TaskResultNotificationConfig, TaskResultNotificationConfig.task_id == Task.id,
                   ).outerjoin(Channel, Channel.id == TaskResultNotificationConfig.channel_id)
    query = project_filter(db, who, query, Task, project_id).order_by(Task.created_at.desc())
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.execute(query.limit(limit).offset(offset)).all()
    return {"items": [{"id": task_id, "project_id": owner, "name": name,
                       "notification_enabled": bool(enabled), "channel_name": channel_name}
                      for task_id, owner, name, enabled, channel_name in rows], "total": total}


@router.get("/tasks/{task_id}/result-notification")
def task_result_notification(task_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id)
    config = db.get(TaskResultNotificationConfig, task.id)
    return {"task_id": task.id, "channel_id": config.channel_id if config else None,
            "enabled": config.enabled if config else False, "revision": config.revision if config else 0}


@router.put("/tasks/{task_id}/result-notification")
def update_task_result_notification(task_id: str, payload: S.TaskResultNotificationUpdate,
                                    who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id, "project_admin")
    if payload.enabled and not payload.channel_id:
        raise Problem(422, "missing_channel", "启用结果通知需要选择钉钉连接")
    if payload.channel_id:
        channel = require(db, Channel, payload.channel_id)
        if channel.project_id != task.project_id:
            raise Problem(422, "invalid_channel", "只能选择同项目的钉钉连接")
    config = db.get(TaskResultNotificationConfig, task.id)
    revision = config.revision if config else 0
    if revision != payload.base_revision:
        raise Problem(409, "stale_config", "结果通知配置已被其他人修改，请刷新后重试")
    if config:
        config.channel_id, config.enabled = payload.channel_id, payload.enabled
        config.revision += 1
    else:
        config = TaskResultNotificationConfig(task_id=task.id, channel_id=payload.channel_id,
                                              enabled=payload.enabled)
        db.add(config)
    db.flush()
    audit(db, who, "update_task_result_notification", task.id, task.project_id)
    return {"task_id": task.id, "channel_id": config.channel_id,
            "enabled": config.enabled, "revision": config.revision}


def validated_task_values(db, project_id, payload):
    version = require(db, Version, payload.version_id)
    program = require(db, Program, version.program_id)
    if not version.published or program.project_id != project_id:
        raise Problem(422, "invalid_version", "任务必须绑定本项目已发布版本")
    validate_parameters(version.parameter_schema, payload.default_parameters, partial=True)
    if payload.node_id:
        require(db, Node, payload.node_id)
        if not grant_for(db, payload.node_id, project_id):
            raise Problem(422, "invalid_node", "执行电脑未授权给该项目")
    if any(not key or len(key) > 100 for key in payload.resource_keys) or len(set(payload.resource_keys)) != len(payload.resource_keys):
        raise Problem(422, "invalid_resources", "资源名称无效或重复")
    if any(field not in version.parameter_schema.get("properties", {}) for field in payload.business_key_fields):
        raise Problem(422, "invalid_business_key", "业务键必须引用已定义的参数字段")
    values = payload.model_dump(exclude={"project_id", "base_config_hash", "file_options"})
    if uses_directory(version.parameter_schema):
        if not payload.node_id or payload.requires_input:
            raise Problem(422, "invalid_directory_task", "文件夹输入需要固定执行电脑，不能同时要求平台文件批次")
        node = require(db, Node, payload.node_id)
        if DIRECTORY_CAPABILITY not in node.capabilities:
            raise Problem(422, "directory_node_required", "请先在执行电脑配置允许读取的根目录并连接新版执行器")
        grant = grant_for(db, node.id, project_id)
        if not grant or not grant.input_roots:
            raise Problem(422, "directory_grant_required", "请先为该项目配置执行电脑的允许目录")
        validate_directory_parameters(payload.default_parameters, partial=True)
        values["business_key_fields"] = list(dict.fromkeys([*payload.business_key_fields, "batch_key"]))
        if len(values["business_key_fields"]) > 20:
            raise Problem(422, "invalid_business_key", "目录批次键计入最多20项业务去重字段")
    return values, version


def task_config_hash(task, file_options=None):
    def value(field):
        return task[field] if isinstance(task, dict) else getattr(task, field)

    configuration = {field: value(field) for field in (
        "name", "version_id", "default_parameters", "node_id", "timeout_seconds",
        "resource_keys", "business_key_fields", "requires_input", "operation",
    )}
    if file_options and (file_options.get("inputs") or file_options.get("output_dir")):
        configuration["file_options"] = file_options
    return content_hash(configuration)


def task_view(task, version, db):
    from .node_files import task_options
    return {**public(task), "program_id": version.program_id,
            "parameter_schema": version.parameter_schema,
            "input_mode": input_mode(version.parameter_schema, task.requires_input),
            "config_hash": task_config_hash(task, task_options(db, task.id))}


@router.post("/tasks", status_code=201)
def create_task(payload: S.TaskCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    authorize(db, who, payload.project_id, "developer")
    values, version = validated_task_values(db, payload.project_id, payload)
    task = Task(project_id=payload.project_id, **values)
    db.add(task)
    db.flush()
    if payload.file_options is not None:
        from .node_files import set_task_options
        set_task_options(db, task, payload.file_options.model_dump())
    audit(db, who, "create_task", task.id, task.project_id)
    return task_view(task, version, db)


@router.patch("/tasks/{task_id}")
def change_task(task_id: str, payload: S.Enabled, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id, "operator")
    task.enabled = payload.enabled
    audit(db, who, "set_task_enabled", task.id, task.project_id)
    return public(task)


@router.put("/tasks/{task_id}")
def update_task(task_id: str, payload: S.TaskUpdate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id, "developer")
    from .node_files import task_options
    if payload.base_config_hash != task_config_hash(task, task_options(db, task.id)):
        raise Problem(409, "stale_config", "任务配置已被其他人修改，请刷新后重试")
    values, version = validated_task_values(db, task.project_id, payload)
    execution_fields = {"version_id", "default_parameters", "node_id", "timeout_seconds", "resource_keys",
                        "business_key_fields", "requires_input", "operation"}
    execution_changed = any(getattr(task, field) != values[field] for field in execution_fields)
    if execution_changed and db.scalar(select(Run.id).where(
            Run.task_id == task.id,
            Run.state.in_(["waiting_input", "queued", *ACTIVE, "unknown"]),
    ).limit(1)):
        raise Problem(409, "task_in_use", "任务仍有未结束或待核对的运行，暂不能修改执行配置")
    if execution_changed and db.scalar(select(Schedule.id).where(
            Schedule.task_id == task.id, Schedule.enabled.is_(True)).limit(1)):
        raise Problem(409, "active_schedules", "请先暂停该任务的全部定时计划，再修改执行配置")
    for field, value in values.items():
        setattr(task, field, value)
    from .node_files import set_task_options, task_options
    set_task_options(db, task, payload.file_options.model_dump() if payload.file_options is not None else task_options(db, task.id))
    audit(db, who, "update_task", task.id, task.project_id)
    return task_view(task, version, db)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id, "developer")
    if db.scalar(select(Run.id).where(Run.task_id == task.id).limit(1)):
        raise Problem(409, "task_has_history", "任务已有运行记录，为保留审计历史只能停用，不能删除")
    if db.scalar(select(Schedule.id).where(Schedule.task_id == task.id).limit(1)):
        raise Problem(409, "task_has_schedules", "请先删除该任务的定时计划")
    if any(task.id in caller.task_ids for caller in db.scalars(
            select(Caller).where(Caller.project_id == task.project_id))):
        raise Problem(409, "task_has_callers", "请先从事件接入调用方中移除该任务")
    notification_config = db.get(TaskResultNotificationConfig, task.id)
    if notification_config:
        db.delete(notification_config)
    from .models import TaskFileOptions
    file_config = db.get(TaskFileOptions, task.id)
    if file_config:
        db.delete(file_config)
        db.flush()
    audit(db, who, "delete_task", task.id, task.project_id)
    db.delete(task)
    return Response(status_code=204)


@router.get("/nodes")
def nodes(request: Request, project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
          offset: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    if project_id:
        authorize(db, who, project_id)
        query = select(Node).join(NodeProjectGrant, NodeProjectGrant.node_id == Node.id).where(
            NodeProjectGrant.project_id == project_id,
            NodeProjectGrant.enabled.is_(True),
        )
    elif who.record.is_admin:
        query = select(Node)
    else:
        from .models import Member
        query = select(Node).join(NodeProjectGrant, NodeProjectGrant.node_id == Node.id).join(
            Member, Member.project_id == NodeProjectGrant.project_id).where(
                Member.user_id == who.record.id,
                NodeProjectGrant.enabled.is_(True),
            ).distinct()
    query = query.order_by(Node.created_at.desc())

    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    records = db.scalars(query.limit(limit).offset(offset)).all()
    identifiers = [node.id for node in records]
    states = {state.node_id: state for state in db.scalars(select(NodeConfigState).where(
        NodeConfigState.node_id.in_(identifiers))).all()} if identifiers else {}
    active_counts = dict(db.execute(select(Run.node_id, func.count()).where(
        Run.node_id.in_(identifiers), Run.state.in_(ACTIVE)).group_by(Run.node_id)).all()
        ) if identifiers else {}
    grants = {grant.node_id: grant for grant in db.scalars(select(NodeProjectGrant).where(
        NodeProjectGrant.node_id.in_(identifiers),
        NodeProjectGrant.project_id == project_id)).all()} if identifiers and project_id else {}

    def node_view(node):
        state = states.get(node.id)
        grant = grants.get(node.id)
        return {**public(node),
                "online": bool(node.last_seen and node.enabled and
                               time.time()-node.last_seen < request.app.state.settings.node_timeout),
                **node_dispatch_status(db, request.app.state.settings, node, state=state,
                                       active_runs=active_counts.get(node.id, 0)),
                "config_revision": state.desired_revision if state else 0,
                "applied_revision": state.applied_revision if state else 0,
                "config_status": state.apply_status if state else "legacy_unmanaged",
                "project_input_roots": list(grant.input_roots) if grant and grant.enabled else []}

    return {"items": [node_view(node) for node in records], "total": total}


@router.post("/nodes", status_code=201)
def create_node(payload: S.NodeCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    require(db, Project, payload.project_id)
    authorize(db, who, payload.project_id, "project_admin")
    roots = validate_root_list(payload.input_roots)
    token, hashed = token_pair()
    values = payload.model_dump(exclude={"input_roots"})
    values["capabilities"] = [value for value in values["capabilities"] if value != DIRECTORY_CAPABILITY]
    if roots:
        values["capabilities"].append(DIRECTORY_CAPABILITY)
    node = Node(token_hash=hashed, **values)
    db.add(node)
    db.flush()
    state = NodeConfigState(node_id=node.id)
    db.add(state)
    publish_configuration(state, roots)
    db.add(NodeProjectGrant(node_id=node.id, project_id=node.project_id,
                            input_roots=roots, updated_by=who.actor))
    audit(db, who, "create_node", node.id, node.project_id)
    return {**public(node), "token": token, "config_revision": state.desired_revision,
            "config_hash": state.desired_hash, "input_roots": roots}


@router.get("/nodes/{node_id}/configuration")
def node_configuration(node_id: str, who=Depends(administrator), db=Depends(get_db, scope="function")):
    node = require(db, Node, node_id)
    state = config_state(db, node.id)
    grants = db.scalars(select(NodeProjectGrant).where(
        NodeProjectGrant.node_id == node.id,
    ).order_by(NodeProjectGrant.created_at, NodeProjectGrant.id)).all()
    return {"node": public(node), "desired_revision": state.desired_revision,
            "applied_revision": state.applied_revision, "config_status": state.apply_status,
            "config_error": state.apply_error, "input_roots": list(state.desired_input_roots),
            "root_status": list(state.root_status), "grants": [
                {**public(grant), "project_name": require(db, Project, grant.project_id).name}
                for grant in grants]}


def _cancel_unstarted_project_runs(db, node_id: str, project_id: str) -> None:
    for run in db.scalars(select(Run).where(
            Run.node_id == node_id, Run.project_id == project_id,
            Run.state.in_(["leased", "cancel_requested"]))):
        run.cancel_requested = True
        if run.state == "leased":
            run.state = "cancel_requested"


@router.put("/nodes/{node_id}/configuration")
def update_node_configuration(node_id: str, payload: S.NodeConfigurationUpdate,
                              who=Depends(administrator), db=Depends(get_db, scope="function")):
    """原子维护电脑根目录与项目授权，避免分步保存留下越权或半配置状态。"""
    write_lock(db, who)
    node = require(db, Node, node_id)
    state = config_state(db, node.id)
    if payload.base_revision != state.desired_revision:
        raise Problem(409, "config_revision_conflict", "电脑配置已变化，请刷新后重新提交")
    if not node.maintenance:
        raise Problem(409, "maintenance_required", "请先让电脑进入维护状态再修改配置")
    if db.scalar(select(Run.id).where(
            Run.node_id == node.id,
            Run.state.in_(["leased", "running", "cancel_requested", "unknown"])).limit(1)):
        raise Problem(409, "node_busy", "电脑仍有运行或待核对任务，不能修改配置")

    roots = validate_root_list(payload.input_roots)
    project_ids = [item.project_id for item in payload.grants]
    if len(project_ids) != len(set(project_ids)):
        raise Problem(422, "duplicate_project_grant", "同一项目不能重复配置")
    if node.project_id not in project_ids:
        raise Problem(422, "owner_project_required", "电脑登记时所属项目必须保留")
    if payload.slots != 1 and sum(1 for item in payload.grants if item.enabled) > 1:
        raise Problem(422, "shared_node_single_slot", "跨项目共享电脑首版只允许同时运行一个任务")

    existing_project_ids = set(db.scalars(select(NodeProjectGrant.project_id).where(
        NodeProjectGrant.node_id == node.id)).all())
    if not existing_project_ids.issubset(project_ids):
        raise Problem(409, "incomplete_project_grants", "项目授权列表已变化，请刷新后重新提交")
    prepared: list[tuple[Project, S.NodeProjectGrantSpec, list[str]]] = []
    for item in payload.grants:
        project = require(db, Project, item.project_id)
        project_roots = validate_root_list(item.input_roots, field="project_input_roots")
        if not roots_are_subset(project_roots, roots):
            raise Problem(422, "grant_outside_host_roots", f"项目“{project.name}”的目录不在电脑允许目录内")
        if item.project_id == node.project_id and not item.enabled:
            raise Problem(422, "owner_project_required", "电脑登记时所属项目不能移除")
        prepared.append((project, item, project_roots))

    node.name, node.slots = payload.name, payload.slots
    # 所有共享配置变更都推进修订号，使旧页面不能覆盖刚发生的项目授权调整。
    publish_configuration(state, roots)
    for project, item, project_roots in prepared:
        grant = grant_for(db, node.id, project.id, enabled=False)
        was_enabled = grant.enabled if grant else False
        if grant is None:
            grant = NodeProjectGrant(node_id=node.id, project_id=project.id,
                                     input_roots=project_roots, enabled=item.enabled,
                                     updated_by=who.actor)
            db.add(grant)
        else:
            grant.input_roots, grant.enabled = project_roots, item.enabled
            grant.updated_by, grant.updated_at = who.actor, time.time()
        if was_enabled and not item.enabled:
            _cancel_unstarted_project_runs(db, node.id, project.id)
    audit(db, who, "update_node_configuration", node.id, node.project_id,
          note=f"revision={state.desired_revision};projects={len(prepared)}")
    db.flush()
    return {"node": public(node), "desired_revision": state.desired_revision,
            "applied_revision": state.applied_revision, "config_status": state.apply_status,
            "config_error": state.apply_error, "input_roots": list(state.desired_input_roots),
            "root_status": list(state.root_status), "grants": [
                {**public(grant), "project_name": require(db, Project, grant.project_id).name}
                for grant in db.scalars(select(NodeProjectGrant).where(
                    NodeProjectGrant.node_id == node.id).order_by(
                        NodeProjectGrant.created_at, NodeProjectGrant.id)).all()]}


@router.patch("/nodes/{node_id}")
def change_node(node_id: str, payload: S.NodePatch, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    node = require(db, Node, node_id)
    changes = payload.model_dump(exclude_none=True)
    global_change = any(key in changes for key in ("name", "slots", "input_roots"))
    if global_change and not who.record.is_admin:
        raise Problem(403, "forbidden", "电脑名称、容量和允许目录只能由平台管理员维护")
    if not global_change:
        shared = db.scalar(select(NodeProjectGrant.id).where(
            NodeProjectGrant.node_id == node.id,
            NodeProjectGrant.enabled.is_(True),
            NodeProjectGrant.project_id != node.project_id,
        ).limit(1))
        if shared and not who.record.is_admin:
            raise Problem(403, "shared_node_admin_required", "共享电脑的维护和停用只能由平台管理员操作")
        authorize(db, who, node.project_id, "project_admin")
    if changes.get("slots", node.slots) != 1 and db.scalar(select(NodeProjectGrant.id).where(
            NodeProjectGrant.node_id == node.id,
            NodeProjectGrant.enabled.is_(True),
            NodeProjectGrant.project_id != node.project_id).limit(1)):
        raise Problem(422, "shared_node_single_slot", "跨项目共享电脑首版只允许同时运行一个任务")
    for key in ("enabled", "maintenance", "name", "slots"):
        if key in changes:
            setattr(node, key, changes[key])
    if "input_roots" in changes:
        state = config_state(db, node.id)
        if payload.base_revision != state.desired_revision:
            raise Problem(409, "config_revision_conflict", "电脑配置已变化，请刷新后重新提交")
        if not node.maintenance:
            raise Problem(409, "maintenance_required", "修改电脑目录前请先进入维护状态")
        active = db.scalar(select(Run.id).where(Run.node_id == node.id,
                                                Run.state.in_(["leased", "running", "cancel_requested", "unknown"])).limit(1))
        if active:
            raise Problem(409, "node_busy", "电脑仍有运行或待核对任务，不能修改目录")
        roots = validate_root_list(changes["input_roots"])
        grants = db.scalars(select(NodeProjectGrant).where(
            NodeProjectGrant.node_id == node.id,
            NodeProjectGrant.enabled.is_(True),
        )).all()
        if any(not roots_are_subset(grant.input_roots, roots) for grant in grants):
            raise Problem(422, "grant_outside_host_roots", "请先缩小各项目目录授权，再缩小电脑允许目录")
        publish_configuration(state, roots)
    audit(db, who, "update_node", node.id, node.project_id)
    state = config_state(db, node.id)
    return {**public(node), "config_revision": state.desired_revision,
            "applied_revision": state.applied_revision, "config_status": state.apply_status}


@router.put("/nodes/{node_id}/projects/{project_id}")
def update_node_project(node_id: str, project_id: str, payload: S.NodeProjectGrantUpdate,
                        who=Depends(administrator), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    node, project = require(db, Node, node_id), require(db, Project, project_id)
    state = config_state(db, node.id)
    if payload.base_revision != state.desired_revision:
        raise Problem(409, "config_revision_conflict", "电脑配置已变化，请刷新后重新提交")
    roots = validate_root_list(payload.input_roots, field="project_input_roots")
    if project.id == node.project_id and not payload.enabled:
        raise Problem(422, "owner_project_required", "电脑登记时所属项目不能移除；可停用整台电脑")
    if payload.enabled and project.id != node.project_id and node.slots != 1:
        raise Problem(422, "shared_node_single_slot", "请先把电脑总并发数调整为1，再授权给其他项目")
    if not roots_are_subset(roots, state.desired_input_roots):
        raise Problem(422, "grant_outside_host_roots", "项目目录必须位于电脑允许目录内")
    grant = grant_for(db, node.id, project.id, enabled=False)
    was_enabled = grant.enabled if grant else False
    previous_roots = list(grant.input_roots) if grant else []
    if grant is None:
        grant = NodeProjectGrant(node_id=node.id, project_id=project.id,
                                 input_roots=roots, enabled=payload.enabled, updated_by=who.actor)
        db.add(grant)
    else:
        grant.input_roots, grant.enabled = roots, payload.enabled
        grant.updated_by, grant.updated_at = who.actor, time.time()
    if was_enabled and (not payload.enabled or not roots_are_subset(previous_roots, roots)):
        _cancel_unstarted_project_runs(db, node.id, project.id)
    publish_configuration(state, list(state.desired_input_roots))
    audit(db, who, "update_node_project_grant", node.id, project.id,
          note=f"enabled={payload.enabled};project={project.id}")
    db.flush()
    return {**public(grant), "project_name": project.name,
            "config_revision": state.desired_revision, "config_hash": state.desired_hash,
            "config_status": state.apply_status}


@router.get("/callers")
def callers(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    return scoped_list(Caller, db, who, project_id, limit, offset)


@router.post("/callers", status_code=201)
def create_caller(payload: S.CallerCreate, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    require(db, Project, payload.project_id)
    authorize(db, who, payload.project_id, "project_admin")
    for task_id in payload.task_ids:
        if require(db, Task, task_id).project_id != payload.project_id:
            raise Problem(422, "invalid_task", "调用方只能绑定本项目任务")
    token, hashed = token_pair()
    caller = Caller(token_hash=hashed, **payload.model_dump())
    db.add(caller)
    db.flush()
    audit(db, who, "create_caller", caller.id, caller.project_id)
    return {**public(caller), "token": token}


@router.patch("/callers/{caller_id}")
def change_caller(caller_id: str, payload: S.Active, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    caller = require(db, Caller, caller_id)
    authorize(db, who, caller.project_id, "project_admin")
    caller.active = payload.active
    audit(db, who, "update_caller", caller.id, caller.project_id)
    return public(caller)


@router.get("/batches")
def batches(project_id: str | None = None, limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), who=Depends(identity), db=Depends(get_db, scope="function")):
    if who.kind == "caller":
        authorize_input(db, who, project_id or who.record.project_id)
        return batch_listing(
            db, select(Batch).where(Batch.project_id == who.record.project_id).order_by(Batch.created_at.desc()),
            limit, offset)
    if who.kind != "user":
        raise Problem(403, "input_forbidden", "当前身份不能访问批次")
    query = project_filter(db, who, select(Batch).order_by(Batch.created_at.desc()), Batch, project_id)
    return batch_listing(db, query, limit, offset)


@router.post("/batches", status_code=201)
def create_batch(payload: S.BatchCreate, request: Request, who=Depends(identity),
                 db=Depends(get_db, scope="function")):
    write_lock(db, who)
    authorize_input(db, who, payload.project_id)
    maximum = request.app.state.settings.max_project_open_batches
    open_batches = db.scalar(select(func.count(Batch.id)).where(
        Batch.project_id == payload.project_id, Batch.ready.is_(False))) or 0
    if open_batches >= maximum:
        raise Problem(413, "project_batch_limit", "项目待交付文件批次已达上限，请先完成或删除无文件草稿")
    batch = Batch(**payload.model_dump())
    db.add(batch)
    db.flush()
    audit(db, who, "create_batch", batch.id, batch.project_id)
    return public(batch)


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: str, who=Depends(identity), db=Depends(get_db, scope="function")):
    batch = require(db, Batch, batch_id)
    authorize_input(db, who, batch.project_id, "viewer")
    files = db.scalars(select(BatchFile).where(BatchFile.batch_id == batch_id).order_by(BatchFile.name)).all()
    return {**public(batch), "retention_state": batch_retention_state(db, batch.id),
            "files": file_views(db, "input", files, public)}


@router.delete("/batches/{batch_id}", status_code=204)
def delete_empty_batch(batch_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    batch = require(db, Batch, batch_id)
    authorize(db, who, batch.project_id, "operator")
    if batch.ready or db.scalar(select(BatchFile.id).where(BatchFile.batch_id == batch.id)):
        raise Problem(409, "batch_not_empty", "只能删除尚未上传文件的待交付批次")
    if (db.scalar(select(Run.id).where(Run.input_batch_id == batch.id))
            or db.scalar(select(Schedule.id).where(Schedule.input_batch_id == batch.id))):
        raise Problem(409, "batch_in_use", "文件批次已被运行或计划引用，不能删除")
    audit(db, who, "delete_empty_batch", batch.id, batch.project_id)
    db.delete(batch)
    return Response(status_code=204)


@router.put("/batches/{batch_id}/files/{filename}")
async def upload_input(batch_id: str, filename: str, request: Request, who=Depends(identity), db=Depends(get_db, scope="function")):
    safe_name(filename)
    payload = await read_upload(request, request.app.state.settings.max_upload_bytes)
    return await run_in_threadpool(_store_input, batch_id, filename, payload, request.app.state.settings, who, db)


def _store_input(batch_id: str, filename: str, payload: bytes, settings: Settings, who: Identity,
                 db: Session) -> dict:
    write_lock(db, who)
    batch = require(db, Batch, batch_id)
    authorize_input(db, who, batch.project_id)
    if batch_has_expired_files(db, batch.id):
        raise Problem(410, "file_expired", "该批次已有文件超过保留期限，请创建新批次")
    existing = db.scalar(select(BatchFile).where(BatchFile.batch_id == batch_id, BatchFile.name == filename))
    if existing:
        # 上传响应丢失时，重发同一内容不新增记录，也不改变已冻结清单。
        if existing.sha256 == hashlib.sha256(payload).hexdigest() and existing.size == len(payload):
            return public(existing)
        raise Problem(409, "duplicate_file", "文件已存在，请创建修订批次")
    if batch.ready:
        raise Problem(409, "immutable_batch", "已就绪批次不可修改")
    file_count = db.scalar(select(func.count(BatchFile.id)).where(BatchFile.batch_id == batch.id)) or 0
    batch_bytes = db.scalar(select(func.coalesce(func.sum(BatchFile.size), 0)).where(
        BatchFile.batch_id == batch.id)) or 0
    if file_count >= settings.max_batch_files or batch_bytes + len(payload) > settings.max_batch_bytes:
        raise Problem(413, "batch_input_limit", "文件批次的文件数量或总容量已达上限")
    project_bytes = db.scalar(select(func.coalesce(func.sum(BatchFile.size), 0)).select_from(
        BatchFile).join(Batch, Batch.id == BatchFile.batch_id).where(
            Batch.project_id == batch.project_id, active_file_condition("input", BatchFile))) or 0
    if project_bytes + len(payload) > settings.max_project_input_bytes:
        raise Problem(413, "project_input_quota", "项目输入文件容量已达上限，请联系管理员归档历史输入")
    sha = store_bytes(settings, payload)
    record = BatchFile(batch_id=batch.id, name=filename, sha256=sha, size=len(payload))
    db.add(record)
    db.flush()
    audit(db, who, "upload_input", batch.id, batch.project_id)
    return public(record)


@router.post("/batches/{batch_id}/ready")
def ready(batch_id: str, who=Depends(identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    batch = require(db, Batch, batch_id)
    authorize_input(db, who, batch.project_id)
    if batch_has_expired_files(db, batch.id):
        raise Problem(410, "file_expired", "该批次已有文件超过保留期限，请创建新批次")
    if not db.scalar(select(BatchFile.id).where(BatchFile.batch_id == batch.id)):
        raise Problem(409, "empty_batch", "批次至少包含一个文件")
    batch.ready = True
    audit(db, who, "ready_batch", batch.id, batch.project_id)
    return public(batch)
