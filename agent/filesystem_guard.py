"""在实际文件访问期间固定目录身份，避免路径校验后的链接替换竞态。"""

from contextlib import contextmanager
import os
from pathlib import Path
import stat

from .config import AgentError


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create.restype = wintypes.HANDLE
    _information = _kernel.GetFileInformationByHandleEx
    _information.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _information.restype = wintypes.BOOL
    _close = _kernel.CloseHandle
    _close.argtypes = [wintypes.HANDLE]
    _close.restype = wintypes.BOOL

    def _windows_handle(path: Path, *, directory: bool, write: bool = False, protect_write: bool = False):
        # 不授予FILE_SHARE_DELETE：句柄存活期间，当前目录与每级祖先不可改名/替换。
        # 仅FILE_READ_ATTRIBUTES属于元数据访问，Windows不会据此可靠约束删除共享。
        # FILE_LIST_DIRECTORY使共享检查生效，持锁失败时拒绝该次访问。
        access = 0x80 | 0x1 if directory else (0x40000000 if write else 0x80000000)
        # 目录同时拒绝写共享，阻止持锁期间把同一目录原地设置为重解析点。
        sharing = 0x1 if directory and protect_write else 0x1 | 0x2
        handle = _create(str(path), access, sharing, None, 1 if write else 3,
                         0x00200000 | (0x02000000 if directory else 0), None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = _AttributeTag()
        if not _information(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            error = ctypes.WinError(ctypes.get_last_error())
            _close(handle)
            raise error
        if attributes.attributes & 0x400 or bool(attributes.attributes & 0x10) != directory:
            _close(handle)
            raise AgentError("文件访问拒绝重解析点或不匹配的文件类型")
        return handle


class GuardedDirectory:
    """Windows持有整条无删除共享链；POSIX所有子操作相对于已打开目录fd。"""

    def __init__(self, path: Path):
        self.path = path
        self.handles: list = []

    def __enter__(self):
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise AgentError("受保护目录必须是绝对路径")
        try:
            if os.name == "nt":
                for directory in reversed((self.path, *self.path.parents)):
                    self.handles.append(_windows_handle(directory, directory=True,
                                                        protect_write=directory == self.path))
            else:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                self.handles.append(os.open(self.path.anchor, flags))
                for part in self.path.parts[1:]:
                    self.handles.append(os.open(part, flags, dir_fd=self.handles[-1]))
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        for handle in reversed(self.handles):
            _close(handle) if os.name == "nt" else os.close(handle)
        self.handles.clear()

    def __exit__(self, *_):
        self.close()

    def mkdir(self, name: str) -> None:
        if os.name == "nt":
            (self.path / name).mkdir(exist_ok=False)
        else:
            os.mkdir(name, dir_fd=self.handles[-1])

    def scan(self):
        return os.scandir(self.path if os.name == "nt" else self.handles[-1])

    def stat_file(self, name: str):
        if os.name == "nt":
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.handles[-1], follow_symlinks=False)

    @contextmanager
    def open_file(self, name: str, *, write: bool = False):
        if os.name == "nt":
            handle = _windows_handle(self.path / name, directory=False, write=write)
            try:
                descriptor = msvcrt.open_osfhandle(handle, (os.O_WRONLY if write else os.O_RDONLY) | os.O_BINARY)
            except BaseException:
                _close(handle)
                raise
        else:
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
            descriptor = os.open(name, flags, 0o600, dir_fd=self.handles[-1])
        with os.fdopen(descriptor, "wb" if write else "rb") as stream:
            observed = os.fstat(stream.fileno())
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise AgentError("文件访问仅允许无硬链接的普通文件")
            yield stream
