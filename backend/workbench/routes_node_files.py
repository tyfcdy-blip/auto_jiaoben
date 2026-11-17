"""HTTPS反向轮询文件能力；目录元数据也受项目授权与配置代次限制。"""
import time
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, func, delete
from . import node_file_schemas as F
from .schemas import NodeConfigAck
from .models import (Node, NodeConfigState, NodeFileConfig, NodeFileRequest, Run, RunFileOptions,
                     Task, User, Member, Artifact, NodeProjectGrant, Project)
from .node_files import (direct_child, output_roots, ready_node, run_file_payload,
                         run_file_status, set_task_options, task_options)
from .node_config import grant_for
from .input_sources import canonical_root, roots_are_subset, validate_root_list
from .security import (Problem, audit, authorize, get_db, human, node_identity, require, write_lock)
from .storage import content_hash, safe_name
from .redaction import redact

router = APIRouter()


@router.get("/nodes/{node_id}")
def node_detail(node_id: str, request: Request, who=Depends(human), db=Depends(get_db, scope="function")):
    from .execution import node_dispatch_status
    from .views import public
    node = require(db, Node, node_id)
    query = select(NodeProjectGrant, Project).join(Project, Project.id == NodeProjectGrant.project_id).where(
        NodeProjectGrant.node_id == node_id, NodeProjectGrant.enabled.is_(True))
    if not who.record.is_admin:
        query = query.join(Member, Member.project_id == Project.id).where(Member.user_id == who.record.id)
    grants = db.execute(query).all()
    if not grants and not who.record.is_admin:
        raise Problem(404, "not_found", "电脑不存在或无权查看")
    state = db.get(NodeConfigState, node_id)
    return {**public(node), **node_dispatch_status(db, request.app.state.settings, node, state=state),
            "online": bool(node.last_seen and node.enabled and time.time()-node.last_seen < request.app.state.settings.node_timeout),
            "config_revision": state.desired_revision if state else 0,
            "applied_revision": state.applied_revision if state else 0,
            "config_status": state.apply_status if state else "legacy_unmanaged",
            "projects": [{"project_id": project.id, "name": project.name, "enabled": grant.enabled} for grant, project in grants]}


def project_node(db, who, node_id: str, project_id: str, level="viewer"):
    authorize(db, who, project_id, level)
    node = require(db, Node, node_id)
    if not grant_for(db, node_id, project_id):
        raise Problem(404, "not_found", "电脑未授权给该项目")
    return node


