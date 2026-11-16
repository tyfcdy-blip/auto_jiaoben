"""受信任开发任务的目录输入校验；直接读取，不冻结、复制或提供 OS 隔离。"""

from collections.abc import Callable
import hashlib
import os
from pathlib import Path
import re
import stat

from .config import AgentError
from .storage import safe_name


CAPABILITY = "directory-input-v1"
MAX_FILES = 1000
MAX_DIRECTORIES = 1000
MAX_DEPTH = 32


def _regular_status(path: Path, *, directory: bool) -> os.stat_result:
    observed = path.lstat()
    if stat.S_ISLNK(observed.st_mode) or getattr(observed, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise AgentError("目录输入不允许符号链接或重解析点")
    if directory:
        if not stat.S_ISDIR(observed.st_mode):
            raise AgentError("输入路径必须是普通目录")
    elif not stat.S_ISREG(observed.st_mode) or observed.st_nlink > 1:
        raise AgentError("目录输入只接受普通文件，不接受设备或硬链接")
    return observed


def checked_directory(value: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096 or re.match(r"^(?:\\\\[?.]\\|//[?.]/)", value):
        raise AgentError("输入目录路径无效或为设备路径")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise AgentError("输入目录必须是无越界段的绝对路径")
    for part in path.parts[1:]:
        safe_name(part)
    for ancestor in reversed((path, *path.parents)):
        _regular_status(ancestor, directory=True)
    resolved = path.resolve(strict=True)
    if resolved != path:
        raise AgentError("输入目录不能通过链接或别名重定向")
    return resolved


def validate_roots(values: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > 30:
        raise AgentError("input_roots 必须是最多 30 项的绝对目录列表")
    roots = []
    for value in values:
        if (not isinstance(value, str) or not value or len(value) > 4096
                or re.match(r"^(?:\\\\[?.]\\|//[?.]/)", value)):
            raise AgentError("目录白名单包含无效或设备路径")
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts:
            raise AgentError("目录白名单必须使用无越界段的绝对路径")
        for part in path.parts[1:]:
            safe_name(part)
        # 服务端摘要按登记原文计算；这里只校验，不改写 Windows 斜杠形式。
        root = value
        if root not in roots:
            roots.append(root)
    return roots


def _lexically_contains(root: str, child: str) -> bool:
    """在访问磁盘前判断路径边界，避免无关的离线共享目录阻塞任务。"""
    normalized_root = os.path.normcase(os.path.normpath(root))
    normalized_child = os.path.normcase(os.path.normpath(child))
    try:
        return os.path.commonpath((normalized_root, normalized_child)) == normalized_root
    except ValueError:
        return False


def _signature(observed: os.stat_result, *, handle: bool = False) -> tuple:
    values = (observed.st_dev, observed.st_ino, observed.st_mode, observed.st_nlink,
              observed.st_size, observed.st_mtime_ns)
    # Windows对刚原子保存的Office文件，path stat与handle fstat可能返回不同的ctime语义。
    # 句柄内核对仍比较身份、类型、链接数、大小和mtime；路径读取前后继续比较ctime。
    return values if handle else (*values, observed.st_ctime_ns)


def _hash_file(path: Path, before: os.stat_result, progress: Callable[[], None]) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if _signature(os.fstat(stream.fileno()), handle=True) != _signature(before, handle=True):
            raise AgentError("目录输入在打开文件时发生变动")
        digest, count = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            count += len(chunk)
            if count > before.st_size:
                raise AgentError("目录输入在读取时增长")
            digest.update(chunk)
            progress()
        if (count != before.st_size
                or _signature(os.fstat(stream.fileno()), handle=True) != _signature(before, handle=True)):
            raise AgentError("目录输入在读取时发生变动")
    if _signature(_regular_status(path, directory=False)) != _signature(before):
        raise AgentError("目录输入在读取后发生变动")
    return digest.hexdigest()


def directory_manifest(value: str, roots: list[str], byte_limit: int,
                       progress: Callable[[], None] = lambda: None,
                       project_roots: list[str] | None = None) -> list[dict[str, str]]:
    if not roots:
        raise AgentError("执行电脑未配置目录输入白名单")
    host_values = validate_roots(roots)
    selected_values = host_values
    if project_roots is not None:
        selected_values = validate_roots(project_roots)
        if any(not any(_lexically_contains(host, project) for host in host_values)
               for project in selected_values):
            raise AgentError("项目目录授权不在执行电脑白名单内")
    if not any(_lexically_contains(root, value) for root in selected_values):
        raise AgentError("输入目录不在执行电脑白名单内")

    # 只访问与本次输入相关的根目录。其他盘符或 SMB 共享即使离线，也不影响本次任务。
    relevant_values = [root for root in selected_values if _lexically_contains(root, value)]
    allowed = [checked_directory(root) for root in relevant_values]
    selected = checked_directory(value)
    if not any(selected.is_relative_to(root) for root in allowed):
        raise AgentError("输入目录不在执行电脑白名单内")
    pending = [(selected, 0)]
    directories: dict[Path, os.stat_result] = {}
    directory_entries: dict[Path, set[str]] = {}
    files: list[tuple[Path, str, os.stat_result]] = []
    names: set[str] = set()
    total = 0
    while pending:
        directory, depth = pending.pop()
        if depth > MAX_DEPTH or len(directories) >= MAX_DIRECTORIES:
            raise AgentError("输入目录层级或目录数量超过上限")
        directories[directory] = _regular_status(directory, directory=True)
        directory_entries[directory] = set()
        # scandir 迭代而不是先加载整个目录；空子目录也受数量限制。
        with os.scandir(directory) as entries:
            for entry in entries:
                directory_entries[directory].add(entry.name)
                path = directory / entry.name
                name = safe_name(path.relative_to(selected).as_posix(), nested=True)
                if name.casefold() in names:
                    raise AgentError("输入目录存在大小写冲突名称")
                names.add(name.casefold())
                observed = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(observed.st_mode) or getattr(observed, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise AgentError("目录输入不允许符号链接或重解析点")
                if stat.S_ISDIR(observed.st_mode):
                    if len(directories) + len(pending) >= MAX_DIRECTORIES:
                        raise AgentError("输入目录数量超过上限")
                    pending.append((path, depth + 1))
                else:
                    observed = _regular_status(path, directory=False)
                    total += observed.st_size
                    if len(files) >= MAX_FILES or total > byte_limit:
                        raise AgentError("目录输入文件数量或总容量超过上限")
                    files.append((path, name, observed))
                progress()
    if not files:
        raise AgentError("输入目录为空，没有可处理文件")
    manifest = [{"name": name, "path": str(path), "sha256": _hash_file(path, observed, progress)}
                for path, name, observed in sorted(files, key=lambda row: row[1])]
    # 避免后读文件掩盖已读文件的修改；仍不承诺检查结束后的文件冻结。
    for path, _, observed in files:
        if _signature(_regular_status(path, directory=False)) != _signature(observed):
            raise AgentError("目录输入在生成清单期间发生变动")
    for path, observed in directories.items():
        checked_directory(str(path))
        if _signature(_regular_status(path, directory=True)) != _signature(observed):
            raise AgentError("输入目录清单在读取期间发生变动")
        current_entries: set[str] = set()
        with os.scandir(path) as entries:
            for entry in entries:
                current_entries.add(entry.name)
                if len(current_entries) > len(directory_entries[path]):
                    raise AgentError("输入目录清单在读取期间发生变动")
                progress()
        if current_entries != directory_entries[path]:
            raise AgentError("输入目录清单在读取期间发生变动")
    return manifest
