"""
Sandbox Execution Environment
=============================

Runs untrusted Python / JavaScript with layered confinement:

1. OS level (``backend.utils.proc_jail``): Windows Job Object — one process
   max (no subprocesses by any API), memory cap, whole-tree kill on timeout,
   no clipboard/desktop/shutdown access; output capped by a watchdog.
2. Environment: allowlisted variables only — API keys, the backend API token
   and profile paths never reach the child. TEMP/HOME point into the sandbox.
3. Language level:
   * Python — an audit hook armed before user code runs: file writes only
     inside the sandbox dir, reads only there and in the interpreter/stdlib/
     site-packages; no process creation, ``ctypes``, ``gc`` introspection,
     killing other processes, or registry writes; network off unless
     ``allow_network`` (loopback socketpairs, which asyncio needs, still work).
   * JavaScript — Node's ``--permission`` model: fs read/write only in the
     sandbox dir, no child_process / worker / native addons / process.binding;
     network entry points replaced unless ``allow_network``.

Audit hooks are not a hard security boundary (PEP 578); layer 1 is. Treat this
as strong containment for your own / LLM-written code, not a hostile-code VM.
"""

import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Dict, List, Optional

from backend.utils.proc_jail import JailResult, minimal_env, run_jailed

logger = logging.getLogger("sarah.sandbox")

MAX_TIMEOUT_SECONDS = 120


class SandboxMode(str, Enum):
    """Sandbox security modes."""
    STRICT = "strict"      # Maximum security, minimal permissions
    MODERATE = "moderate"  # Balanced security and functionality
    PERMISSIVE = "permissive"  # Minimal restrictions


@dataclass
class SandboxLimits:
    """Resource limits for sandbox execution."""
    max_execution_time: int = 30  # seconds
    max_memory_mb: int = 512
    max_output_size: int = 10_000  # characters returned to the caller
    max_file_size: int = 10 * 1024 * 1024  # 10 MB, also the raw output cap
    max_processes: int = 1
    allow_network: bool = False
    allow_file_write: bool = True
    allowed_modules: Optional[List[str]] = None  # None = all allowed


@dataclass
class SandboxResult:
    """Result of sandbox execution."""
    success: bool
    output: str
    error: str
    exit_code: int
    execution_time: float
    memory_used_mb: float = 0.0
    stdout: str = ""
    stderr: str = ""
    files_created: List[str] = field(default_factory=list)
    security_violations: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Python bootstrap (runs in the child, before any user code)
# ---------------------------------------------------------------------------

