"""输入与运行产物的保留策略；先逻辑过期，再安全回收无引用对象。"""
import os
import re
import time
from sqlalchemy import exists, or_, select
from .db import lock
from .models import (Artifact, Batch, BatchFile, FileRetention, LoopStatus, Run, Schedule,
                     Version)
from .security import Problem
from .storage import storage_usage

TERMINAL_STATES = {"succeeded", "failed", "partial", "canceled"}
KINDS = {"input", "artifact"}


def retention_record(db, kind, record_id):
    return db.get(FileRetention, {"kind": kind, "record_id": record_id})


def file_view(db, kind, record, public):
    result = public(record)
    retained = retention_record(db, kind, record.id)
    result["retention_state"] = "expired" if retained else "active"
    result["expired_at"] = public(retained)["expired_at"] if retained else None
    return result


def file_views(db, kind, records, public):
    records = list(records)
    identifiers = [record.id for record in records]
    retained = {item.record_id: item for item in db.scalars(select(FileRetention).where(
        FileRetention.kind == kind, FileRetention.record_id.in_(identifiers))).all()} if identifiers else {}
    results = []
    for record in records:
        result = public(record)
        item = retained.get(record.id)
        result["retention_state"] = "expired" if item else "active"
        result["expired_at"] = public(item)["expired_at"] if item else None
        results.append(result)
    return results


def require_available(db, kind, record_id):
    if retention_record(db, kind, record_id):
        raise Problem(410, "file_expired", "文件已超过保留期限，元数据仍保留")


def batch_has_expired_files(db, batch_id):
    return bool(db.scalar(select(FileRetention.record_id).join(
        BatchFile, BatchFile.id == FileRetention.record_id).where(
            FileRetention.kind == "input", BatchFile.batch_id == batch_id).limit(1)))


def batch_retention_state(db, batch_id):
    return batch_retention_states(db, [batch_id])[batch_id]


def batch_retention_states(db, batch_ids):
    batch_ids = list(batch_ids)
    if not batch_ids:
        return {}
    expired = set(db.scalars(select(BatchFile.batch_id).join(
        FileRetention,
        (FileRetention.kind == "input") & (FileRetention.record_id == BatchFile.id),
    ).where(BatchFile.batch_id.in_(batch_ids)).distinct()))
    active = set(db.scalars(select(BatchFile.batch_id).where(
        BatchFile.batch_id.in_(batch_ids), active_file_condition("input", BatchFile)).distinct()))
    return {batch_id: ("partial" if batch_id in active else "expired")
            if batch_id in expired else "active" for batch_id in batch_ids}


def active_file_condition(kind, model):
    return ~exists(select(FileRetention.record_id).where(
        FileRetention.kind == kind, FileRetention.record_id == model.id))


def mark_expired(db, settings, now):
    input_cutoff = now - settings.input_retention_days * 86400
    artifact_cutoff = now - settings.artifact_retention_days * 86400
    input_budget = (settings.cleanup_batch_size + 1) // 2
    marked = 0

    protected_run = exists(select(Run.id).where(
        or_(Run.input_batch_id == Batch.id,
            (Run.project_id == Batch.project_id) & (Run.expected_input_label == Batch.label)),
        ~Run.state.in_(TERMINAL_STATES),
    ))
    protected_schedule = exists(select(Schedule.id).where(
        or_(Schedule.input_batch_id == Batch.id,
            (Schedule.project_id == Batch.project_id) & (Schedule.input_label == Batch.label)),
    ))
    input_rows = db.execute(select(BatchFile, Batch.project_id).join(
        Batch, Batch.id == BatchFile.batch_id).where(
            BatchFile.created_at <= input_cutoff,
            active_file_condition("input", BatchFile),
            ~protected_run,
            ~protected_schedule,
        ).order_by(BatchFile.created_at, BatchFile.id).limit(input_budget)).all()
    for item, project_id in input_rows:
        db.add(FileRetention(kind="input", record_id=item.id, project_id=project_id,
                             sha256=item.sha256, size=item.size, expired_at=now, next_try=now))
        marked += 1
    remaining = settings.cleanup_batch_size - len(input_rows)

    if remaining:
        artifact_rows = db.execute(select(Artifact, Run.project_id).join(
            Run, Run.id == Artifact.run_id).where(
                Artifact.created_at <= artifact_cutoff,
                Run.finished_at.is_not(None), Run.finished_at <= artifact_cutoff,
                Run.state.in_(TERMINAL_STATES),
                active_file_condition("artifact", Artifact),
            ).order_by(Artifact.created_at, Artifact.id).limit(remaining)).all()
        for item, project_id in artifact_rows:
            db.add(FileRetention(kind="artifact", record_id=item.id, project_id=project_id,
                                 sha256=item.sha256, size=item.size, expired_at=now, next_try=now))
            marked += 1
    return marked


