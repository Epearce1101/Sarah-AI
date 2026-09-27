"""Run an untrusted child process under OS-level limits.

On Windows the child is placed in a Job Object that:
  * kills the whole process tree when the job closes (timeouts can't leave
    grandchildren running),
  * caps committed memory for the job,
  * caps the number of live processes (default 1: the child can't spawn
    anything, whatever API it uses),
  * denies clipboard access, desktop switching, system-parameter changes and
    logoff/shutdown.

The child starts blocked on a "release" line read from stdin, so it cannot run
any code before it is inside the job. Output goes to temp files that a watchdog
caps, instead of unbounded in-memory pipes.

On POSIX the equivalent is a new session plus RLIMIT_AS / RLIMIT_NPROC.

This is containment, not a VM: file and network access still go through the
language-level layers in ``backend.sandbox``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

IS_WINDOWS = os.name == "nt"

RELEASE_LINE = b"go\n"

_ENV_PASSTHROUGH = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "OS", "LANG", "LC_ALL", "TZ",
)


@dataclass
class JailResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    limit_hit: Optional[str] = None  # "output" when the watchdog killed it
    peak_memory_mb: float = 0.0
    jailed: bool = False


def minimal_env(work_dir: Path, extra_path: Iterable[Path] = ()) -> Dict[str, str]:
    """An allowlisted environment: no API keys, tokens, or user profile paths."""
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    path_parts = [str(p) for p in extra_path]
    if IS_WINDOWS:
        path_parts.append(os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32"))
    else:
        path_parts += ["/usr/bin", "/bin"]
    env["PATH"] = os.pathsep.join(path_parts)
    for key in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE"):
        env[key] = str(work_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# ---------------------------------------------------------------------------
# Windows Job Object plumbing
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BASIC_UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    _JobObjectBasicUIRestrictions = 4
    _JobObjectExtendedLimitInformation = 9

    _LIMIT_ACTIVE_PROCESS = 0x00000008
    _LIMIT_JOB_MEMORY = 0x00000200
    _LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    _UILIMIT_ALL = (
        0x0001  # HANDLES: no USER handles from outside the job
        | 0x0002  # READCLIPBOARD
        | 0x0004  # WRITECLIPBOARD
        | 0x0008  # SYSTEMPARAMETERS
        | 0x0010  # DISPLAYSETTINGS
        | 0x0020  # GLOBALATOMS
        | 0x0040  # DESKTOP
        | 0x0080  # EXITWINDOWS
    )

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    def _create_job(memory_mb: int, max_processes: int) -> int:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _EXTENDED_LIMIT_INFORMATION()
        flags = _LIMIT_KILL_ON_JOB_CLOSE | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if memory_mb > 0:
            flags |= _LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_mb * 1024 * 1024
        if max_processes > 0:
            flags |= _LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = max_processes
        info.BasicLimitInformation.LimitFlags = flags
        ui = _BASIC_UI_RESTRICTIONS(_UILIMIT_ALL)
        ok = _k32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ) and _k32.SetInformationJobObject(
            job, _JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui)
        )
        if not ok:
            err = ctypes.get_last_error()
            _k32.CloseHandle(job)
            raise ctypes.WinError(err)
        return job

    def _peak_job_memory_mb(job: int) -> float:
        info = _EXTENDED_LIMIT_INFORMATION()
        if _k32.QueryInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            return info.PeakJobMemoryUsed / (1024 * 1024)
        return 0.0


def _posix_limits(memory_mb: int, max_processes: int):
    def apply() -> None:  # runs in the child between fork and exec
        import resource

        if memory_mb > 0:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if max_processes > 0:
            resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))

    return apply


def _read_capped(path: Path, limit: int) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def run_jailed(
    cmd: Sequence[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    timeout: float,
    memory_mb: int = 512,
    max_processes: int = 1,
    max_output_bytes: int = 1_000_000,
    wait_for_release: bool = True,
) -> JailResult:
    """Run `cmd` confined; fails closed if the confinement can't be applied.

    `wait_for_release`: the command reads one line from stdin before running
    untrusted code, so assignment to the job happens before anything executes.
    Pass False for commands that don't (the tiny pre-assignment window is then
    the only unconfined time).
    """
    out_dir = Path(tempfile.mkdtemp(prefix="sarah_jail_out_"))
    stdout_path, stderr_path = out_dir / "stdout", out_dir / "stderr"
    job = _create_job(memory_mb, max_processes) if IS_WINDOWS else None
    proc: Optional[subprocess.Popen] = None
    timed_out = False
    limit_hit: Optional[str] = None
    peak_mb = 0.0
    try:
        with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
            popen_kwargs = {}
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs["start_new_session"] = True
                popen_kwargs["preexec_fn"] = _posix_limits(memory_mb, max_processes)
            proc = subprocess.Popen(
                list(cmd), cwd=str(cwd), env=env,
                stdin=subprocess.PIPE, stdout=so, stderr=se, **popen_kwargs,
            )

        if job is not None and not _k32.AssignProcessToJobObject(job, int(proc._handle)):
            err = ctypes.get_last_error()
            proc.kill()
            proc.wait()
            raise ctypes.WinError(err)

        try:
            if wait_for_release:
                proc.stdin.write(RELEASE_LINE)
            proc.stdin.close()
        except OSError:
            pass  # child already exited; its output says why

        deadline = time.monotonic() + timeout
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
            try:
                produced = stdout_path.stat().st_size + stderr_path.stat().st_size
            except OSError:
                produced = 0
            if produced > max_output_bytes:
                limit_hit = "output"
            elif time.monotonic() > deadline:
                timed_out = True
            if limit_hit or timed_out:
                _kill(proc, job)
                break

        if job is not None:
            peak_mb = _peak_job_memory_mb(job)
        return JailResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=_read_capped(stdout_path, max_output_bytes),
            stderr=_read_capped(stderr_path, max_output_bytes),
            timed_out=timed_out,
            limit_hit=limit_hit,
            peak_memory_mb=peak_mb,
            jailed=job is not None,
        )
    finally:
        if proc is not None and proc.poll() is None:
            _kill(proc, job)
        if job is not None:
            _k32.TerminateJobObject(job, 1)
            _k32.CloseHandle(job)
        shutil.rmtree(out_dir, ignore_errors=True)


def _kill(proc: subprocess.Popen, job) -> None:
    if job is not None:
        _k32.TerminateJobObject(job, 1)  # takes descendants down too
    else:
        try:
            os.killpg(proc.pid, 9)
        except (OSError, AttributeError):
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
