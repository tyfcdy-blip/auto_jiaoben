"""执行电脑授权目录浏览、选定输入校验及声明产物另存。"""

from collections.abc import Callable
import os
from pathlib import Path
import stat

from .config import AgentError
from .directory_input import (
    MAX_FILES, _hash_file, _lexically_contains, _regular_status, _signature, _validated_status,
    checked_directory, directory_manifest, validate_roots,
)
from .storage import safe_name
from .filesystem_guard import GuardedDirectory

CAPABILITY = "node-files-v1"


def authorized_path(value: str, host_roots: list[str], project_roots: list[str], *, directory: bool) -> Path:
    """先做词法授权，再接触磁盘；逐级拒绝链接/重解析点与设备路径。"""
    hosts, grants = validate_roots(host_roots), validate_roots(project_roots)
    if any(not any(_lexically_contains(host, grant) for host in hosts) for grant in grants):
        raise AgentError("项目文件授权超出执行电脑授权")
    validate_roots([value])
    relevant = [root for root in grants if _lexically_contains(root, value)]
    if not relevant:
        raise AgentError("所选路径不在本项目授权目录内")
    roots = [checked_directory(root) for root in relevant]
    path = Path(value)
    if directory:
        selected = checked_directory(value)
    else:
        checked_directory(str(path.parent))
        _regular_status(path, directory=False)
        selected = path.resolve(strict=True)
        if selected != path:
            raise AgentError("所选文件路径不能重定向")
    if not any(selected.is_relative_to(root) for root in roots):
        raise AgentError("所选路径越出本项目授权目录")
    return selected


def browse_directory(value: str, host_roots: list[str], project_roots: list[str], limit: int, offset: int = 0) -> dict:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise AgentError("浏览数量必须在1至200之间")
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise AgentError("浏览偏移无效")
    directory = authorized_path(value, host_roots, project_roots, directory=True)
    rows = []
    truncated = False
    with GuardedDirectory(directory) as guarded, guarded.scan() as entries:
        for entry in entries:
            if len(rows) >= 10000:
                raise AgentError("目录超过10000项，请选择更小的子目录")
            safe_name(entry.name)
            observed = guarded.stat_file(entry.name)
            is_directory = stat.S_ISDIR(observed.st_mode)
            observed = _validated_status(observed, directory=is_directory)
            rows.append({"name": entry.name, "path": str(directory / entry.name),
                         "kind": "directory" if is_directory else "file",
                         "size": None if is_directory else observed.st_size})
    checked_directory(str(directory))
    rows.sort(key=lambda row: (row["kind"] != "directory", row["name"].casefold()))
    truncated = len(rows) > offset + limit
    return {"entries": rows[offset:offset + limit], "truncated": truncated,
            "next_offset": offset + limit if truncated else None}


def selected_manifest(inputs: dict, parameters: dict, host_roots: list[str], project_roots: list[str],
                      byte_limit: int, progress: Callable[[], None], input_kinds: dict[str, str]) -> list[dict[str, str]]:
    manifest, names, paths = [], set(), set()
    total = 0
    if set(inputs) != set(input_kinds):
        raise AgentError("输入类型与选择参数不一致")
    for parameter, selected in inputs.items():
        if parameters.get(parameter) != selected:
            raise AgentError("任务文件参数与领取清单不一致")
        kind = input_kinds[parameter]
        if kind not in {"file", "files", "directory"} or isinstance(selected, list) != (kind == "files"):
            raise AgentError("文件参数形状与声明类型不一致")
        values = selected if isinstance(selected, list) else [selected]
        for value in values:
            # 授权验证在is_dir之前，禁止借探测任意路径存在性绕过授权。
            validate_roots([value])
            if not any(_lexically_contains(root, value) for root in project_roots):
                raise AgentError("所选输入不在本项目授权目录内")
            path = Path(value)
            if kind == "directory":
                authorized_path(value, host_roots, project_roots, directory=True)
                rows = directory_manifest(value, host_roots, byte_limit - total, progress, project_roots)
            else:
                path = authorized_path(value, host_roots, project_roots, directory=False)
                observed = _regular_status(path, directory=False)
                if observed.st_size > byte_limit - total:
                    raise AgentError("选定输入总容量超过上限")
                rows = [{"name": safe_name(path.name), "path": str(path), "sha256": _hash_file(path, observed, progress)}]
            for row in rows:
                normalized = os.path.normcase(row["path"])
                if normalized in paths:
                    continue
                if row["name"].casefold() in names:
                    raise AgentError("选定输入存在同名文件，请分开运行")
                total += _regular_status(Path(row["path"]), directory=False).st_size
                if len(manifest) >= MAX_FILES or total > byte_limit:
                    raise AgentError("选定输入文件数或总容量超过上限")
                manifest.append(row)
                names.add(row["name"].casefold())
                paths.add(normalized)
    return manifest


def export_artifacts(source: Path, names: list[str], destination: str, run_id: str,
                     host_roots: list[str], project_roots: list[str], byte_limit: int,
                     progress: Callable[[], None], on_saved: Callable[[dict], None],
                     retry_number: int = 0) -> list[dict[str, str]]:
    """创建独占运行目录。失败保留现场；再次请求不能覆盖或混入原目录。"""
    parent = authorized_path(destination, host_roots, project_roots, directory=True)
    target = parent / safe_name(run_id)
    checked_directory(str(source))
    with GuardedDirectory(parent) as parent_guard, GuardedDirectory(source) as source_guard:
        if retry_number:
            if not target.exists():
                parent_guard.mkdir(safe_name(run_id))
            with GuardedDirectory(target) as run_guard:
                run_guard.mkdir(f"retry-{retry_number}")
            target = target / f"retry-{retry_number}"
        else:
            parent_guard.mkdir(safe_name(run_id))
        with GuardedDirectory(target) as target_guard:
            files, seen = [], set()
            for name in names:
                safe_name(name)
                if name.casefold() in seen or name == "result.json":
                    raise AgentError("另存产物名称重复或保留")
                seen.add(name.casefold())
                path = source / name
                observed = _regular_status(path, directory=False)
                if observed.st_size > byte_limit:
                    raise AgentError("另存产物超过大小上限")
                with source_guard.open_file(name) as original, target_guard.open_file(name, write=True) as output:
                    if _signature(os.fstat(original.fileno()), handle=True) != _signature(observed, handle=True):
                        raise AgentError("另存产物在打开时变动")
                    count = 0
                    while chunk := original.read(65536):
                        count += len(chunk)
                        if count > observed.st_size:
                            raise AgentError("另存产物在复制时增长")
                        output.write(chunk)
                        progress()
                    output.flush()
                    os.fsync(output.fileno())
                    if count != observed.st_size or _signature(os.fstat(original.fileno()), handle=True) != _signature(observed, handle=True):
                        raise AgentError("另存产物在复制时变动")
                if _signature(_regular_status(path, directory=False)) != _signature(observed):
                    raise AgentError("另存产物在复制后变动")
                checked_directory(str(target))
                row = {"name": name, "path": str(target / name)}
                files.append(row)
                on_saved(row)
            return files