# Everything the hook uses is captured in a closure up front, so user code
# can't disarm it by monkeypatching os/os.path/builtins. On Windows the path
# checks use the C-level nt._getfullpathname/_getfinalpathname directly for
# the same reason (ntpath.realpath looks helpers up at call time).
_PY_BOOTSTRAP = r'''
import sys
sys.stdin.readline()
import json as _json
_cfg = _json.loads(sys.argv[1])
import site as _site
for _p in _cfg["site"]:
    _site.addsitedir(_p)
import ctypes as _ctypes_preload  # its import-time dlopen must happen before arming
del _ctypes_preload, _site, _p

def _arm(cfg):
    import os
    import _socket
    import _weakref
    _isinstance, _type, _str, _bytes, _int, _tuple = isinstance, type, str, bytes, int, tuple
    _PermissionError, _OSError, _Exception = PermissionError, OSError, Exception
    _len, _repr = len, repr
    _fspath = os.fspath
    _fsenc = sys.getfilesystemencoding()
    _getsockname = _socket.socket.getsockname
    _wref = _weakref.ref
    windows = os.name == "nt"
    if windows:
        import nt
        _full, _final = nt._getfullpathname, nt._getfinalpathname
        sep = "\\"

        def canon(p):
            try:
                p = _full(p)
            except _Exception:
                return None
            head, tail = p, ""
            while True:
                try:
                    real = _final(head)
                    break
                except _OSError:
                    h, s, t = head.rpartition(sep)
                    if not s:
                        return None
                    if h.endswith(":"):
                        h += sep
                    if h == head:
                        return None
                    tail = t + (sep + tail if tail else "")
                    head = h
            if real.startswith("\\\\?\\UNC\\"):
                return None
            if real.startswith("\\\\?\\"):
                real = real[4:]
            if tail:
                real = real.rstrip(sep) + sep + tail
            return real.lower()
    else:
        _realpath = os.path.realpath
        sep = "/"

        def canon(p):
            try:
                return _realpath(p)
            except _Exception:
                return None

    root = canon(cfg["root"])
    write_roots = (root,)
    read_roots = _tuple(r for r in (canon(x) for x in cfg["read_roots"]) if r)
    allow_net = bool(cfg["net"])

    def as_text(p):
        t = _type(p)
        if t is _str:
            return p
        if t is _bytes:
            return p.decode(_fsenc, "surrogateescape")
        if t is _int or p is None:
            return None  # file descriptor / cwd default
        try:
            v = _fspath(p)
        except _Exception:
            raise _PermissionError("sandbox: unsupported path object")
        if _type(v) is _str:
            return v
        if _type(v) is _bytes:
            return v.decode(_fsenc, "surrogateescape")
        raise _PermissionError("sandbox: unsupported path object")

    def inside(p, roots):
        text = as_text(p)
        if text is None:
            return True
        c = canon(text)
        if c is None:
            return False
        for r in roots:
            if c == r or c.startswith(r.rstrip(sep) + sep):
                return True
        return False

    def deny(what):
        raise _PermissionError("sandbox: " + what)

    blocked = frozenset((
        "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
        "os.startfile", "os.fork", "os.forkpty", "os.kill", "os.killpg",
        "signal.pthread_kill", "gc.get_objects", "gc.get_referrers",
        "gc.get_referents", "winreg.CreateKey", "winreg.DeleteKey",
        "winreg.DeleteValue", "winreg.SetValue", "winreg.SaveKey",
        "winreg.LoadKey", "winreg.ConnectRegistry", "sys.remote_exec",
    ))
    blocked_prefixes = ("ctypes.", "_winapi.")
    write_events = frozenset((
        "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
        "os.link", "os.symlink", "os.truncate", "os.utime", "os.chflags",
        "os.lchflags", "os.lchmod",
    ))
    read_events = frozenset(("os.listdir", "os.scandir", "os.chdir"))
    loopback = frozenset(("127.0.0.1", "::1", "localhost"))
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    bound = []  # weakrefs to loopback sockets bound to an ephemeral port

    def loopback_addr(addr):
        if _type(addr) is not _tuple or _len(addr) < 2:
            return None
        host, port = addr[0], addr[1]
        if _type(host) is not _str or host not in loopback or _type(port) is not _int:
            return None
        return port

    def own_port(port):
        for ref in bound:
            s = ref()
            if s is None:
                continue
            try:
                if _getsockname(s)[1] == port:
                    return True
            except _Exception:
                pass
        return False

    def hook(event, args):
        if event in blocked:
            deny(event + " is not allowed")
        for prefix in blocked_prefixes:
            if event.startswith(prefix):
                deny(event + " is not allowed")
        if event == "open":
            path = args[0]
            mode = args[1] if _len(args) > 1 else None
            flags = args[2] if _len(args) > 2 else 0
            writing = (
                (_type(mode) is _str and ("w" in mode or "a" in mode or "x" in mode or "+" in mode))
                or (_type(flags) is _int and flags & write_flags)
            )
            if not inside(path, write_roots if writing else read_roots):
                deny("access outside the sandbox is denied: " + _repr(as_text(path)))
        elif event in write_events:
            for a in args[:2]:
                if a is not None and _type(a) is not _int and not inside(a, write_roots):
                    deny("modifying files outside the sandbox is denied")
        elif event in read_events:
            if args and not inside(args[0], read_roots):
                deny("access outside the sandbox is denied: " + _repr(as_text(args[0])))
        elif not allow_net and event.startswith("socket."):
            if event == "socket.__new__":
                return
            if event == "socket.bind":
                if loopback_addr(args[1]) == 0:
                    bound.append(_wref(args[0]))
                    return
            elif event in ("socket.connect", "socket.sendto"):
                port = loopback_addr(args[1])
                if port is not None and own_port(port):
                    return
            elif event == "socket.getaddrinfo":
                if _type(args[0]) is _str and args[0] in loopback:
                    return
            deny("network access is disabled")

    sys.addaudithook(hook)

_script = _cfg["root"] + ("\\" if sys.platform == "win32" else "/") + _cfg["script"]
with open(_script, "rb") as _f:
    _code = compile(_f.read(), _script, "exec")
sys.argv = [_script] + list(_cfg["args"])
_arm(_cfg)
del _arm, _cfg, _f, _json
exec(_code, {"__name__": "__main__", "__file__": _script, "__builtins__": __builtins__})
'''

