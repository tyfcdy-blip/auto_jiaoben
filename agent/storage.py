import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from .config import AgentError


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name == "posix":
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def safe_name(name: str, *, nested: bool = False) -> str:
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name or "\x00" in name:
        raise AgentError("文件名无效")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise AgentError("拒绝越界文件路径")
    if not nested and len(path.parts) != 1:
        raise AgentError("此处仅允许单层文件名")
    for part in path.parts:
        if re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")):
            raise AgentError("文件名包含不安全字符")
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise AgentError("文件名为系统保留名称")
    return name


def extract_bundle(archive: Path, destination: Path, expanded_limit: int) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 1000:
            raise AgentError("程序包文件数超过上限")
        names: set[str] = set()
        total = 0
        for member in members:
            name = safe_name(member.filename.rstrip("/") if member.is_dir() else member.filename, nested=True)
            if name.casefold() in names:
                raise AgentError("程序包包含重复或大小写冲突路径")
            names.add(name.casefold())
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise AgentError("程序包不允许链接或特殊文件")
            if member.flag_bits & 1:
                raise AgentError("不接受加密程序包")
            total += member.file_size
            if total > expanded_limit:
                raise AgentError("程序包解压容量超过上限")
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.filename).parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with bundle.open(member) as source, target.open("xb") as output:
                while chunk := source.read(65536):
                    written += len(chunk)
                    if written > member.file_size:
                        raise AgentError("解压数据与文件清单不一致")
                    output.write(chunk)


class StateLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"1")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.handle.close()
            raise AgentError("同一状态目录已有执行器运行") from error
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
