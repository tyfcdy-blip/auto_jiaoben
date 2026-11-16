import ctypes
import os
import signal
import subprocess
from ctypes import wintypes

from .config import AgentError


class ProcessTree:
    """Windows Job 和 POSIX 独立进程组；业务进程必须在 attach 后解锁启动。"""

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.job = None
        if os.name != "nt":
            return
        class BasicLimit(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]
        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("basic", BasicLimit), ("io", IoCounters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.job = self.kernel.CreateJobObjectW(None, None)
        limits = ExtendedLimit()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.job or not self.kernel.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise AgentError("无法建立 Windows 进程树约束")
        if not self.kernel.AssignProcessToJobObject(self.job, wintypes.HANDLE(int(process._handle))):
            self.close()
            raise AgentError("无法将启动器加入 Windows Job，业务程序尚未启动")

    def has_live_members(self) -> bool:
        if os.name == "nt":
            class Accounting(ctypes.Structure):
                _fields_ = [("user", ctypes.c_longlong), ("kernel", ctypes.c_longlong),
                            ("period_user", ctypes.c_longlong), ("period_kernel", ctypes.c_longlong),
                            ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                            ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]
            accounting = Accounting()
            if not self.kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise AgentError("无法核验剩余子进程状态")
            return accounting.active > 0
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop(self) -> None:
        if os.name == "nt":
            if self.job and not self.kernel.TerminateJobObject(self.job, 124):
                raise AgentError("无法确认 Windows 进程树终止")
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise AgentError("进程树停止超时，结果待核对") from error

    def close(self) -> None:
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
