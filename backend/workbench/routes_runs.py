from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from . import schemas as S
from .security import write_lock
from .execution import ACTIVE, admit, trigger_authorized, release, queued_run_reason, TERMINAL
from .models import Run, Task, Version, Artifact, LogEntry, Project, TriggerEvent, RunFileOptions
from .security import Problem, audit, authorize, get_db, human, identity, require
from .storage import object_path
from .views import project_filter, public, run_view
from .retention import file_views, require_available
from .node_files import run_file_status
import time

router = APIRouter()

RUN_STATE_GROUPS = {
    "active": ACTIVE,
    "queued": {"queued", "waiting_input"},
    "failed": {"failed", "partial", "unknown"},
}


def authorize_run(db, who, run, level="viewer"):
    if who.kind == "caller":
        triggered = db.scalar(select(TriggerEvent.id).where(
            TriggerEvent.run_id == run.id, TriggerEvent.actor == who.actor,
        ).limit(1))
        if (run.project_id != who.record.project_id or run.task_id not in who.record.task_ids
                or triggered is None):
            raise Problem(404, "not_found", "资源不存在或无权访问")
    elif who.kind == "user":
        authorize(db, who, run.project_id, level)
    else:
        raise Problem(403, "forbidden", "执行节点请使用节点专用接口")


