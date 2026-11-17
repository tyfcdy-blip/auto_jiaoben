"""执行电脑文件的控制面；服务器从不解析或打开电脑上的文件。"""
import time
from sqlalchemy import select
from .input_sources import canonical_root, roots_are_subset
from .models import Node, NodeConfigState, NodeFileConfig, RunFileOptions, TaskFileOptions, Version, Run, Schedule
from .node_config import configuration_ready, grant_for
from .security import Problem

CAPABILITY = "node-files-v1"


def file_fields(schema: dict) -> dict[str, str]:
    fields = {}
    for name, definition in schema.get("properties", {}).items():
        if not isinstance(definition, dict):
            continue
        kind = definition.get("x-workbench-file")
        if kind is None:
            continue
        if (not isinstance(kind, str) or kind not in {"file", "files", "directory"}
                or definition.get("type") != ("array" if kind == "files" else "string")
                or (kind == "files" and (not isinstance(definition.get("items"), dict)
                                        or definition["items"].get("type") != "string"))):
            raise Problem(422, "invalid_file_schema", "文件参数必须声明file/files/directory及对应字符串类型")
        fields[name] = kind
    return fields


def output_roots(db, node_id: str, project_id: str) -> list[str]:
    config = db.get(NodeFileConfig, node_id)
    if not config:
        return []
    return next((grant["output_roots"] for grant in config.grants
                 if grant["project_id"] == project_id), [])


def ready_node(db, node_id: str, project_id: str, *, settings=None, write=False):
    node = db.get(Node, node_id)
    grant = grant_for(db, node_id, project_id)
    if not node or not node.enabled or not grant:
        raise Problem(403, "node_grant_revoked", "电脑已停用或项目目录授权已撤销")
    if CAPABILITY not in node.capabilities:
        raise Problem(409, "file_agent_unsupported", "请更新执行器后使用电脑文件选择与结果另存")
    if settings and (not node.last_seen or time.time() - node.last_seen > settings.node_timeout):
        raise Problem(409, "node_offline", "执行电脑离线，请连接执行器后重试")
    state = db.get(NodeConfigState, node_id)
    if not state or not configuration_ready(state):
        raise Problem(409, "file_config_pending", "执行电脑尚未同步目录配置")
    config = db.get(NodeFileConfig, node_id)
    if write and (not config or config.apply_status != "applied" or config.applied_revision != config.revision):
        raise Problem(409, "file_config_pending", "执行电脑尚未同步结果目录配置")
    return node, grant, state, config


def validate_options(db, task, options: dict, *, settings=None) -> tuple[dict, int, int]:
    inputs, directory = options.get("inputs", {}), options.get("output_dir")
    if not inputs and directory is None:
        return {"inputs": {}, "output_dir": None}, 0, 0
    if not task.node_id:
        raise Problem(422, "file_node_required", "使用电脑文件需先指定执行电脑")
    _, grant, state, config = ready_node(db, task.node_id, task.project_id, settings=settings, write=directory is not None)
    fields = file_fields(db.get(Version, task.version_id).parameter_schema)
    for name, selection in inputs.items():
        kind = fields.get(name)
        if kind is None:
            raise Problem(422, "unknown_file_parameter", "文件映射必须引用程序声明的文件参数")
        paths = selection if kind == "files" else [selection]
        if (not isinstance(paths, list) or not paths or len(paths) > 100
                or any(not isinstance(path, str) for path in paths)):
            raise Problem(422, "invalid_file_selection", "文件参数类型不匹配或超过100项")
        if not roots_are_subset(paths, grant.input_roots):
            raise Problem(422, "file_outside_grant", "所选输入不在项目授权读取目录内")
    if directory is not None and not roots_are_subset([directory], output_roots(db, task.node_id, task.project_id)):
        raise Problem(422, "output_outside_grant", "结果目录不在项目授权写入目录内")
    return {"inputs": inputs, "output_dir": directory}, state.applied_revision, config.applied_revision if config else 0