@router.get("/nodes/{node_id}/file-configuration")
def file_configuration(node_id: str, project_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    project_node(db, who, node_id, project_id)
    config = db.get(NodeFileConfig, node_id)
    can_manage = bool(who.record.is_admin)
    return {"revision": config.revision if config else 0,
            "applied_revision": config.applied_revision if config else 0,
            "apply_status": config.apply_status if config else "not_configured",
            "output_roots": config.output_roots if config and can_manage else [],
            "grants": config.grants if config and can_manage else [],
            "allowed_input_roots": grant_for(db, node_id, project_id).input_roots,
            "allowed_output_roots": output_roots(db, node_id, project_id)}


@router.put("/nodes/{node_id}/file-configuration")
def update_configuration(node_id: str, payload: F.FileConfiguration,
                         who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    # 电脑级文件权限与既有读取配置保持同一边界；项目管理员不能扩张宿主机权限。
    if not who.record.is_admin:
        raise Problem(403, "forbidden", "电脑结果目录授权仅平台管理员可维护")
    node = require(db, Node, node_id)
    config = db.get(NodeFileConfig, node_id)
    if payload.base_revision != (config.revision if config else 0):
        raise Problem(409, "stale_config", "写入目录配置已变更，请刷新")
    if not node.maintenance:
        raise Problem(409, "maintenance_required", "请先让电脑进入维护状态再修改结果目录")
    if db.scalar(select(Run.id).where(Run.node_id == node_id,
                 Run.state.in_(["leased", "running", "cancel_requested", "unknown"])).limit(1)):
        raise Problem(409, "node_busy", "电脑仍有运行或待核对任务，不能修改结果目录")
    roots = validate_root_list(payload.output_roots, field="output_roots")
    grants, projects = [], set()
    for grant in payload.grants:
        if grant.project_id in projects or not grant_for(db, node_id, grant.project_id):
            raise Problem(422, "invalid_output_grant", "写入授权项目重复或尚未授权此电脑")
        projects.add(grant.project_id)
        selected = validate_root_list(grant.output_roots, field="output_roots")
        if not roots_are_subset(selected, roots):
            raise Problem(422, "invalid_output_grant", "项目写目录必须属于电脑允许写入目录")
        grants.append({"project_id": grant.project_id, "output_roots": selected})
    if not config:
        config = NodeFileConfig(node_id=node_id, revision=0)
        db.add(config)
    config.revision += 1
    config.output_roots, config.grants, config.apply_status = roots, grants, "pending"
    config.config_hash = content_hash({"revision": config.revision, "output_roots": roots})
    audit(db, who, "configure_node_output", node_id, node.project_id)
    return {"revision": config.revision, "apply_status": "pending"}


@router.get("/agent/file-configuration")
def agent_configuration(who=Depends(node_identity), db=Depends(get_db, scope="function")):
    config = db.get(NodeFileConfig, who.record.id)
    if not config:
        return Response(status_code=204)
    return {"revision": config.revision, "output_roots": config.output_roots, "config_hash": config.config_hash}


@router.post("/agent/file-configuration/ack")
def configuration_ack(payload: NodeConfigAck, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    config = require(db, NodeFileConfig, who.record.id)
    if payload.revision != config.revision or payload.config_hash != config.config_hash:
        raise Problem(409, "stale_config", "配置代次已过期")
    config.apply_status = payload.status
    if payload.status == "applied":
        config.applied_revision = payload.revision
    return {"accepted": True}


def request_authorized(db, record):
    _, grant, state, config = ready_node(db, record.node_id, record.project_id, write=record.purpose == "output")
    actor = db.get(User, record.actor.removeprefix("user:"))
    member = db.scalar(select(Member).where(Member.user_id == actor.id, Member.project_id == record.project_id)) if actor else None
    if not actor or not actor.active or not (actor.is_admin or member and member.role in {"operator", "developer", "project_admin"}):
        raise Problem(403, "browse_authorization_revoked", "文件浏览发起人的权限已撤销")
    roots = grant.input_roots if record.purpose == "input" else output_roots(db, record.node_id, record.project_id)
    if (state.applied_revision != record.config_revision
            or (record.purpose == "output" and config.applied_revision != record.output_config_revision)):
        raise Problem(409, "stale_config", "目录配置已变更，请重新浏览")
    if not roots_are_subset([record.path], roots):
        raise Problem(403, "file_grant_revoked", "目录授权已撤销")
    return roots


@router.post("/nodes/{node_id}/file-requests", status_code=202)
def create_request(node_id: str, payload: F.BrowseCreate, request: Request,
                   who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    project_node(db, who, node_id, payload.project_id, "operator")
    _, grant, state, config = ready_node(db, node_id, payload.project_id,
                                       settings=request.app.state.settings, write=payload.purpose == "output")
    roots = grant.input_roots if payload.purpose == "input" else output_roots(db, node_id, payload.project_id)
    if not roots_are_subset([payload.path], roots):
        raise Problem(422, "file_outside_grant", "目录不在项目授权范围内")
    now = time.time()
    # 浏览仅为短时目录元数据，按电脑有界回收；审计事件、任务配置和真实文件不受影响。
    expired_ids = list(db.scalars(select(NodeFileRequest.id).where(
        NodeFileRequest.node_id == node_id, NodeFileRequest.expires_at < now-86400)
        .order_by(NodeFileRequest.expires_at).limit(100)))
    if expired_ids:
        db.execute(delete(NodeFileRequest).where(NodeFileRequest.id.in_(expired_ids)))
    pending = db.scalar(select(func.count()).select_from(NodeFileRequest).where(
        NodeFileRequest.project_id == payload.project_id, NodeFileRequest.expires_at > now,
        NodeFileRequest.state.in_(["pending", "leased"])))
    if pending >= 10:
        raise Problem(429, "browse_limit", "文件浏览请求过多，请等待完成")
    record = NodeFileRequest(node_id=node_id, actor=who.actor, **payload.model_dump(),
                             config_revision=state.applied_revision,
                             output_config_revision=config.applied_revision if config else 0,
                             expires_at=now+60)
    db.add(record)
    db.flush()
    audit(db, who, "browse_node_files", record.id, payload.project_id)
    return {"id": record.id, "state": record.state, "expires_at": record.expires_at}


@router.get("/nodes/{node_id}/file-requests/{request_id}")
def read_request(node_id: str, request_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    record = require(db, NodeFileRequest, request_id)
    if record.node_id != node_id:
        raise Problem(404, "not_found", "浏览请求不存在")
    project_node(db, who, node_id, record.project_id, "operator")
    request_authorized(db, record)
    state = "expired" if record.expires_at < time.time() else record.state
    return {"id": record.id, "state": state, "expires_at": record.expires_at,
            "path": record.path, "offset": record.offset,
            **(record.response or {"entries": [], "truncated": False, "next_offset": None, "error": ""})}


@router.post("/agent/file-requests/claim")
def claim_request(who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    records = db.scalars(select(NodeFileRequest).where(NodeFileRequest.node_id == who.record.id,
        NodeFileRequest.state.in_(["pending", "leased"]), NodeFileRequest.expires_at > time.time())
        .order_by(NodeFileRequest.created_at).limit(10))
    for record in records:
        try:
            roots = request_authorized(db, record)
        except Problem:
            record.state = "failed"
            record.response = {"entries": [], "truncated": False, "next_offset": None, "error": "目录配置或权限已变更，请重新浏览"}
            continue
        record.state = "leased"
        return {"id": record.id, "project_id": record.project_id, "purpose": record.purpose,
                "path": record.path, "limit": record.limit, "offset": record.offset,
                "expires_at": record.expires_at, "config_revision": record.config_revision,
                "output_config_revision": record.output_config_revision, "allowed_roots": roots}
    return Response(status_code=204)


@router.post("/agent/file-requests/{request_id}/complete")
def complete_request(request_id: str, payload: F.BrowseCompletion,
                     who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    record = require(db, NodeFileRequest, request_id)
    if record.node_id != who.record.id:
        raise Problem(404, "not_found", "请求不属于此电脑")
    if record.expires_at < time.time():
        raise Problem(410, "browse_expired", "浏览请求已过期")
    request_authorized(db, record)
    if payload.config_revision != record.config_revision or payload.output_config_revision != record.output_config_revision:
        raise Problem(409, "stale_config", "响应配置代次不一致")
    if len(payload.entries) > record.limit:
        raise Problem(422, "browse_limit", "浏览响应超出请求限制")
    seen = set()
    for entry in payload.entries:
        safe_name(entry.name)
        key = canonical_root(entry.path)
        expected = canonical_root(record.path.rstrip("/\\")+"/"+entry.name)
        if not direct_child(record.path, entry.path) or key != expected or key in seen:
            raise Problem(422, "invalid_browse_entry", "浏览结果必须是当前目录直接子项且不能重复")
        seen.add(key)
    if payload.next_offset is not None and (not payload.truncated or payload.next_offset != record.offset + len(payload.entries) or not payload.entries):
        raise Problem(422, "invalid_browse_offset", "浏览分页位置不一致")
    digest = content_hash(payload.model_dump())
    if record.response_hash:
        if record.response_hash != digest:
            raise Problem(409, "browse_conflict", "同一浏览请求返回内容冲突")
        return {"accepted": True}
    if record.state != "leased":
        raise Problem(409, "browse_not_leased", "浏览请求尚未领取")
    record.response_hash = digest
    record.response = payload.model_dump(exclude={"config_revision", "output_config_revision"})
    record.response["error"] = redact(payload.error)
    record.state = "failed" if payload.error else "succeeded"
    return {"accepted": True}


@router.get("/tasks/{task_id}/file-options")
def get_task_options(task_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    from .routes_catalog import task_config_hash
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id)
    options = task_options(db, task.id)
    return {**options, "base_config_hash": task_config_hash(task, options)}


@router.put("/tasks/{task_id}/file-options")
def put_task_options(task_id: str, payload: F.FileOptionsUpdate, who=Depends(human), db=Depends(get_db, scope="function")):
    from .routes_catalog import task_config_hash
    write_lock(db, who)
    task = require(db, Task, task_id)
    authorize(db, who, task.project_id, "developer")
    if payload.base_config_hash != task_config_hash(task, task_options(db, task.id)):
        raise Problem(409, "stale_config", "任务配置已变更，请刷新后重试")
    result = set_task_options(db, task, payload.model_dump(exclude={"base_config_hash"}))
    audit(db, who, "configure_task_files", task.id, task.project_id)
    return result


@router.post("/agent/runs/{run_id}/file-save")
def file_save(run_id: str, payload: F.SaveCompletion, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    from .execution import owned_run
    write_lock(db, who)
    run = owned_run(db, who.record, run_id, payload.attempt_id)
    record = require(db, RunFileOptions, run_id)
    # 失败且无文件路径的状态回报不授予任何文件访问权；撤权后仍能结束另存状态。
    options = record.options if payload.status == "failed" and not payload.files else run_file_payload(db, run)
    if not options["output_dir"] or payload.retry_number != record.retry_number:
        raise Problem(409, "save_not_requested", "另存未请求或重试编号已失效")
    digest = content_hash(payload.model_dump())
    if record.save_hash:
        if record.save_hash != digest:
            raise Problem(409, "save_conflict", "同一另存尝试返回内容冲突")
        return {"accepted": True}
    base = options["output_dir"].rstrip("/\\") + "/" + run.id
    if payload.retry_number:
        base += "/retry-" + str(payload.retry_number)
    names = set()
    # 上传后的产物记录是服务器可确认的声明证据；Agent仍须在本地严格匹配result.json。
    artifacts = set(db.scalars(select(Artifact.name).where(Artifact.run_id == run.id)))
    for saved in payload.files:
        safe_name(saved.name)
        if (saved.name in names or canonical_root(saved.path) != canonical_root(base+"/"+saved.name)
                or (artifacts and saved.name not in artifacts)):
            raise Problem(422, "invalid_saved_file", "另存文件不属于独立运行目录或已声明产物")
        names.add(saved.name)
    record.save_hash, record.save_state = digest, payload.status
    record.saved_files = [saved.model_dump() for saved in payload.files]
    record.save_error = redact(payload.error)
    record.retry_requested = False
    return {"accepted": True}


@router.post("/runs/{run_id}/file-save/retry", status_code=202)
def retry_save(run_id: str, request: Request, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = require(db, Run, run_id)
    authorize(db, who, run.project_id, "operator")
    record = require(db, RunFileOptions, run_id)
    if run.state not in {"succeeded", "failed", "partial", "canceled"} or not run.attempt_id:
        raise Problem(409, "run_not_finished", "程序结束后才能单独重试另存")
    ready_node(db, record.node_id, run.project_id, settings=request.app.state.settings, write=True)
    run_file_payload(db, run)
    if run_file_status(db, run)["save_state"] != "failed" or record.retry_number >= 10:
        raise Problem(409, "save_retry_unavailable", "仅失败的另存可重试，每次运行最多10次")
    record.retry_number += 1
    record.save_state, record.save_hash, record.save_error = "pending", None, ""
    record.retry_requested, record.retry_expires_at = True, time.time()+300
    audit(db, who, "retry_file_save", run.id, run.project_id)
    return {"accepted": True, "retry_number": record.retry_number}


@router.post("/agent/file-save-retries/claim")
def claim_retry(who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    records = db.scalars(select(RunFileOptions).where(RunFileOptions.node_id == who.record.id,
                        RunFileOptions.retry_requested.is_(True)).limit(10))
    for record in records:
        if record.retry_expires_at < time.time():
            record.save_state, record.save_error, record.retry_requested = "failed", "另存重试超时", False
            continue
        run = require(db, Run, record.run_id)
        try:
            options = run_file_payload(db, run)
        except Problem:
            record.save_state, record.save_error, record.retry_requested = "failed", "目录授权已变更", False
            continue
        return {"run_id": run.id, "attempt_id": run.attempt_id,
                "retry_number": record.retry_number, "node_files": options}
    return Response(status_code=204)
