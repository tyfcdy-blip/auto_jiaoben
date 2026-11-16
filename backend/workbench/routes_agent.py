import time
from fastapi import APIRouter, Depends, Request, Response, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from . import schemas as S
from .config import Settings
from .security import write_lock
from .execution import (TERMINAL, claim, complete, node_run_grant_authorized,
                        owned_run, still_authorized)
from .models import Artifact, BatchFile, LogEntry, Run, Task, Version
from .security import Identity, Problem, get_db, node_identity
from .storage import object_path, read_upload, safe_name, store_bytes
from .retention import active_file_condition, require_available
from .views import public
from .input_sources import uses_directory
from .input_sources import validate_root_list
from .redaction import redact
from .node_config import (CONFIG_CAPABILITY, config_state, configuration_payload,
                          grant_for, publish_configuration,
                          report_configuration)

router = APIRouter(prefix="/agent")


@router.post("/heartbeat")
def heartbeat(payload: S.Heartbeat, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    node = who.record
    node.last_seen, node.capabilities = time.time(), payload.capabilities
    node.isolation, node.agent_version = payload.isolation, payload.agent_version
    state = config_state(db, node.id)
    reported_roots = (validate_root_list(payload.reported_input_roots, field="reported_input_roots")
                      if payload.reported_input_roots is not None else None)
    if (state.desired_revision == 0 and reported_roots is not None
            and CONFIG_CAPABILITY in payload.capabilities):
        publish_configuration(state, reported_roots)
        owner = grant_for(db, node.id, node.project_id, enabled=False)
        if owner is not None and not owner.input_roots:
            owner.input_roots = list(reported_roots)
            owner.updated_by, owner.updated_at = who.actor, time.time()
    report_configuration(state, revision=payload.config_revision, config_hash=payload.config_hash,
                         input_roots=reported_roots,
                         root_status=[item.model_dump() for item in payload.root_status])
    canceled = db.scalars(select(Run.id).where(Run.node_id == node.id, Run.cancel_requested.is_(True),
                                               Run.state.in_(["leased", "running", "cancel_requested"]))).all()
    return {"cancel_run_ids": list(canceled)}


@router.get("/configuration")
def configuration(who=Depends(node_identity), db=Depends(get_db, scope="function")):
    state = config_state(db, who.record.id)
    if state.desired_revision <= 0:
        return Response(status_code=204)
    return {**configuration_payload(state.desired_revision, list(state.desired_input_roots)),
            "config_hash": state.desired_hash}


@router.post("/configuration/ack")
def acknowledge_configuration(payload: S.NodeConfigAck, who=Depends(node_identity),
                              db=Depends(get_db, scope="function")):
    write_lock(db, who)
    state = config_state(db, who.record.id)
    if payload.revision != state.desired_revision or payload.config_hash != state.desired_hash:
        raise Problem(409, "stale_configuration", "配置修订已变化，请重新获取")
    if payload.status == "applied":
        state.applied_revision, state.applied_hash = payload.revision, payload.config_hash
        state.apply_status, state.apply_error = "applied", ""
    else:
        state.apply_status, state.apply_error = "failed", payload.error or "执行电脑拒绝配置"
    state.reported_at = time.time()
    return {"ok": True, "revision": state.desired_revision, "status": state.apply_status}


@router.post("/claim")
def receive(request: Request, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    gate = write_lock(db, who)
    db.refresh(who.record)
    if not who.record.enabled:
        raise Problem(401, "unauthorized", "节点已停用")
    settings = request.app.state.settings
    if not settings.dispatch_enabled or not gate.dispatch_enabled:
        return Response(status_code=204)
    claimed = claim(db, settings, who.record)
    if not claimed:
        return Response(status_code=204)
    run, grant, config = claimed
    task, version = db.get(Task, run.task_id), db.get(Version, run.version_id)
    prefix = settings.base_path + "/api/v1/agent/runs/"+run.id
    files = db.scalars(select(BatchFile).where(
        BatchFile.batch_id == run.input_batch_id,
        active_file_condition("input", BatchFile))).all() if run.input_batch_id else []
    response = {"run_id": run.id, "attempt_id": run.attempt_id, "protocol_version": "1", "command": version.command,
            "bundle_sha256": version.bundle_sha256, "bundle_url": prefix+"/bundle?attempt_id="+run.attempt_id,
            "parameters": run.parameters, "operation": task.operation, "timeout_seconds": task.timeout_seconds,
            "side_effects": version.side_effects, "result_required": version.result_required,
            "resource_keys": task.resource_keys, "input_files": [
                {"name": item.name, "sha256": item.sha256, "url": prefix+"/inputs/"+item.id+"?attempt_id="+run.attempt_id}
                for item in files]}
    if uses_directory(version.parameter_schema):
        response["input_directory"] = run.parameters["input_dir"]
    if CONFIG_CAPABILITY in who.record.capabilities:
        response["project_id"] = run.project_id
        response["config_revision"] = config.applied_revision if config else 0
        response["allowed_input_roots"] = list(grant.input_roots)
    return response


@router.get("/runs/{run_id}/bundle")
def download_bundle(run_id: str, attempt_id: str, request: Request, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    run = owned_run(db, who.record, run_id, attempt_id)
    if run.state != "running" and not node_run_grant_authorized(db, who.record.id, run):
        raise Problem(409, "grant_revoked", "项目已撤销这台电脑的启动授权")
    version = db.get(Version, run.version_id)
    return FileResponse(object_path(request.app.state.settings, version.bundle_sha256), media_type="application/zip")


@router.get("/runs/{run_id}/inputs/{file_id}")
def download_input(run_id: str, file_id: str, attempt_id: str, request: Request, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    run = owned_run(db, who.record, run_id, attempt_id)
    if run.state != "running" and not node_run_grant_authorized(db, who.record.id, run):
        raise Problem(409, "grant_revoked", "项目已撤销这台电脑的启动授权")
    item = db.get(BatchFile, file_id)
    if not item or item.batch_id != run.input_batch_id:
        raise Problem(404, "not_found", "输入文件不存在")
    require_available(db, "input", item.id)
    return FileResponse(object_path(request.app.state.settings, item.sha256), media_type="application/octet-stream")


@router.post("/runs/{run_id}/started")
def started(run_id: str, payload: S.Started, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = owned_run(db, who.record, run_id, payload.attempt_id)
    if run.state == "running" and run.pid == payload.pid:
        return {"ok": True}
    db.refresh(who.record)
    task, version = db.get(Task, run.task_id), db.get(Version, run.version_id)
    if (run.cancel_requested or not who.record.enabled or not task.enabled or not version.published
            or not node_run_grant_authorized(db, who.record.id, run)
            or not still_authorized(db, run)):
        raise Problem(409, "start_revoked", "开始执行前任务已取消、暂停或授权失效，禁止进入业务程序")
    if run.state != "leased":
        raise Problem(409, "invalid_state", "启动报告与当前状态不一致")
    if run.pid and run.pid != payload.pid:
        raise Problem(409, "pid_conflict", "重复启动进程编号不一致")
    run.pid, run.started_at = payload.pid, time.time()
    if not run.cancel_requested:
        run.state = "running"
    return {"ok": True}


@router.get("/runs/{run_id}/control")
def control(run_id: str, attempt_id: str, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    run = owned_run(db, who.record, run_id, attempt_id)
    return {"cancel_requested": run.cancel_requested}


@router.post("/runs/{run_id}/logs")
def receive_logs(run_id: str, payload: S.Logs, request: Request, who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = owned_run(db, who.record, run_id, payload.attempt_id)
    for entry in payload.entries:
        existing = db.scalar(select(LogEntry).where(LogEntry.run_id == run.id, LogEntry.sequence == entry.sequence))
        sanitized = redact(entry.text)
        if existing:
            if existing.text != sanitized or existing.stream != entry.stream:
                raise Problem(409, "log_conflict", "同一序号日志内容冲突")
            continue
        if run.log_chars + len(sanitized) > request.app.state.settings.max_log_chars:
            run.logs_truncated = True
            continue
        run.log_chars += len(sanitized)
        db.add(LogEntry(run_id=run.id, sequence=entry.sequence, stream=entry.stream, text=sanitized))
    return {"ok": True, "truncated": run.logs_truncated}


@router.post("/runs/{run_id}/complete")
def finish(run_id: str, payload: S.Completion, request: Request,
           who=Depends(node_identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = owned_run(db, who.record, run_id, payload.attempt_id)
    if payload.error:
        payload.error = redact(payload.error)
    if complete(db, run, payload, request.app.state.settings) is False:
        return JSONResponse(status_code=409, content={"code": "completion_conflict",
                            "message": "报告与终态冲突，已保留证据待核对", "request_id": run.id})
    return {"ok": True, "state": run.state}


@router.post("/runs/{run_id}/artifacts/{filename}")
async def upload_artifact(run_id: str, filename: str, request: Request, attempt_id: str = Query(),
                          who=Depends(node_identity), db=Depends(get_db, scope="function")):
    safe_name(filename)
    payload = await read_upload(request, request.app.state.settings.max_upload_bytes)
    return await run_in_threadpool(_store_artifact, run_id, filename, attempt_id, payload,
                                  request.app.state.settings, who, db)


def _store_artifact(run_id: str, filename: str, attempt_id: str, payload: bytes, settings: Settings,
                    who: Identity, db: Session) -> dict:
    write_lock(db, who)
    run = owned_run(db, who.record, run_id, attempt_id)
    if run.state in TERMINAL:
        raise Problem(409, "closed_attempt", "尝试已结束，不能替换产物")
    sha = store_bytes(settings, payload)
    existing = db.scalar(select(Artifact).where(Artifact.run_id == run.id, Artifact.name == filename))
    if existing:
        if existing.sha256 != sha:
            raise Problem(409, "artifact_conflict", "产物名称相同但内容不同")
        return public(existing)
    item = Artifact(run_id=run.id, name=filename, sha256=sha, size=len(payload))
    db.add(item)
    db.flush()
    return public(item)