def task_options(db, task_id: str) -> dict:
    record = db.get(TaskFileOptions, task_id)
    return record.options if record else {"inputs": {}, "output_dir": None}


def set_task_options(db, task, options: dict) -> dict:
    options, _, _ = validate_options(db, task, options)
    record = db.get(TaskFileOptions, task.id)
    previous = record.options if record else {"inputs": {}, "output_dir": None}
    if options != previous:
        if db.scalar(select(Run.id).where(Run.task_id == task.id,
                     Run.state.in_(["waiting_input", "queued", "leased", "running", "cancel_requested", "unknown"])).limit(1)):
            raise Problem(409, "task_in_use", "任务仍有未结束或待核对运行，暂不能修改文件配置")
        if db.scalar(select(Schedule.id).where(Schedule.task_id == task.id, Schedule.enabled.is_(True)).limit(1)):
            raise Problem(409, "active_schedules", "请先暂停该任务的全部定时计划，再修改文件配置")
    # 专用文件设置与任务原子编辑共用这一处默认值同步，清除选择不能留下隐藏的旧路径。
    defaults = dict(task.default_parameters)
    for name in previous["inputs"]:
        defaults.pop(name, None)
    defaults.update(options["inputs"])
    from .storage import validate_parameters
    validate_parameters(db.get(Version, task.version_id).parameter_schema, defaults, partial=True)
    task.default_parameters = defaults
    if not record and not options["inputs"] and options["output_dir"] is None:
        return options
    if not record:
        record = TaskFileOptions(task_id=task.id, options=options)
        db.add(record)
    else:
        record.options = options
    return options


def run_file_payload(db, run) -> dict | None:
    record = db.get(RunFileOptions, run.id)
    if not record:
        return None
    _, grant, state, config = ready_node(db, record.node_id, run.project_id,
                                       write=record.options.get("output_dir") is not None)
    if (record.node_id != (run.node_id or record.node_id)
            or state.applied_revision != record.config_revision
            or (record.options.get("output_dir") and config.applied_revision != record.output_config_revision)):
        raise Problem(409, "file_config_changed", "文件授权配置已变更，请核对后重新提交")
    paths = [path for value in record.options["inputs"].values()
             for path in (value if isinstance(value, list) else [value])]
    allowed_output = output_roots(db, record.node_id, run.project_id)
    if (not roots_are_subset(paths, grant.input_roots)
            or (record.options.get("output_dir") and not roots_are_subset([record.options["output_dir"]], allowed_output))):
        raise Problem(403, "file_grant_revoked", "运行文件授权已撤销")
    return {"config_revision": record.config_revision, "output_config_revision": record.output_config_revision,
            "allowed_input_roots": grant.input_roots, "allowed_output_roots": allowed_output,
            "input_kinds": {name: kind for name, kind in file_fields(db.get(Version, run.version_id).parameter_schema).items()
                            if name in record.options["inputs"]},
            **record.options}


def run_file_status(db, run, records: dict | None = None) -> dict | None:
    record = db.get(RunFileOptions, run.id) if records is None else records.get(run.id)
    if not record:
        return None
    state, error = record.save_state, record.save_error
    if state == "pending" and run.finished_at and not run.started_at:
        state, error = "failed", "程序未启动，没有可另存的结果文件"
    elif record.retry_expires_at and state == "pending" and time.time() > record.retry_expires_at:
        state, error = "failed", "另存响应超时，可再次重试；业务脚本不会重跑"
    elif state == "pending" and run.finished_at and time.time() > run.finished_at + 300:
        state, error = "failed", "程序已结束但电脑未确认另存结果，请检查连接后重试另存"
    return {"enabled": True, **record.options, "save_state": state, "saved_files": record.saved_files,
            "save_error": error, "retry_number": record.retry_number}


def direct_child(parent: str, child: str) -> bool:
    flavor, root = canonical_root(parent)
    child_flavor, path = canonical_root(child)
    prefix = root.rstrip("/") + "/"
    return flavor == child_flavor and path.startswith(prefix) and "/" not in path[len(prefix):]
