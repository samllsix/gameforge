"""受限进程执行：给 Godot / Python 等不可信执行套资源笼。

Windows：Job Object（ctypes，零依赖）
  - 内存上限（ProcessMemoryLimit）
  - 进程数上限（ActiveProcessLimit，防 fork 炸弹）
  - CPU 时间上限（PerProcessUserTimeLimit）
  - KILL_ON_JOB_CLOSE：父进程退出自动清场（根治 Godot 僵尸进程泄漏）
Linux：resource.setrlimit（RLIMIT_AS / RLIMIT_NPROC / RLIMIT_CPU）

安全语义：fail-closed——Job Object 创建/关联失败则拒绝执行，绝不裸奔。
env 白名单：默认只放行系统必需变量，剥掉 GAMEFORGE_*/DB* 等敏感凭据。
网络：Windows 无轻量隔离方案（gd-guard 已拦 GDScript 网络层 API）；
     Linux 可选 unshare（需要权限，默认关闭）。
"""

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# 子进程允许继承的最小环境变量集（白名单，其余一律剥除）
_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "LANG",
    "LC_ALL",
    "HOME",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)


@dataclass
class RuntimePolicy:
    """单次执行的运行时限制。"""

    memory_limit_mb: int = 2048
    cpu_limit_percent: int = 100  # 记录语义；CPU 硬限制走 cpu_time 秒
    cpu_time_seconds: int = 120  # 进程累计 CPU 时间上限
    timeout_seconds: int = 120  # 墙钟超时（超时杀整个 Job）
    process_limit: int = 32
    network: bool = False  # 语义位：Phase 1 依赖 gd-guard 拦 API 层
    extra_env: Dict[str, str] = field(default_factory=dict)  # 显式追加的环境变量

    def sanitized_env(self) -> Dict[str, str]:
        """白名单环境：剥掉 GAMEFORGE_*/DB* 等敏感凭据。"""
        env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}
        env.update(self.extra_env)
        return env


@dataclass
class ExecutionOutcome:
    """受限执行结果。"""

    success: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    killed_reason: Optional[str] = None  # timeout / memory / cpu / none

    def to_dict(self) -> Dict[str, object]:
        return {
            "success": self.success,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "killed_reason": self.killed_reason,
        }


# ────────────────────────── Windows Job Object ──────────────────────────
if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.windll.kernel32

    # 显式签名：不声明时 ctypes 把句柄当 32 位 int 传，64 位 HANDLE 截断 → 87 错误
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),  # ULONG_PTR，值类型非指针；c_size_t 与其等宽
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_PROCESS_TIME = 0x0004
    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x0100
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _WindowsJobCage:
        """Job Object 资源笼。usage: with _WindowsJobCage(policy) as job: ..."""

        def __init__(self, policy: RuntimePolicy):
            self.policy = policy
            self._job = None

        def __enter__(self) -> "_WindowsJobCage":
            job = _kernel32.CreateJobObjectW(None, None)
            if not job:
                raise OSError("CreateJobObjectW failed")
            info = _JOBOBJECT_EXTENDED_LIMIT()
            base_flags = (
                _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            info.BasicLimitInformation.LimitFlags = base_flags | _JOB_OBJECT_LIMIT_PROCESS_TIME
            info.ProcessMemoryLimit = self.policy.memory_limit_mb * 1024 * 1024
            info.BasicLimitInformation.ActiveProcessLimit = self.policy.process_limit
            info.BasicLimitInformation.PerProcessUserTimeLimit = self.policy.cpu_time_seconds * 10_000_000

            ok = _kernel32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
            )
            if not ok:
                # 嵌套 Job 环境（本进程已被托管）不支持 PROCESS_TIME —— 去掉后重试，
                # CPU 失控由墙钟 timeout_seconds 兜底
                info.BasicLimitInformation.LimitFlags = base_flags
                ok = _kernel32.SetInformationJobObject(
                    job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
                )
            if not ok:
                _kernel32.CloseHandle(job)
                raise OSError(f"SetInformationJobObject failed: {ctypes.GetLastError()}")
            self._job = job
            return self

        def assign(self, proc: subprocess.Popen) -> None:
            if not _kernel32.AssignProcessToJobObject(self._job, int(proc._handle)):  # noqa: SLF001
                raise OSError(f"AssignProcessToJobObject failed: {ctypes.GetLastError()}")

        def terminate(self) -> None:
            _kernel32.TerminateJobObject(self._job, 1)

        def __exit__(self, *exc) -> None:
            if self._job:
                # KILL_ON_JOB_CLOSE 保证句柄关闭时整 Job 清场
                _kernel32.CloseHandle(self._job)
                self._job = None


# ────────────────────────── Linux rlimit ──────────────────────────
def _linux_preexec(policy: RuntimePolicy):
    import resource

    def _apply():
        resource.setrlimit(resource.RLIMIT_AS, (policy.memory_limit_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NPROC, (policy.process_limit,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_time_seconds,) * 2)

    return _apply


def run_isolated(
    cmd: List[str],
    policy: Optional[RuntimePolicy] = None,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdout_max: int = 200_000,
) -> ExecutionOutcome:
    """在资源笼里执行命令，超时杀整组进程。

    fail-closed：Windows 上 Job Object 创建/关联失败直接抛错，不裸奔执行。
    """
    policy = policy or RuntimePolicy()
    env = env if env is not None else policy.sanitized_env()
    t0 = time.monotonic()

    preexec = _linux_preexec(policy) if not _IS_WINDOWS else None

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            preexec_fn=preexec,
        )
    except OSError as e:
        raise OSError(f"沙箱进程启动失败（拒绝裸奔执行）: {e}") from e

    killed_reason = None
    try:
        if _IS_WINDOWS:
            with _WindowsJobCage(policy) as cage:
                cage.assign(proc)
                try:
                    out, err = proc.communicate(timeout=policy.timeout_seconds)
                except subprocess.TimeoutExpired:
                    cage.terminate()
                    killed_reason = "timeout"
                    out, err = proc.communicate(timeout=10)
        else:
            try:
                out, err = proc.communicate(timeout=policy.timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                killed_reason = "timeout"
                out, err = proc.communicate(timeout=10)
    except OSError as e:
        # Job Object 失败：进程已启动但没进笼——立即杀掉并拒绝（fail-closed）
        proc.kill()
        raise

    elapsed = time.monotonic() - t0
    outcome = ExecutionOutcome(
        success=proc.returncode == 0 and killed_reason is None,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=(out or "")[:stdout_max],
        stderr=(err or "")[:stdout_max],
        elapsed_seconds=elapsed,
        killed_reason=killed_reason,
    )
    logger.info(
        "sandbox.executed",
        cmd=cmd[0] if cmd else "?",
        exit_code=outcome.exit_code,
        elapsed=round(elapsed, 2),
        killed=killed_reason,
    )
    return outcome