def _live_shas(db, shas):
    shas = list(shas)
    if not shas:
        return set()
    live = set(db.scalars(select(Version.bundle_sha256).where(Version.bundle_sha256.in_(shas))))
    live.update(db.scalars(select(BatchFile.sha256).where(
        BatchFile.sha256.in_(shas), active_file_condition("input", BatchFile))))
    live.update(db.scalars(select(Artifact.sha256).where(
        Artifact.sha256.in_(shas), active_file_condition("artifact", Artifact))))
    return live


def delete_expired_objects(db, settings, now):
    rows = db.scalars(select(FileRetention).where(
        FileRetention.deletion_state.in_(["pending", "shared", "failed"]),
        FileRetention.next_try <= now,
    ).order_by(FileRetention.next_try, FileRetention.record_id).limit(settings.cleanup_batch_size)).all()
    live_shas = _live_shas(db, {retained.sha256 for retained in rows})
    processed = 0
    for retained in rows:
        if retained.sha256 in live_shas:
            retained.deletion_state = "shared"
            retained.attempts = 0
            retained.next_try = now + max(3600, settings.cleanup_interval_seconds)
            retained.last_error = None
            continue
        path = settings.data_dir / "objects" / retained.sha256
        try:
            if not re.fullmatch(r"[a-f0-9]{64}", retained.sha256) or path.is_symlink():
                raise OSError("unsafe_object")
            if path.exists():
                if not path.is_file():
                    raise OSError("unsafe_object")
                os.unlink(path)
            retained.deletion_state = "deleted"
            retained.attempts = 0
            retained.deleted_at = now
            retained.next_try = 0
            retained.last_error = None
            processed += 1
        except OSError as exc:
            retained.attempts += 1
            retained.deletion_state = "failed"
            retained.last_error = type(exc).__name__
            retained.next_try = now + min(3600, settings.cleanup_interval_seconds * 2 ** min(retained.attempts, 4))
    return processed


def cleanup_files(factory, settings, now=None):
    now = now or time.time()
    with factory.begin() as db:
        lock(db)
        previous = db.get(LoopStatus, "storage_cleanup")
        if previous and now - previous.last_success < settings.cleanup_interval_seconds:
            return {"marked": 0, "deleted": 0, "skipped": True}
        marked = mark_expired(db, settings, now)
        status = db.get(LoopStatus, "storage_cleanup")
        if status:
            status.last_success = now
        else:
            db.add(LoopStatus(name="storage_cleanup", last_success=now))
    # 逻辑过期先独立提交；即使物理删除后进程崩溃，下载也保持410，下一轮可幂等补记状态。
    with factory.begin() as db:
        lock(db)
        deleted = delete_expired_objects(db, settings, now)
    return {"marked": marked, "deleted": deleted, "skipped": False}


def storage_health(settings):
    status = storage_usage(settings)
    status["healthy"] = (status["used_bytes"] <= settings.storage_max_bytes
                         and status["free_bytes"] >= settings.storage_min_free_bytes)
    return status
