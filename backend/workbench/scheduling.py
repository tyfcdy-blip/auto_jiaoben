from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import time
from croniter import croniter, CroniterBadCronError
from sqlalchemy import select
from .execution import ACTIVE, admit, alert, fail_without_start, recover_alert
from .models import Alert, Batch, Gate, LoopStatus, Node, NodeProjectGrant, Run, Schedule, Task
from .security import Problem


def next_fire(expression, timezone_name, after):
    try:
        if len(expression.split()) != 5 or not croniter.is_valid(expression):
            raise ValueError()
        # 一期只启用无夏令时的两种明确时区，避免静默决定重复/跳过时刻语义。
        if timezone_name not in {"Asia/Shanghai", "UTC"}:
            raise ValueError()
        local = datetime.fromtimestamp(after, ZoneInfo(timezone_name))
        return croniter(expression, local).get_next(datetime).timestamp()
    except (ValueError, ZoneInfoNotFoundError, CroniterBadCronError):
        raise Problem(422, "invalid_schedule", "需要有效的五字段 cron，时区支持 Asia/Shanghai 或 UTC") from None


def resolve_dates(parameters, timestamp, zone):
    local = datetime.fromtimestamp(timestamp, ZoneInfo(zone))
    previous_month = (local.replace(day=1).timestamp() - 86400)
    values = {"${scheduled_date}": local.strftime("%Y-%m-%d"),
              "${previous_month}": datetime.fromtimestamp(previous_month, ZoneInfo(zone)).strftime("%Y-%m")}
    def resolve(value):
        if isinstance(value, str):
            if "${" in value and value not in values:
                raise Problem(422, "invalid_expression", "日期表达式不在允许列表中")
            return values.get(value, value)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value
    return resolve(parameters)


def watermark(db, name, now):
    record = db.get(LoopStatus, name)
    if record:
        record.last_success = now
    else:
        db.add(LoopStatus(name=name, last_success=now))


def scan_schedules(db, settings, now=None):
    now = now or time.time()
    gate = db.get(Gate, 1)
    if settings.dispatch_enabled and gate.dispatch_enabled:
        schedules = db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_fire <= now)
                               .order_by(Schedule.next_fire).limit(100)).all()
        for schedule in schedules:
            due = schedule.next_fire
            # skip只跳过错过至少一分钟的时刻；once保留最早漏触发时刻的参数快照。
            if schedule.misfire_policy == "skip" and now-due >= 60:
                schedule.next_fire = next_fire(schedule.cron, schedule.timezone, now)
                schedule.last_error = "错过计划时间，已按“跳过”策略顺延至下一次"
                message = f"定时计划“{schedule.name}”（{schedule.id}）{schedule.last_error}"
                alert(db, schedule.project_id, "schedule_misfire", message,
                      f"schedule-misfire:{schedule.id}:{schedule.revision}:{int(due)}")
                continue
            task = db.get(Task, schedule.task_id)
            try:
                resolved = resolve_dates({**task.default_parameters, **schedule.parameters}, due, schedule.timezone)
                expected_label = resolve_dates(schedule.input_label, due, schedule.timezone) if schedule.input_label else None
                admit(db, settings, task, "schedule:"+schedule.id,
                      f"{schedule.revision}:{int(due)}", resolved, schedule.input_batch_id, "schedule", expected_label)
                schedule.last_error = None
                missed = db.scalars(select(Alert).where(
                    Alert.kind == "schedule_misfire",
                    Alert.status.in_(["open", "acknowledged"]),
                    Alert.event_key.like(f"schedule-misfire:{schedule.id}:%"),
                )).all()
                for record in missed:
                    recover_alert(
                        db,
                        record,
                        f"定时计划“{schedule.name}”（{schedule.id}）已在后续时刻成功受理",
                    )
            except Problem as exc:
                if exc.status == 429:
                    schedule.last_error = "队列已满，保留待受理计划时刻"
                    continue
                schedule.last_error = exc.message
                schedule.enabled = False
                alert(db, schedule.project_id, "schedule_failed", exc.message,
                      f"schedule:{schedule.id}:{schedule.revision}:{int(due)}")
            schedule.next_fire = next_fire(schedule.cron, schedule.timezone, now)
    watermark(db, "scheduler", now)


def scan_health(db, settings, now=None):
    now = now or time.time()
    runs = db.scalars(select(Run).where(Run.state.in_(["waiting_input", "queued", *ACTIVE]))).all()
    for run in runs:
        if run.state == "unknown":
            continue
        if run.state == "waiting_input":
            batch = db.get(Batch, run.input_batch_id) if run.input_batch_id else db.scalar(
                select(Batch).where(Batch.project_id == run.project_id, Batch.label == run.expected_input_label))
            if batch and batch.ready:
                run.input_batch_id = batch.id
                run.state = "queued"
            elif now-run.created_at > settings.input_timeout:
                fail_without_start(db, run, "等待输入超时")
        elif run.state == "queued" and now-run.created_at > settings.queue_timeout:
            fail_without_start(db, run, "排队超时，未启动程序")
        elif run.state == "queued" and now-run.created_at > 60:
            alert(db, run.project_id, "schedule_delayed", "任务尚未找到可执行节点或资源", "delay:"+run.id, run_id=run.id)
        elif run.state in ACTIVE:
            node = db.get(Node, run.node_id)
            task = db.get(Task, run.task_id)
            lost = not node.last_seen or now-node.last_seen > settings.node_timeout
            overdue = run.leased_at and now-run.leased_at > task.timeout_seconds+settings.node_timeout
            if lost or overdue:
                run.state, run.error = "unknown", "节点失联或运行超时，等待核对进程及业务结果"
                alert(db, run.project_id, "unknown", run.error, "run:"+run.id, run_id=run.id, node_id=run.node_id)
    for node in db.scalars(select(Node).where(Node.enabled.is_(True), Node.last_seen.is_not(None))):
        if now-node.last_seen > settings.node_timeout and not node.maintenance:
            projects = db.scalars(select(NodeProjectGrant.project_id).where(
                NodeProjectGrant.node_id == node.id,
                NodeProjectGrant.enabled.is_(True),
            )).all()
            for project_id in projects:
                alert(db, project_id, "node_offline", "执行节点已失联",
                      f"node:{node.id}:{project_id}:{int(node.last_seen)}", node_id=node.id)
    # 只根据可验证事实恢复对应条件；节点上线不能推断失联运行已成功。
    records = db.scalars(select(Alert).where(Alert.status.in_(["open", "acknowledged"]),
                                           Alert.kind.in_(["node_offline", "schedule_delayed"]))).all()
    for record in records:
        if record.kind == "node_offline":
            node = db.get(Node, record.node_id)
            if node and node.enabled and node.last_seen and now-node.last_seen <= settings.node_timeout:
                recover_alert(db, record, "节点心跳已恢复；原运行如仍待核对，须单独处理")
        else:
            run = db.get(Run, record.run_id)
            if run and run.state not in {"queued", "waiting_input"}:
                recover_alert(db, record, "运行已离开排队状态；最终结果请查看运行详情")
    watermark(db, "reconciler", now)
