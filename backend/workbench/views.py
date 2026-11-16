from datetime import datetime, timezone
from sqlalchemy import inspect, select, func
from .models import Member, Task

HIDDEN = {"password_hash", "token_hash", "encrypted_config", "completion_hash", "business_key"}


def public(record):
    result = {}
    for column in inspect(record).mapper.column_attrs:
        key = column.key
        if key in HIDDEN:
            continue
        value = getattr(record, key)
        if value is not None and (key.endswith("_at") or key in {"last_seen", "next_fire", "last_success"}):
            value = datetime.fromtimestamp(value, timezone.utc).isoformat()
        result[key] = value
    return result


def run_view(db, run):
    result = public(run)
    result["task_name"] = db.get(Task, run.task_id).name
    return result


def project_filter(db, who, query, model, project_id=None):
    if project_id:
        query = query.where(model.project_id == project_id)
    if not who.record.is_admin:
        projects = select(Member.project_id).where(Member.user_id == who.record.id)
        query = query.where(model.project_id.in_(projects))
    return query


def listing(db, query, limit=50, offset=0, transform=None):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    records = db.scalars(query.limit(limit).offset(offset)).all()
    return {"items": [(transform or public)(record) for record in records], "total": total}