# ---------------------------------------------------------------------------
# Node bootstrap (Node --permission already confines fs / processes)
# ---------------------------------------------------------------------------

_JS_BOOTSTRAP = r'''
const fs = require("fs");
try { fs.readSync(0, Buffer.alloc(8), 0, 8, null); } catch (_) {}
const CFG = __CFG__;
if (!CFG.net) {
  const deny = () => {
    const e = new Error("sandbox: network access is disabled");
    e.code = "ERR_ACCESS_DENIED";
    throw e;
  };
  const lock = (obj, names) => {
    for (const n of names) {
      try {
        if (obj && n in obj) Object.defineProperty(obj, n, { value: deny, writable: false, configurable: false });
      } catch (_) {}
    }
  };
  const net = require("net");
  lock(net, ["connect", "createConnection", "createServer"]);
  lock(net.Socket.prototype, ["connect"]);
  lock(require("tls"), ["connect", "createServer"]);
  for (const m of ["http", "https"]) lock(require(m), ["request", "get", "createServer"]);
  lock(require("http2"), ["connect", "createServer", "createSecureServer"]);
  lock(require("dgram"), ["createSocket"]);
  const dnsNames = ["lookup", "lookupService", "resolve", "resolve4", "resolve6", "resolveAny",
    "resolveCname", "resolveMx", "resolveNs", "resolveTxt", "resolveSrv", "resolvePtr", "reverse"];
  const dns = require("dns");
  lock(dns, dnsNames);
  lock(dns.promises, dnsNames);
  lock(globalThis, ["fetch", "WebSocket", "EventSource"]);
}
process.argv = [process.argv[0], CFG.script, ...CFG.args];
require(CFG.script);
'''


def _sandbox_violations_from_stderr(stderr: str) -> List[str]:
    found = [v.strip() for v in re.findall(r"sandbox: ([^\r\n'\"]+)", stderr or "")]
    if "ERR_ACCESS_DENIED" in (stderr or "") and not found:
        found.append("blocked by Node permission model (ERR_ACCESS_DENIED)")
    return list(dict.fromkeys(found))[:10]


@lru_cache(maxsize=1)
def _python_runtime() -> Dict[str, object]:
    """Interpreter + import paths for the child.

    A venv's python.exe on Windows is a redirector that spawns the base
    interpreter as a *child* process, which the one-process job limit would
    kill; run the base interpreter and add the venv's site-packages instead.
    """
    exe = getattr(sys, "_base_executable", None) or sys.executable
    site_dirs = list(dict.fromkeys(
        p for p in (sysconfig.get_paths().get("purelib"), sysconfig.get_paths().get("platlib")) if p
    ))
    read_roots = list(dict.fromkeys(
        [sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(exe), *site_dirs]
    ))
    return {"exe": exe, "site": site_dirs, "read_roots": read_roots}