@router.post("/tasks/{task_id}/runs")
def trigger(task_id: str, payload: S.RunCreate, request: Request, response: Response,
            who=Depends(identity), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    task = require(db, Task, task_id)
    trigger_authorized(db, who, task)
    run, duplicate = admit(db, request.app.state.settings, task, who.actor, payload.event_id,
                           payload.parameters, payload.input_batch_id, "event" if who.kind == "caller" else "manual",
                           file_options=payload.file_options.model_dump() if payload.file_options else None)
    audit(db, who, "trigger_duplicate" if duplicate else "trigger", run.id, task.project_id)
    response.status_code = 200 if duplicate else 202
    return {"run_id": run.id, "accepted": True, "deduplicated": duplicate, "state": run.state}


def _run_response(db, settings, run, cache=None):
    cache = cache if cache is not None else {}
    task_cache = cache.setdefault("tasks", {})
    project_cache = cache.setdefault("projects", {})
    if run.task_id not in task_cache:
        task_cache[run.task_id] = db.get(Task, run.task_id)
    result = public(run)
    result["file_status"] = run_file_status(db, run, cache.get("file_options"))
    result["task_name"] = task_cache[run.task_id].name
    if run.project_id not in project_cache:
        project_cache[run.project_id] = db.get(Project, run.project_id)
    project = project_cache[run.project_id]
    result["project_name"] = project.name if project else run.project_id
    waiting = queued_run_reason(db, settings, run, cache)
    if waiting:
        result["waiting_reason_code"] = waiting["code"]
        result["waiting_reason"] = waiting["message"]
    return result


@router.get("/runs")
def runs(request: Request, project_id: str | None = None, task_id: str | None = None, node_id: str | None = None,
         schedule_id: str | None = None,
         state: str | None = Query(
             None,
             pattern="^(waiting_input|queued|leased|running|cancel_requested|succeeded|failed|partial|unknown|canceled)$",
         ), state_group: str | None = Query(None, pattern="^(active|queued|failed)$"),
         source: str | None = Query(None, pattern="^(manual|schedule|event)$"),
         limit: int = Query(50, ge=1, le=200),
         offset: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    if schedule_id:
        actor = "schedule:" + schedule_id
        query = project_filter(
            db,
            who,
            select(TriggerEvent, Run).join(Run, Run.id == TriggerEvent.run_id).where(
                TriggerEvent.actor == actor,
            ).order_by(TriggerEvent.created_at.desc(), TriggerEvent.id.desc()),
            Run,
            project_id,
        )
        if task_id:
            query = query.where(Run.task_id == task_id)
        if node_id:
            query = query.where((Run.node_id == node_id) | Run.task_id.in_(select(Task.id).where(Task.node_id == node_id)))
        if state:
            query = query.where(Run.state == state)
        if state_group:
            query = query.where(Run.state.in_(RUN_STATE_GROUPS[state_group]))
        if source and source != "schedule":
            return {"items": [], "total": 0}
        total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
        pairs = db.execute(query.limit(limit).offset(offset)).all()
        run_ids = {run.id for _, run in pairs}
        ranked_events = select(
            TriggerEvent.run_id,
            TriggerEvent.id,
            func.row_number().over(
                partition_by=TriggerEvent.run_id,
                order_by=(TriggerEvent.created_at, TriggerEvent.id),
            ).label("position"),
        ).where(
            TriggerEvent.actor == actor,
            TriggerEvent.run_id.in_(run_ids),
        ).subquery()
        first_event_ids = dict(db.execute(select(
            ranked_events.c.run_id,
            ranked_events.c.id,
        ).where(ranked_events.c.position == 1)).all()) if run_ids else {}
        cache: dict = {"file_options": {record.run_id: record for record in db.scalars(
            select(RunFileOptions).where(RunFileOptions.run_id.in_(run_ids)))}}
        items = []
        for event, run in pairs:
            item = _run_response(db, request.app.state.settings, run, cache)
            item["original_source"] = run.source
            item["source"] = "schedule"
            item["schedule_triggered_at"] = public(event)["created_at"]
            item["schedule_event_id"] = event.event_id
            item["schedule_deduplicated"] = (
                run.actor != actor
                or run.source != "schedule"
                or event.id != first_event_ids.get(run.id)
            )
            items.append(item)
        return {"items": items, "total": total}

    query = project_filter(db, who, select(Run).order_by(Run.created_at.desc(), Run.id.desc()), Run, project_id)
    if node_id:
        query = query.where((Run.node_id == node_id) | Run.task_id.in_(select(Task.id).where(Task.node_id == node_id)))
    if task_id:
        query = query.where(Run.task_id == task_id)
    if state:
        query = query.where(Run.state == state)
    if state_group:
        query = query.where(Run.state.in_(RUN_STATE_GROUPS[state_group]))
    if source:
        query = query.where(Run.source == source)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    records = db.scalars(query.limit(limit).offset(offset)).all()
    task_ids, version_ids = {run.task_id for run in records}, {run.version_id for run in records}
    project_ids = {run.project_id for run in records}
    reason_cache: dict = {
        "file_options": {record.run_id: record for record in db.scalars(select(RunFileOptions).where(
            RunFileOptions.run_id.in_([run.id for run in records])))},
        "tasks": {task.id: task for task in db.scalars(
            select(Task).where(Task.id.in_(task_ids))).all()} if task_ids else {},
        "versions": {version.id: version for version in db.scalars(
            select(Version).where(Version.id.in_(version_ids))).all()} if version_ids else {},
        "projects": {project.id: project for project in db.scalars(
            select(Project).where(Project.id.in_(project_ids))).all()} if project_ids else {},
    }
    return {"items": [_run_response(db, request.app.state.settings, run, reason_cache)
                      for run in records], "total": total}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request, who=Depends(identity), db=Depends(get_db, scope="function")):
    run = require(db, Run, run_id)
    authorize_run(db, who, run)
    result = _run_response(db, request.app.state.settings, run)
    result["artifacts"] = file_views(
        db, "artifact", db.scalars(select(Artifact).where(Artifact.run_id == run.id)), public)
    if who.kind == "caller":
        result.pop("file_status", None)
        result.pop("late_evidence", None)
        result.pop("parameters", None)
        for key in ("actor", "node_id", "attempt_id", "waiting_reason", "waiting_reason_code", "project_name"):
            result.pop(key, None)
    return result


@router.get("/runs/{run_id}/logs")
def logs(run_id: str, after: int = Query(0, ge=0), who=Depends(human), db=Depends(get_db, scope="function")):
    run = require(db, Run, run_id)
    authorize_run(db, who, run)
    records = db.scalars(select(LogEntry).where(LogEntry.run_id == run.id, LogEntry.sequence > after)
                         .order_by(LogEntry.sequence).limit(500)).all()
    return {"entries": [public(record) for record in records], "truncated": run.logs_truncated}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = require(db, Run, run_id)
    authorize_run(db, who, run, "operator")
    if run.state in TERMINAL or run.state == "unknown":
        raise Problem(409, "invalid_state", "当前状态不能直接取消，请查看核对信息")
    run.cancel_requested = True
    if run.state in {"queued", "waiting_input"}:
        run.state, run.finished_at = "canceled", time.time()
        release(db, run)
    else:
        run.state = "cancel_requested"
    audit(db, who, "cancel_run", run.id, run.project_id)
    return run_view(db, run)


@router.post("/runs/{run_id}/reconcile")
def reconcile(run_id: str, payload: S.Reconcile, who=Depends(human), db=Depends(get_db, scope="function")):
    write_lock(db, who)
    run = require(db, Run, run_id)
    authorize_run(db, who, run, "project_admin")
    if run.state != "unknown":
        raise Problem(409, "invalid_state", "只有待核对运行可执行此操作")
    run.state, run.finished_at = payload.outcome, time.time()
    release(db, run)
    audit(db, who, "reconcile_run", run.id, run.project_id, payload.note)
    return run_view(db, run)


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
def artifact_download(run_id: str, artifact_id: str, request: Request, who=Depends(identity), db=Depends(get_db, scope="function")):
    run = require(db, Run, run_id)
    authorize_run(db, who, run)
    item = require(db, Artifact, artifact_id)
    if item.run_id != run.id:
        raise Problem(404, "not_found", "文件不存在")
    require_available(db, "artifact", item.id)
    audit(db, who, "download_artifact", item.id, run.project_id)
    return FileResponse(object_path(request.app.state.settings, item.sha256), filename=item.name,
                        media_type="application/octet-stream")