@lru_cache(maxsize=1)
def _node_runtime() -> Optional[str]:
    """Path to a Node that supports --permission, or None."""
    try:
        from backend.config import settings
        configured = str(settings.node_path)
    except Exception:
        configured = ""
    node = configured if configured and os.path.exists(configured) else shutil.which("node")
    if not node:
        return None
    try:
        probe = subprocess.run(
            [node, "--permission", "-e", "0"], capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    return node if probe.returncode == 0 else None


class Sandbox:
    """
    Sandboxed code execution environment (see module docstring for layers).
    """

    def __init__(
        self,
        mode: SandboxMode = SandboxMode.MODERATE,
        limits: Optional[SandboxLimits] = None
    ):
        self.mode = mode
        self.limits = limits or SandboxLimits()
        self.temp_dir: Optional[pathlib.Path] = None

    def __enter__(self):
        """Context manager entry - create temp directory."""
        self.temp_dir = pathlib.Path(tempfile.mkdtemp(prefix="sarah_sandbox_")).resolve()
        logger.info(f"Created sandbox directory: {self.temp_dir}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup temp directory."""
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                logger.info(f"Cleaned up sandbox directory: {self.temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to cleanup sandbox: {e}")

    # ------------------------------------------------------------------
    # File staging
    # ------------------------------------------------------------------

    def _safe_path(self, filename: str) -> Optional[pathlib.Path]:
        """Resolve `filename` inside the sandbox, or None if it would escape."""
        candidate = pathlib.PurePath(filename)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            return None
        resolved = (self.temp_dir / candidate).resolve()
        if resolved != self.temp_dir and self.temp_dir not in resolved.parents:
            return None
        return resolved

    def _stage(self, script_name: str, code: str, input_files: Optional[Dict[str, str]]) -> Optional[SandboxResult]:
        try:
            (self.temp_dir / script_name).write_text(code, encoding="utf-8")
        except Exception as e:
            return self._failure(f"Failed to write code file: {e}")
        for filename, content in (input_files or {}).items():
            target = self._safe_path(filename)
            if target is None:
                return self._failure(f"Input file path escapes the sandbox: {filename!r}")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to create input file {filename}: {e}")
        return None

    @staticmethod
    def _failure(message: str, elapsed: float = 0.0) -> SandboxResult:
        return SandboxResult(
            success=False, output="", error=message, exit_code=-1, execution_time=elapsed
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run(self, cmd: List[str], env: Dict[str, str], code: str, script_name: str) -> SandboxResult:
        start_time = time.time()
        timeout = max(1, min(int(self.limits.max_execution_time), MAX_TIMEOUT_SECONDS))
        try:
            jail: JailResult = run_jailed(
                cmd,
                cwd=self.temp_dir,
                env=env,
                timeout=timeout,
                memory_mb=self.limits.max_memory_mb,
                max_processes=self.limits.max_processes,
                max_output_bytes=self.limits.max_file_size,
            )
        except Exception as e:
            # Fail closed: never fall back to running unconfined.
            return self._failure(f"Sandbox could not be created: {e}", time.time() - start_time)

        execution_time = time.time() - start_time
        stdout = jail.stdout[:self.limits.max_output_size]
        stderr = jail.stderr[:self.limits.max_output_size]
        violations = self._check_security_violations(code) + _sandbox_violations_from_stderr(jail.stderr)

        if jail.timed_out:
            stderr = (stderr + "\n" if stderr else "") + f"Execution timed out after {timeout}s"
            violations.append("timeout")
        if jail.limit_hit == "output":
            stderr = (stderr + "\n" if stderr else "") + "Output limit exceeded; process stopped"
            violations.append("output limit exceeded")

        output = stdout
        if stderr:
            output += f"\n\nSTDERR:\n{stderr}"

        script_path = self.temp_dir / script_name
        files_created = [
            str(p.relative_to(self.temp_dir))
            for p in self.temp_dir.rglob("*")
            if p.is_file() and p != script_path
        ]

        return SandboxResult(
            success=jail.returncode == 0 and not jail.timed_out and not jail.limit_hit,
            output=output,
            error=stderr,
            exit_code=-1 if jail.timed_out else jail.returncode,
            execution_time=execution_time,
            memory_used_mb=jail.peak_memory_mb,
            stdout=stdout,
            stderr=stderr,
            files_created=files_created,
            security_violations=list(dict.fromkeys(violations)),
        )

    def execute_python(
        self,
        code: str,
        input_files: Optional[Dict[str, str]] = None,
        args: Optional[List[str]] = None
    ) -> SandboxResult:
        """Execute Python code in the sandbox."""
        if not self.temp_dir:
            raise RuntimeError("Sandbox not initialized. Use 'with Sandbox() as sandbox:'")

        failure = self._stage("script.py", code, input_files)
        if failure:
            return failure

        runtime = _python_runtime()
        cfg = {
            "root": str(self.temp_dir),
            "script": "script.py",
            "net": bool(self.limits.allow_network),
            "site": runtime["site"],
            "read_roots": [str(self.temp_dir), *runtime["read_roots"]],
            "args": [str(a) for a in (args or [])],
        }
        exe = str(runtime["exe"])
        cmd = [exe, "-E", "-s", "-B", "-c", _PY_BOOTSTRAP, json.dumps(cfg)]
        env = self._get_restricted_env(extra_path=[pathlib.Path(exe).parent])
        return self._run(cmd, env, code, "script.py")

    def execute_javascript(
        self,
        code: str,
        input_files: Optional[Dict[str, str]] = None,
        args: Optional[List[str]] = None
    ) -> SandboxResult:
        """Execute JavaScript code in the sandbox using Node.js (--permission)."""
        if not self.temp_dir:
            raise RuntimeError("Sandbox not initialized")

        node = _node_runtime()
        if not node:
            return self._failure(
                "Node.js with --permission support (v20+) not found; JavaScript execution is disabled."
            )

        failure = self._stage("script.js", code, input_files)
        if failure:
            return failure

        cfg = {
            "script": str(self.temp_dir / "script.js"),
            "net": bool(self.limits.allow_network),
            "args": [str(a) for a in (args or [])],
        }
        bootstrap = _JS_BOOTSTRAP.replace("__CFG__", json.dumps(cfg))
        heap_mb = max(64, self.limits.max_memory_mb // 2)
        cmd = [
            node,
            "--permission",
            f"--allow-fs-read={self.temp_dir}",
            f"--allow-fs-write={self.temp_dir}",
            f"--max-old-space-size={heap_mb}",
            "-e",
            bootstrap,
        ]
        env = self._get_restricted_env(extra_path=[pathlib.Path(node).parent])
        return self._run(cmd, env, code, "script.js")

    def _get_restricted_env(self, extra_path=()) -> Dict[str, str]:
        """Allowlisted environment: nothing secret is inherited."""
        env = minimal_env(self.temp_dir, extra_path)
        if self.mode == SandboxMode.STRICT:
            env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            env["PIP_NO_INDEX"] = "1"
        return env

    def _check_security_violations(self, code: str) -> List[str]:
        """Advisory static scan (STRICT mode). Enforcement happens at runtime."""
        violations = []

        dangerous_patterns = [
            (r'\beval\s*\(', "Use of eval()"),
            (r'\bexec\s*\(', "Use of exec()"),
            (r'\b__import__\s*\(', "Dynamic imports with __import__"),
            (r'os\.system\s*\(', "Use of os.system()"),
            (r'subprocess\.call\s*\(', "Use of subprocess.call()"),
            (r'subprocess\.Popen\s*\(', "Use of subprocess.Popen()"),
            (r'open\s*\([^)]*["\']w', "File write operations"),
        ]

        if self.mode == SandboxMode.STRICT:
            for pattern, description in dangerous_patterns:
                if re.search(pattern, code):
                    violations.append(description)

            network_patterns = [
                (r'\burllib\b', "Network access via urllib"),
                (r'\brequests\b', "Network access via requests"),
                (r'\bsocket\b', "Network access via socket"),
            ]

            if not self.limits.allow_network:
                for pattern, description in network_patterns:
                    if re.search(pattern, code):
                        violations.append(description)

        return violations

    def get_file(self, filename: str) -> Optional[str]:
        """Read a file created in the sandbox."""
        if not self.temp_dir:
            return None

        file_path = self._safe_path(filename)
        if file_path is None or not file_path.exists():
            return None

        try:
            return file_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.warning(f"Failed to read file {filename}: {e}")
            return None

    def list_files(self) -> List[str]:
        """List all files in the sandbox."""
        if not self.temp_dir:
            return []

        files = []
        for file_path in self.temp_dir.rglob("*"):
            if file_path.is_file():
                files.append(str(file_path.relative_to(self.temp_dir)))
        return files


def format_sandbox_result(result: SandboxResult) -> str:
    """Format sandbox result into human-readable text."""
    lines = ["🔒 SANDBOX EXECUTION\n"]

    if result.success:
        lines.append("✅ Execution completed successfully")
    else:
        lines.append("❌ Execution failed")

    lines.append(f"\nExit Code: {result.exit_code}")
    lines.append(f"Execution Time: {result.execution_time:.2f}s")

    if result.memory_used_mb > 0:
        lines.append(f"Memory Used: {result.memory_used_mb:.1f} MB")

    if result.files_created:
        lines.append(f"\n📁 Files Created ({len(result.files_created)}):")
        for file in result.files_created[:10]:  # Limit to 10
            lines.append(f"  • {file}")
        if len(result.files_created) > 10:
            lines.append(f"  ... and {len(result.files_created) - 10} more")

    if result.security_violations:
        lines.append(f"\n⚠️ Security Violations:")
        for violation in result.security_violations:
            lines.append(f"  • {violation}")

    if result.output:
        lines.append(f"\n📤 Output:")
        lines.append("```")
        lines.append(result.output[:500])  # Limit output
        if len(result.output) > 500:
            lines.append("... (output truncated)")
        lines.append("```")

    if result.error:
        lines.append(f"\n❌ Error:")
        lines.append("```")
        lines.append(result.error[:500])
        if len(result.error) > 500:
            lines.append("... (error truncated)")
        lines.append("```")

    return "\n".join(lines)


# Convenience function for quick execution
def execute_code_safely(
    code: str,
    language: str = "python",
    timeout: int = 30,
    allow_network: bool = False,
    input_files: Optional[Dict[str, str]] = None,
) -> SandboxResult:
    """
    Execute code in a fresh sandbox (see module docstring).

    Args:
        code: Code to execute
        language: "python" or "javascript"
        timeout: Maximum execution time in seconds (clamped to 1..120)
        allow_network: Whether to allow network access
        input_files: filename -> content staged next to the script

    Returns:
        SandboxResult
    """
    limits = SandboxLimits(
        max_execution_time=max(1, min(int(timeout), MAX_TIMEOUT_SECONDS)),
        allow_network=allow_network,
    )

    with Sandbox(mode=SandboxMode.MODERATE, limits=limits) as sandbox:
        if language.lower() in ["python", "py"]:
            return sandbox.execute_python(code, input_files=input_files)
        elif language.lower() in ["javascript", "js", "node"]:
            return sandbox.execute_javascript(code, input_files=input_files)
        else:
            return SandboxResult(
                success=False,
                output="",
                error=f"Unsupported language: {language}",
                exit_code=-1,
                execution_time=0.0
            )
