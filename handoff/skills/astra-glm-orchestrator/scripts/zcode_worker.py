#!/usr/bin/env python3
"""ZCode app-server worker for Astra (GLM-5.3-Flash), one-turn task runner.

Subcommands:
  doctor [--zcode-path PATH] [--node PATH] [--zcode-home PATH]
      Validate the local ZCode runtime, provider configuration and credential
      file presence WITHOUT performing any live model request. Uses exactly
      the same shared runtime/config preflight as `run`.
  run --workspace ABS --task ABS --run-dir ABS [--timeout 900]
      [--effort low|high|max] [--notify-codex] [--notify-server PATH]
      [--verbose] [--launch-token TOKEN]
      Execute exactly one worker turn in a fresh ZCode app-server session and
      write state.json / events.ndjson / result.md under --run-dir. --timeout
      is a TOTAL run deadline covering startup, preflight, RPC waits,
      permission approvals and the turn wait. Only the final child-process
      cleanup runs on a separate bounded grace (roughly 30 seconds), so the
      total wall clock can exceed --timeout by cleanup plus one bounded terminal
      notification attempt (15 seconds plus client shutdown).
      --notify-codex opts in to bounded Codex notifications (see
      codex_notify.py) on meaningful events only: a pending high-risk
      permission AFTER its file exists, and the terminal status AFTER
      evidence and cleanup are recorded. An atomic compact status.json is
      maintained for every run. --launch-token is reserved for `start`.
  start --workspace ABS --task ABS --run-dir ABS [--timeout 900]
      [--effort low|high|max] [--launch-wait 20] [--notify-server PATH]
      Convenience launcher: prefights notification support (no send), then
      starts exactly ONE hidden background `run --notify-codex` worker and
      waits bounded for its real status.json handshake before returning a
      compact launch result. On failure/timeout it stops the process it
      owns, using cooperative cancellation first. Uncertain cleanup is reported
      explicitly and must be investigated before another worker starts.
  status --run-dir ABS
      Print the compact live status.json of one run directory. A missing
      artifact leaves liveness unknown; a PID alone is never identity.

Python 3.11 stdlib only. Never commit, push, or modify vendor/config files.
Worker result status is ready_for_review / blocked / failed - never accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

MODEL_ID = "GLM-5.3-Flash"
PROVIDER_ID = "account:zai-individual-coding-plan"   # RPC/rule provider id
CREDENTIAL_PROVIDER_ID = "builtin:zai-coding-plan"   # key in <home>/v2/config.json
ACCOUNT_MODE = "individual-coding-plan"
ACCOUNT_TYPE = "zai"
BASE_URL = "https://api.z.ai/api/anthropic"
API_TYPE = "anthropic-messages"
RUNTIME_VERSION = "0.16.9"                # exact required `node zcode.cjs version`
DEFAULT_ZCODE_CJS = "C:/Program Files/ZCode/resources/glm/zcode.cjs"
MAC_ZCODE_CJS = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"
LOCK_NAME = ".ai/astra-glm.lock"
PERM_WAIT_SECONDS = 180
MAX_VALUE_LEN = 2_000            # per-string-value clip before serialization
MAX_EVENTS = 400                 # bounded retained event list
MAX_FRAME_BYTES = 1_000_000      # child NDJSON frame cap; larger = protocol error
QUEUE_LIMIT = 2_000              # transport frame queue; overflow fails closed
MAX_PERM_INPUT_CHARS = 20_000    # full reviewed permission input budget
VERSION_SUBPROC_TIMEOUT = 20     # upper bound for `node zcode.cjs version`
GIT_SUBPROC_TIMEOUT = 30         # upper bound per git subprocess call
TASKKILL_TIMEOUT = 15            # bounded cleanup grace
STOP_WAIT_TIMEOUT = 10           # bounded cleanup grace
SCHEMA_VERSION = 3
STATUS_SCHEMA_VERSION = 1
STATUS_FILE = "status.json"
LEDGER_FILE = "notifications.json"
LAUNCHER_LOG = "start.log"
WORKER_LOG = "worker-output.log"
KNOWN_STATUS = ("starting", "running", "needs_approval",
                "ready_for_review", "blocked", "failed")
NOTIFY_TOOL = "send_message_to_thread"
NOTIFY_ATTEMPT_SECONDS = 15      # bounded per-notification attempt
NOTIFY_RESERVED_SECONDS = 30     # turn budget never consumed by a notify
LAUNCH_WAIT_DEFAULT = 20         # bounded start handshake wait
LAUNCHER_FILES = {LAUNCHER_LOG, WORKER_LOG}  # tolerated in a prepared (pre-created) run dir
DENY_TOOLS = [
    "Agent", "Task", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "EnterPlanMode", "ExitPlanMode", "CronCreate", "CronUpdate", "CronDelete",
]
SENSITIVE_FIELDS = {"apikey", "api_key", "authorization", "token", "secret",
                    "password", "credential", "accesstoken", "refreshtoken"}
ALLOWED_EFFORTS = ("low", "high", "max")


# ------------------------------------------------------------ redact / clip

def clip_text(value: str) -> str:
    return value if len(value) <= MAX_VALUE_LEN else value[:MAX_VALUE_LEN] + "…[clipped]"


def _redact_str(value: str, secrets: list[str], clip: bool) -> str:
    """Replace exact secret material BEFORE any truncation, then optionally
    clip. Order is load-bearing: a key straddling the clip boundary must never
    leave a fragment behind."""
    for s in secrets:
        if s:
            value = value.replace(s, "[REDACTED]")
    return clip_text(value) if clip else value


def _key_is_sensitive(key, secrets: list[str]) -> bool:
    """A dictionary key carries sensitive data when it names a sensitive
    field OR embeds literal secret material."""
    if not isinstance(key, str):
        return False
    if key.lower() in SENSITIVE_FIELDS:
        return True
    return any(s and s in key for s in secrets)


def redact_obj(obj, secrets: list[str], clip: bool = True, _depth: int = 0):
    """Deep copy with sensitive fields, secret-bearing dictionary KEYS and
    literal secret material redacted; long strings clipped only when `clip`
    is set, AFTER redaction, so serialized JSON stays bounded."""
    if _depth > 24:
        return "[depth-capped]"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _key_is_sensitive(k, secrets):
                out["[REDACTED_KEY]" if isinstance(k, str) and k.lower()
                    not in SENSITIVE_FIELDS else str(k)] = "[REDACTED]"
            else:
                out[k if isinstance(k, str) else str(k)] = \
                    redact_obj(v, secrets, clip, _depth + 1)
        return out
    if isinstance(obj, list):
        return [redact_obj(v, secrets, clip, _depth + 1) for v in obj]
    if isinstance(obj, str):
        return _redact_str(obj, secrets, clip)
    return obj


def redact_text(value: str, secrets: list[str]) -> str:
    return _redact_str(value, secrets, clip=True)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(path)


def pid_alive(pid: int | None) -> bool:
    """Liveness of a recorded PID. Windows os.kill(pid, 0) would TERMINATE
    the process, so query the exit code via a bounded handle instead."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    try:
        import ctypes
        ctypes.windll.kernel32.OpenProcess.restype = ctypes.c_void_p
        ctypes.windll.kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - never crash status reporting
        return False


def load_notify_module():
    """Load codex_notify.py from the SAME directory (works for the installed
    helper layout). Only imported on the explicit notify path, so a plain
    `run` never depends on notification support or this module."""
    path = Path(__file__).resolve().parent / "codex_notify.py"
    spec = importlib.util.spec_from_file_location("codex_notify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load notify client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------ runtime paths

def detect_zcode_cjs(explicit: str | None) -> Path:
    """Resolve zcode.cjs. An explicit --zcode-path that does not exist is a
    hard error - never silently fall back to another discovery candidate."""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"--zcode-path does not exist: {p}")
        return p.resolve(strict=True)
    candidates = []
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates.append(Path(pf) / "ZCode/resources/glm/zcode.cjs")
        pf86 = os.environ.get("ProgramFiles(x86)")
        if pf86:
            candidates.append(Path(pf86) / "ZCode/resources/glm/zcode.cjs")
    elif sys.platform == "darwin":
        candidates.append(Path(MAC_ZCODE_CJS))
    candidates.append(Path(DEFAULT_ZCODE_CJS))
    for c in candidates:
        try:
            resolved = c.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    raise FileNotFoundError(
        "zcode.cjs not found; pass --zcode-path explicitly. Tried: "
        + ", ".join(str(c) for c in candidates))


def builtin_config_path(cjs: Path) -> Path:
    """Verified layout: <runtime root>/config/provider/zcode-builtin.json
    where runtime root is the parent of the glm/ directory holding the cjs."""
    override = os.environ.get("ZCODE_BUILTIN_PROVIDER_CONFIG_FILE")
    if override:
        return Path(override)
    return cjs.parents[1] / "config/provider/zcode-builtin.json"


def runtime_version(node: str, cjs: Path, timeout: float = VERSION_SUBPROC_TIMEOUT) -> str:
    """Offline `node <zcode.cjs> version` with a bounded wait. Raises on any
    mismatch or timeout."""
    proc = subprocess.run([node, str(cjs), "version"],
                          capture_output=True, text=True,
                          timeout=max(1.0, timeout))
    if proc.returncode != 0:
        raise RuntimeError(f"node {cjs} version exited {proc.returncode}: "
                           + clip_text(proc.stderr.strip()))
    observed = proc.stdout.strip().splitlines()[-1].strip() if proc.stdout.strip() else ""
    if observed != RUNTIME_VERSION:
        raise RuntimeError(f"runtime version {observed!r} != required exact "
                           f"{RUNTIME_VERSION!r}")
    return observed


def inside(root: Path, child: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def no_symlink_escape(root: Path, target: Path) -> bool:
    """True if every component from target up to root is a real directory.
    Resolved containment is enforced separately by the caller."""
    cur = target.resolve(strict=False)
    root_r = root.resolve(strict=True)
    if not inside(root_r, cur):
        return False
    while cur != root_r:
        if cur.is_symlink():
            return False
        if cur.parent == cur:
            return False
        cur = cur.parent
    return True


def resolve_contained_dir(root: Path, target: Path) -> Path:
    """Canonical containment check for a directory that may not exist yet,
    run BEFORE any mkdir. The full target is resolved non-strict (which
    collapses '..' and follows existing links), then every already-existing
    component under root is verified to be a genuine directory rather than a
    symlink/junction. Raises ValueError on any escape. Returns the resolved
    path the caller may create."""
    resolved = target.resolve(strict=False)
    if not inside(root, resolved):
        raise ValueError("resolves outside the workspace")
    if resolved == root:
        raise ValueError("target is the workspace itself")
    cur = root
    for part in resolved.relative_to(root).parts:
        nxt = cur / part
        if nxt.exists() or nxt.is_symlink():
            real = nxt.resolve(strict=False)
            if os.path.normcase(str(real)) != os.path.normcase(str(nxt)):
                raise ValueError(f"component {nxt} is a symlink/junction")
            if not nxt.is_dir():
                raise ValueError(f"component {nxt} is not a directory")
        cur = nxt
    return resolved


# ---------------------------------------------------------------- shared preflight

def builtin_config_problems(builtin: dict) -> list[str]:
    """Shared validation of the builtin provider config for doctor AND run.
    Rejects missing/invalid revision (0 is never accepted) and requires the
    official endpoint, anthropic-messages API, the zhipu zai individual
    account access and an enabled rule for the selected model."""
    problems: list[str] = []
    if not isinstance(builtin, dict):
        return ["builtin provider config is not an object"]
    revision = builtin.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        problems.append(f"invalid builtin revision {revision!r}")
    rules = (builtin.get("config", {}) or {}).get("providerConfigRules", {}) \
        .get("providerRules") or []
    rule = next((r for r in rules
                 if isinstance(r, dict) and r.get("providerId") == PROVIDER_ID),
                None)
    if rule is None:
        problems.append(f"builtin rule {PROVIDER_ID} not found in builtin config")
        return problems
    rcfg = rule.get("config", {}) or {}
    access = rcfg.get("access", {}) or {}
    api = rcfg.get("api", {}) or {}
    if access.get("type") != "zhipu-account":
        problems.append(f"builtin rule access.type={access.get('type')!r}")
    if access.get("mode") != ACCOUNT_MODE:
        problems.append(f"builtin rule access.mode={access.get('mode')!r}")
    if access.get("accountType") != ACCOUNT_TYPE:
        problems.append(f"builtin rule access.accountType={access.get('accountType')!r}")
    if api.get("type") != API_TYPE:
        problems.append(f"builtin rule api.type={api.get('type')!r}, "
                        f"expected {API_TYPE!r}")
    if api.get("baseUrl") != BASE_URL:
        problems.append("builtin rule baseUrl is not the official endpoint")
    if MODEL_ID not in (rcfg.get("builtinModelIds") or []):
        problems.append(f"builtin rule does not include {MODEL_ID}")
    mcr = (builtin.get("config", {}) or {}).get("modelConfigRules", {}) or {}
    enabled = any(
        isinstance(r, dict) and r.get("providerId") == PROVIDER_ID
        and r.get("modelId") == MODEL_ID
        and isinstance(r.get("config"), dict)
        and r["config"].get("enabled") is True
        for r in (mcr.get("builtinProviderModelRules") or []))
    if not enabled:
        problems.append(f"no enabled builtin provider model rule for {MODEL_ID}")
    return problems


def validate_credential(home: Path) -> tuple[bool, list[str]]:
    """Return (configured_ok, problems). Never prints or returns key material."""
    problems: list[str] = []
    cfg_path = home / "v2/config.json"
    if not cfg_path.is_file():
        return False, [f"missing {cfg_path}"]
    try:
        cfg = load_json(cfg_path)
    except (OSError, ValueError) as exc:
        return False, [f"unreadable config.json: {exc}"]
    entry = cfg.get("provider", {}).get(CREDENTIAL_PROVIDER_ID)
    if not isinstance(entry, dict):
        return False, [f"config.json provider['{CREDENTIAL_PROVIDER_ID}'] missing"]
    opts = entry.get("options", {})
    if entry.get("enabled") is not True:
        problems.append("provider enabled is not true")
    if entry.get("kind") != "anthropic":
        problems.append("provider kind mismatch; expected anthropic")
    models = entry.get("models") or opts.get("models") or []
    if not isinstance(models, (list, dict)) or MODEL_ID not in models:
        problems.append(f"{MODEL_ID} not in provider models")
    if not str(opts.get("apiKey") or "").strip():
        problems.append("apiKey empty")
    if opts.get("baseURL") != BASE_URL:
        problems.append("baseURL mismatch (expected official endpoint)")
    return not problems, problems


def credential_api_key(home: Path) -> str:
    """Extract the key AFTER validate_credential approved the config. The key
    stays in memory only and is never logged or persisted."""
    cfg = load_json(home / "v2/config.json")
    entry = cfg.get("provider", {}).get(CREDENTIAL_PROVIDER_ID, {})
    return str((entry.get("options", {}) or {}).get("apiKey") or "").strip()


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    home = Path(args.zcode_home or (Path.home() / ".zcode")).expanduser()
    problems: list[str] = []
    cjs = None
    try:
        cjs = detect_zcode_cjs(args.zcode_path)
        print(f"zcode.cjs: {cjs}")
    except FileNotFoundError as exc:
        problems.append(str(exc))

    node = args.node or "node"
    try:
        ver = subprocess.run([node, "--version"], capture_output=True,
                             text=True, timeout=20)
        if ver.returncode == 0:
            print(f"node: {ver.stdout.strip()}")
        else:
            problems.append(f"node --version exited {ver.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        problems.append(f"node not runnable: {exc}")

    # Exact offline runtime version gate (shared with run).
    if cjs:
        try:
            observed = runtime_version(node, cjs)
            print(f"runtime version: {observed} (required exact {RUNTIME_VERSION})")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"runtime version gate failed: {exc}")

    builtin_file = builtin_config_path(cjs).resolve() if cjs else None
    builtin = None
    if builtin_file and builtin_file.is_file():
        try:
            builtin = load_json(builtin_file)
            print(f"builtin provider config: {builtin_file}")
        except (OSError, ValueError) as exc:
            problems.append(f"builtin provider config unreadable: {exc}")
    else:
        problems.append(f"builtin provider config missing: {builtin_file}")

    personal = home / "v2/provider_config.json"
    if personal.is_file():
        try:
            load_json(personal)
            print(f"personal provider config present: {personal}")
        except (OSError, ValueError) as exc:
            problems.append(f"personal provider config unreadable: {exc}")
    else:
        problems.append(f"personal provider config missing: {personal}")

    ok, cred_problems = validate_credential(home)
    problems.extend(cred_problems)

    if builtin is not None:
        # Same shared validation run() performs before any spawn.
        problems.extend(builtin_config_problems(builtin))
        base = None
        for r in ((builtin.get("config", {}) or {})
                  .get("providerConfigRules", {}).get("providerRules") or []):
            if isinstance(r, dict) and r.get("providerId") == PROVIDER_ID:
                base = ((r.get("config", {}) or {}).get("api", {}) or {}).get("baseUrl")
        print(f"builtin rule {PROVIDER_ID}: baseUrl ok={base == BASE_URL}")

    print("credential/config:", "configured" if ok else "PROBLEMS FOUND")
    print("doctor: configuration validation only - no live model request was "
          "made; live success is never claimed by doctor.")
    for p in problems:
        print(f"PROBLEM: {p}")
    return 0 if not problems else 1


# ---------------------------------------------------------------- lock

class WorkspaceLock:
    """Exclusive workspace lock with a validated token. A foreign lock is
    never unlinked; on uncertain cleanup the lock file is preserved but the
    descriptor is always closed so the directory stays removable."""

    def __init__(self, workspace: Path, run_dir: Path):
        self.path = workspace / LOCK_NAME
        self.workspace = workspace
        self.run_dir = run_dir
        self.fd: int | None = None
        self.held = False
        self.token = uuid.uuid4().hex

    def acquire(self) -> None:
        # The .ai parent must be a genuine directory inside the workspace; a
        # symlink/junction .ai must not redirect the lock outside.
        ai_dir = self.workspace / ".ai"
        if ai_dir.exists() or ai_dir.is_symlink():
            real = ai_dir.resolve(strict=False)
            if os.path.normcase(str(real)) != os.path.normcase(str(ai_dir)) \
                    or not ai_dir.is_dir():
                raise RuntimeError(
                    "workspace .ai is not a genuine directory; refusing to "
                    "place the lock through a link")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = load_json(self.path)
            except (OSError, ValueError):
                owner = {}
            raise RuntimeError(
                f"workspace locked by PID {owner.get('pid')} run_dir "
                f"{owner.get('run_dir')}; stale locks are never auto-broken "
                "- resolve manually") from None
        payload = {"pid": os.getpid(), "run_dir": str(self.run_dir),
                   "token": self.token, "created": time.time()}
        os.write(self.fd, json.dumps(payload).encode())
        self.held = True

    def _close_fd(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def preserve(self) -> None:
        """Stop short of releasing when cleanup is uncertain: the lock file
        stays on disk for manual resolution, but our descriptor is closed so
        nothing is leaked."""
        self.held = False
        self._close_fd()

    def release(self) -> None:
        self._close_fd()
        if self.held:
            # Validate our own token before unlinking; never remove another
            # owner's lock file.
            try:
                data = load_json(self.path)
                if data.get("token") == self.token:
                    self.path.unlink()
            except (OSError, ValueError):
                pass
        self.held = False


# ---------------------------------------------------------------- git snapshot

def git_snapshot(workspace: Path, budget: float = GIT_SUBPROC_TIMEOUT) -> dict:
    """Capture repo state with bounded subprocess waits (each git call gets
    at most min(GIT_SUBPROC_TIMEOUT, budget) seconds). Staged and unstaged
    diffs are hashed separately so staged-vs-unstaged state is not lost.
    Works without a HEAD commit. Untracked file CONTENTS are never read."""
    snap = {"git": False}
    per_call = max(1.0, min(float(GIT_SUBPROC_TIMEOUT), float(budget)))
    try:
        def run_git(*args):
            return subprocess.run(["git", "-C", str(workspace), *args],
                                  capture_output=True, text=True,
                                  timeout=per_call)

        head = run_git("rev-parse", "HEAD")
        if head.returncode == 0:
            snap["git"] = True
            snap["head"] = head.stdout.strip()
        else:
            snap["note"] = clip_text(head.stderr.strip()) or "unborn/absent repository"
        # Status/untracked are captured even without HEAD.
        status = run_git("status", "--porcelain")
        if status.returncode == 0:
            snap["status_hash"] = sha256_text(status.stdout)
            snap["untracked_names"] = sorted(
                ln[3:] for ln in status.stdout.splitlines()
                if ln.startswith("??"))[:500]
        if snap.get("git"):
            staged = run_git("diff", "--cached")
            if staged.returncode == 0:
                snap["staged_diff_hash"] = sha256_text(staged.stdout)
            unstaged = run_git("diff")
            if unstaged.returncode == 0:
                snap["unstaged_diff_hash"] = sha256_text(unstaged.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        snap["note"] = f"git unavailable: {clip_text(str(exc))}"
    return snap


# ---------------------------------------------------------------- transport

class Transport:
    """NDJSON child transport. The reader thread validates frames and feeds a
    single bounded queue; overflow or an oversized frame is a FAIL-CLOSED
    protocol error, never silent evidence loss. Child stderr is discarded
    (DEVNULL) so unredacted diagnostics can never leak through a log sink."""

    def __init__(self, argv, env, cwd):
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.protocol_error: str | None = None
        flags = 0
        kwargs = {}
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env,
            cwd=str(cwd), creationflags=flags, **kwargs)
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def _read_stdout(self) -> None:
        try:
            while True:
                line = self.proc.stdout.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_FRAME_BYTES:
                    self._fail(f"frame exceeds {MAX_FRAME_BYTES} characters")
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    self._fail("malformed NDJSON frame from child")
                    continue
                if not isinstance(frame, dict):
                    self._fail("non-object JSON frame from child")
                    continue
                self._put(frame)
        except (OSError, ValueError):
            pass
        finally:
            self._put({"eof": True})

    def _fail(self, message: str) -> None:
        if self.protocol_error is None:
            self.protocol_error = message
        try:
            self.q.put_nowait({"overflow": True})
        except queue.Full:
            pass  # pump checks protocol_error before its next bounded poll

    def _put(self, frame: dict) -> None:
        if self.protocol_error is not None and not frame.get("overflow") \
                and not frame.get("eof"):
            return
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            # Bounded memory AND no silent loss: surface a protocol error
            # instead of dropping the oldest frame (which may be the ack,
            # a permission request or the terminal event).
            self._fail("transport event queue overflow; failing closed")

    def send(self, msg_id: int, method: str, params: dict) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("child stdin closed")
        self.proc.stdin.write(json.dumps({"id": msg_id, "method": method,
                                          "params": params}) + "\n")
        self.proc.stdin.flush()

    def reply(self, msg_id, result) -> None:
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("child stdin closed")
        self.proc.stdin.write(json.dumps({"id": msg_id, "result": result}) + "\n")
        self.proc.stdin.flush()

    def reply_error(self, msg_id, code: int, message: str) -> None:
        """Protocol error at TOP LEVEL {id, error}, never inside result."""
        if self.proc.stdin is None or self.proc.stdin.closed:
            raise RuntimeError("child stdin closed")
        self.proc.stdin.write(json.dumps(
            {"id": msg_id, "error": {"code": code, "message": message}}) + "\n")
        self.proc.stdin.flush()

    def poll(self, timeout: float):
        try:
            return self.q.get(timeout=max(0.05, timeout))
        except queue.Empty:
            return None

    def _close_pipes(self) -> None:
        # A surviving descendant may still hold stdout open after its parent
        # exits. Closing a TextIOWrapper from another thread can then block on
        # the reader's lock indefinitely. Leave that handle to its daemon reader
        # until EOF; the helper process exit also closes it.
        self.reader.join(timeout=0.5)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe is self.proc.stdout and self.reader.is_alive():
                continue
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()
            except OSError:
                pass

    def stop(self) -> str:
        """True tree cleanup. The child's stdin stays OPEN until the forceful
        tree kill has completed, so the root cannot exit on EOF and orphan
        its descendants mid-cleanup; pipes are closed only afterwards. On
        Windows a root that already exited leaves descendants unknown, which
        is reported as 'uncertain' (and the workspace lock is preserved by
        the caller). Bounded waits only."""
        try:
            if os.name == "nt":
                if self.proc.poll() is not None:
                    return "uncertain"   # root gone pre-cleanup; tree unknown
                try:
                    kill = subprocess.run(
                        ["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                        capture_output=True, timeout=TASKKILL_TIMEOUT)
                except (OSError, subprocess.TimeoutExpired):
                    return "uncertain"
                if kill.returncode != 0:
                    return "uncertain"
                try:
                    self.proc.wait(timeout=STOP_WAIT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    return "uncertain"
                return "stopped"
            # POSIX: kill the whole process group we created with
            # start_new_session (group id == child pid), even if the root has
            # already exited - the group id stays valid while children live.
            pgid = self.proc.pid
            killed = False
            try:
                os.killpg(pgid, 9)
                killed = True
            except (ProcessLookupError, PermissionError):
                if self.proc.poll() is None:
                    try:
                        self.proc.kill()
                        killed = True
                    except OSError:
                        return "uncertain"
                elif not _group_gone(pgid):
                    return "uncertain"
            try:
                self.proc.wait(timeout=STOP_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                return "uncertain"
            if killed:
                until = time.monotonic() + 2
                while not _group_gone(pgid):
                    if time.monotonic() >= until:
                        return "uncertain"
                    time.sleep(0.05)
            return "stopped"
        finally:
            self._close_pipes()


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


# ---------------------------------------------------------------- runner

class Runner:
    def __init__(self, args):
        self.args = args
        if args.effort not in ALLOWED_EFFORTS:
            raise ValueError(f"unsupported effort {args.effort!r}; allowed: "
                             f"{', '.join(ALLOWED_EFFORTS)}")
        self.effort = args.effort
        self.home = Path(args.zcode_home or (Path.home() / ".zcode")).expanduser()
        self.secrets: list[str] = []
        self.seq = 0
        self.events: list[dict] = []          # bounded, redacted/clipped copies
        self.perm_cache: dict[str, str] = {}  # perm key -> decision (dedupe)
        self.perm_log: dict[str, str] = {}    # perm key -> decision (report)
        self.perm_identities: dict[str, str] = {}  # perm key -> identity hash
        self.perm_pending = False
        self.blocked_reason: str | None = None
        self.turn_id: str | None = None
        self.usage: dict | None = None
        self.worker_response: str | None = None
        self.workspace: Path | None = None
        self.run_dir: Path | None = None
        self.session_id: str | None = None
        self.task: Path | None = None
        self.task_sha = "n/a"
        self.cjs: Path | None = None
        self.builtin_file: Path | None = None
        self.builtin_revision: int | None = None
        self.started_monotonic = time.monotonic()
        self.started_epoch = time.time()
        self.deadline = self.started_monotonic + float(args.timeout)
        self.notify_codex = bool(getattr(args, "notify_codex", False))
        self.notify_server = getattr(args, "notify_server", None) or None
        self.launch_token = getattr(args, "launch_token", None) or None
        self.notify = None                  # codex_notify module, lazy
        self.run_id = "n/a"
        self.status_now: str | None = None  # last status.json write
        self.notification = "none"          # last notification outcome
        self.ledger: dict | None = None
        self.git_before: dict = {}
        self.git_after: dict = {}
        self.cleanup_status = "not-started"
        self._cleanup_started = False
        self.terminal_event: dict | None = None
        self._creating_session = False
        self.preference_session: str | None = None

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check_launch_cancel(self) -> None:
        if not self.launch_token or self.run_dir is None or self._cleanup_started:
            return
        path = self.run_dir / "launch-cancel.json"
        if path.is_file():
            request = load_json(path)
            if isinstance(request, dict) and request.get("launch_token") == self.launch_token:
                raise RuntimeError("launcher cancelled startup")

    def check_deadline(self, what: str) -> None:
        self.check_launch_cancel()
        if self.remaining() <= 0:
            raise TimeoutError(f"total run deadline exceeded during {what}")

    # -- compact live status artifact

    def write_status(self, status: str) -> None:
        """Atomic compact live artifact with a FIXED field set (run/task
        hash, workspace, status, timestamp, bridge pid, cleanup, notification
        outcome). No transcript, tool input or secret ever enters it; it
        doubles as the `start` handshake artifact. A failed write is reported
        and never fatal - state.json stays the detailed evidence."""
        if self.run_dir is None:
            return
        if status not in KNOWN_STATUS:
            print(f"refusing unknown live status {status!r}", flush=True)
            return
        self.status_now = status
        try:
            atomic_write_json(self.run_dir / STATUS_FILE, {
                "schema_version": STATUS_SCHEMA_VERSION,
                "run_id": self.run_id,
                "task_sha256": self.task_sha,
                "workspace": str(self.workspace),
                "status": status,
                "timestamp": time.time(),
                "bridge_pid": os.getpid(),
                "cleanup": self.cleanup_status,
                "notification": self.notification,
                "launch_token": self.launch_token,
            })
        except Exception as exc:  # noqa: BLE001
            print(redact_text(f"status write failed: {exc}", self.secrets),
                  flush=True)

    # -- bounded Codex notifications (opt-in; never on ordinary progress)

    def notify_preflight(self) -> None:
        """Read-only verification BEFORE any paid GLM work: bundled server
        launches, initialize + tools/list succeed, the one needed tool
        exists. No message is sent and the catalog is never dumped."""
        self.check_deadline("notification preflight")
        if self.notify is None:
            self.notify = load_notify_module()
        _thread_id, _pipe = self.notify.read_host_env()
        server = Path(self.notify_server) if self.notify_server \
            else self.notify.find_server(self.notify.codex_home(None))
        client = self.notify.NotifyClient(
            server, node=getattr(self.args, "notify_node", None) or self.args.node or "node",
            timeout=min(20.0, max(3.0, self.remaining())))
        try:
            client.start()
            client.initialize()
            client.verify_tool()
        finally:
            client.close()

    def notify_budget(self, grace: bool) -> float:
        """Transport overhead counts against the run deadline but never
        consumes the whole turn budget; the terminal notification shares the
        existing bounded post-deadline cleanup grace."""
        if grace:
            return float(NOTIFY_ATTEMPT_SECONDS)
        reserve = min(float(NOTIFY_RESERVED_SECONDS),
                      float(self.args.timeout) * 0.25)
        return min(float(NOTIFY_ATTEMPT_SECONDS),
                   max(1.0, self.remaining() - reserve))

    def load_ledger(self) -> dict:
        if self.ledger is None:
            data = {"schema_version": 1, "entries": []}
            if self.run_dir is not None:
                path = self.run_dir / LEDGER_FILE
                if path.is_file():
                    try:
                        loaded = load_json(path)
                        if isinstance(loaded, dict) \
                                and isinstance(loaded.get("entries"), list):
                            data = loaded
                        else:
                            raise ValueError("invalid notification ledger")
                    except (OSError, ValueError) as exc:
                        raise RuntimeError("cannot read notification ledger; refusing resend") from exc
            self.ledger = data
        return self.ledger

    def save_ledger(self) -> None:
        if self.ledger is None or self.run_dir is None:
            return
        atomic_write_json(self.run_dir / LEDGER_FILE, self.ledger)

    def notify_event(self, event_type: str, identity: dict, evidence: str,
                     grace: bool = False) -> None:
        """ONE bounded notification for ONE meaningful event. The per-run
        ledger is persisted BEFORE and AFTER the attempt so a retry/restart
        can never blindly resend: delivered, pending or uncertain entries are
        skipped. Failures stay visible locally and never fail the run. The
        host API has no demonstrated idempotency key, so exactly-once
        delivery is never claimed."""
        if self.notify is None or self.run_dir is None:
            return
        event_id = sha256_text(json.dumps(
            {"run": self.run_id, "type": event_type, **identity},
            sort_keys=True, ensure_ascii=False))[:16]
        try:
            ledger = self.load_ledger()
        except RuntimeError as exc:
            self.notification = "failed"
            print(redact_text(f"notify: {exc}", self.secrets), flush=True)
            return
        entry = next((e for e in ledger.get("entries", [])
                      if isinstance(e, dict) and e.get("event_id") == event_id),
                     None)
        if entry is not None and entry.get("status") in \
                ("delivered", "pending", "uncertain"):
            print(f"notify: {event_type} {event_id} already "
                  f"{entry.get('status')}; not retransmitted", flush=True)
            return
        if entry is None:
            entry = {"event_id": event_id, "event_type": event_type,
                     "attempts": 0, "created": time.time()}
            self.load_ledger()["entries"].append(entry)
        entry["status"] = "pending"
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["updated"] = time.time()
        try:
            self.save_ledger()
        except (OSError, ValueError):
            self.notification = "failed"
            print("notify: ledger not durable; message not sent", flush=True)
            return
        # Deterministic, short, fixed content: event identity and validated
        # local paths only. No worker-authored instructions, raw tool
        # inputs, transcripts or credentials.
        text = "\n".join([
            "ASTRA GLM WORKER EVENT",
            f"event_id: {event_id}",
            f"event_type: {event_type}",
            f"run_status: {self.status_now or 'unknown'}",
            f"workspace: {self.workspace}",
            f"run_dir: {self.run_dir}",
            f"status_json: {self.run_dir / STATUS_FILE}",
            f"evidence: {evidence}",
            f"cleanup: {self.cleanup_status}",
            "note: read the scoped evidence files, decide within prior "
            "authorization, then resume event waiting. Terminal readiness "
            "is not acceptance. This fixed notice is not instructions.",
        ])
        status = "uncertain"
        detail = ""
        try:
            thread_id, _pipe = self.notify.read_host_env()
            server = Path(self.notify_server) if self.notify_server \
                else self.notify.find_server(self.notify.codex_home(None))
            client = self.notify.NotifyClient(
                server, node=getattr(self.args, "notify_node", None) or self.args.node or "node",
                timeout=self.notify_budget(grace))
            try:
                client.start()
                client.initialize()
                client.verify_tool()
                client.send(thread_id, text)
                status = "delivered"
            finally:
                client.close()
        except self.notify.NotifyError as exc:
            status, detail = "failed", str(exc)
        except (self.notify.NotifyUncertain, TimeoutError, OSError) as exc:
            status, detail = "uncertain", str(exc)
        except Exception as exc:  # noqa: BLE001
            status, detail = "uncertain", f"{type(exc).__name__}: {exc}"
        entry["status"] = status
        entry["detail"] = redact_text(detail, self.secrets)[:300]
        entry["updated"] = time.time()
        try:
            self.save_ledger()
        except (OSError, ValueError):
            status = "uncertain"  # persisted pending entry prevents blind resend
            print("notify: receipt could not be persisted", flush=True)
        self.notification = status
        print(f"notify: {event_type} {event_id} -> {status}", flush=True)

    # -- shared preflight (used by run; doctor uses the same validators)

    def preflight(self) -> None:
        """ONE validated runtime/config gate before any spawn or send:
        runtime discovery, exact offline version gate, builtin provider
        config (revision + official endpoint + access + enabled model rule)
        and credential presence. The API key is loaded to memory only after
        every config check passed."""
        self.check_deadline("preflight")
        cjs = detect_zcode_cjs(self.args.zcode_path)
        node = self.args.node or "node"
        runtime_version(node, cjs,
                        timeout=min(VERSION_SUBPROC_TIMEOUT, self.remaining()))
        builtin_file = builtin_config_path(cjs).resolve()
        if not builtin_file.is_file():
            raise RuntimeError(f"builtin provider config missing: {builtin_file}")
        try:
            builtin = load_json(builtin_file)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"builtin provider config unreadable: {exc}") from None
        problems = builtin_config_problems(builtin)
        if problems:
            raise RuntimeError("builtin provider config invalid: "
                               + "; ".join(problems))
        ok, cred_problems = validate_credential(self.home)
        if not ok:
            raise RuntimeError("credential/config invalid: "
                               + "; ".join(cred_problems))
        # Match doctor's personal-config prerequisite; do not silently fall
        # back to runtime defaults when the explicitly selected home is broken.
        try:
            personal = load_json(self.home / "v2/provider_config.json")
            if not isinstance(personal, dict):
                raise ValueError("expected an object")
        except (OSError, ValueError) as exc:
            raise RuntimeError("personal provider config missing or invalid") from exc
        self.cjs = cjs
        self.builtin_file = builtin_file
        self.builtin_revision = int(builtin["revision"])
        self.secrets.append(credential_api_key(self.home))

    # -- child process

    def spawn(self) -> Transport:
        node = self.args.node or "node"
        env = os.environ.copy()
        env["ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"] = str(self.builtin_file)
        env["ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"] = \
            str(self.home / "v2/provider_config.json")
        return Transport([node, str(self.cjs), "app-server"], env, self.workspace)

    # -- event pump (single pump for RPC waits and turn wait)

    def pump(self, tr: Transport, rid: int | None, deadline: float):
        """Read frames until `rid` resolves or `deadline`. session/event frames
        are stored exactly once and never dropped, so a completion that
        arrives before the send ack is preserved. Interaction frames are
        handled inline. Raises on EOF, overflow/malformed/non-object frames,
        or RPC errors for the awaited id."""
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("timeout waiting for "
                                   + (f"rpc id {rid}" if rid else "event"))
            if tr.protocol_error:
                raise RuntimeError(tr.protocol_error)
            self.check_launch_cancel()
            m = tr.poll(min(0.25, max(0.05, deadline - time.monotonic())))
            if m is None:
                continue
            if m.get("eof"):
                raise RuntimeError("child process exited (EOF)")
            if m.get("overflow"):
                raise RuntimeError(tr.protocol_error or "transport overflow")
            if "method" in m and "id" in m:
                self.interaction(tr, m)
                continue
            if m.get("method") == "session/event":
                self.record_event(m.get("params", {}) or {})
                if rid is None and self.terminal_event is not None:
                    return None   # terminal reached; hand control back
                continue
            if rid is not None and m.get("id") == rid:
                if "error" in m:
                    raise RuntimeError("rpc error: "
                                       + redact_text(json.dumps(m["error"]),
                                                     self.secrets))
                return m.get("result")
            # Unrelated response frames are dropped (never awaited).

    def record_event(self, ev: dict) -> None:
        """Validate and store one session/event. The real wire shape carries
        sessionId/turnId on the event params (outer object), not the payload;
        missing identifiers, a non-object payload or an unknown session/turn
        are protocol failures, never silently adopted."""
        if not isinstance(ev, dict):
            raise RuntimeError("session/event params must be an object")
        typ = ev.get("type")
        if not isinstance(typ, str) or not typ:
            raise RuntimeError("session/event without a type")
        payload = ev.get("payload", {}) or {}
        if not isinstance(payload, dict):
            raise RuntimeError("session/event payload must be an object")
        ev_session = ev.get("sessionId")
        ev_turn = ev.get("turnId")
        if not isinstance(ev_session, str) or not ev_session \
                or not isinstance(ev_turn, str) or not ev_turn:
            raise RuntimeError("session/event missing sessionId/turnId")
        if self.session_id is not None and ev_session != self.session_id:
            raise RuntimeError(
                f"event for unrelated session {ev_session!r}")
        if self.turn_id is not None and ev_turn != self.turn_id:
            raise RuntimeError(f"event for stale turn {ev_turn!r}")
        if self.turn_id is None:
            self.turn_id = ev_turn
        if len(self.events) >= MAX_EVENTS:
            # Bounded memory: drop the oldest non-terminal event.
            for i, old in enumerate(self.events):
                if old.get("type") not in ("turn.completed", "turn.failed"):
                    del self.events[i]
                    break
            else:
                self.events.pop(0)
        # Redact BEFORE any clipping so a key straddling the clip boundary
        # can never leave a fragment behind; dictionary KEYS are sanitized too.
        self.events.append(redact_obj(ev, self.secrets))
        if typ == "turn.completed":
            self.terminal_event = ev
            self.usage = payload.get("usage") or self.usage
            resp = payload.get("response") or payload.get("lastMessage") \
                or payload.get("text")
            if isinstance(resp, str):
                self.worker_response = _redact_str(resp, self.secrets, clip=False)
        elif typ == "turn.failed":
            self.terminal_event = ev

    # -- rpc

    def send(self, tr: Transport, method: str, params: dict) -> int:
        self.check_deadline("send " + method)
        self.seq += 1
        tr.send(self.seq, method, params)
        return self.seq

    def receive(self, tr: Transport, rid: int, timeout: float = 45):
        self.check_deadline("rpc " + str(rid))
        end = min(time.monotonic() + timeout, self.deadline)
        return self.pump(tr, rid, end)

    # -- reverse interactions

    def interaction(self, tr: Transport, msg: dict) -> None:
        if self._cleanup_started:
            # During cleanup no reverse interaction is answered with auth or
            # grants; fail the request at top level instead.
            try:
                tr.reply_error(msg["id"], -32000, "cleanup in progress")
            except (OSError, RuntimeError):
                pass
            return
        method = msg["method"]
        params = msg.get("params", {}) or {}
        if method == "session/requestRuntimePreferences":
            requested = params.get("sessionId")
            expected = self.session_id or self.preference_session
            if expected is None and self._creating_session and isinstance(requested, str) and requested:
                # Creation asks for host preferences before returning a snapshot.
                # This hint grants no auth or tools and must match that snapshot.
                expected = self.preference_session = requested
            if not expected or requested != expected \
                    or params.get("scope") not in ("runtime-materialization", "user-execution"):
                self.blocked_reason = "runtime preferences requested outside active session/scope"
                tr.reply_error(msg["id"], -32000, self.blocked_reason)
            else:
                # Verified 0.16.9 schema. These are per-run host preferences;
                # never enable automatic answers to questions on the user's behalf.
                tr.reply(msg["id"], {"askUserQuestionAutoResolutionEnabled": False,
                                     "memoryEnabled": False,
                                     "nativeSearchEnhancementsEnabled": False,
                                     "modelContextBudgetStrategy": "preflight-v1"})
            return
        if method == "interaction/requestProviderRuntimeHeaders":
            self.handle_headers(tr, msg["id"], params)
            return
        if method == "interaction/requestPermission":
            self.handle_permission(tr, msg["id"], params)
            return
        # Unsupported reverse method (incl. captcha retries): protocol error
        # at top level, never auth, never a guessed response.
        self.blocked_reason = self.blocked_reason or \
            f"unsupported interaction {method}"
        try:
            tr.reply_error(msg["id"], -32601, "unsupported interaction")
        except (OSError, RuntimeError):
            pass

    def handle_headers(self, tr: Transport, msg_id, params: dict) -> None:
        """Supply the credential ONLY for the exact expected scope: active
        session, our provider/model selection and our workspace."""
        if self._cleanup_started or not self.session_id or self.workspace is None:
            tr.reply_error(msg_id, -32000, "cleanup in progress")
            return
        expected = {
            "providerId": PROVIDER_ID,
            "sessionId": self.session_id,
            "reason": "model-request",
        }
        mismatches = [f"{k}={params.get(k)!r}" for k, v in expected.items()
                      if params.get(k) != v]
        sel = params.get("modelSelection", {}) or {}
        if sel.get("providerId") != PROVIDER_ID or sel.get("modelId") != MODEL_ID:
            mismatches.append(f"modelSelection={sel.get('providerId')!r}/"
                              f"{sel.get('modelId')!r}")
        ws = params.get("workspace", {}) or {}
        if self.workspace is not None and (
                ws.get("workspacePath") != str(self.workspace)
                or ws.get("workspaceKey") != str(self.workspace)):
            mismatches.append("workspace mismatch")
        if mismatches:
            self.blocked_reason = self.blocked_reason or (
                "runtime headers requested outside expected scope: "
                + "; ".join(mismatches))
            try:
                tr.reply_error(msg_id, -32000,
                               "runtime headers request outside expected scope")
            except (OSError, RuntimeError):
                pass
            return
        try:
            tr.reply(msg_id, {"headersApplied": True,
                              "requestAuth": {"apiKey": self.secrets[0]}})
        except (OSError, RuntimeError):
            pass

    def handle_permission(self, tr: Transport, msg_id, data: dict) -> None:
        # ---- exact identity first: every identifier is required
        required = ("sessionId", "turnId", "requestId", "toolCallId",
                    "toolName", "riskLevel")
        missing = [k for k in required
                   if not isinstance(data.get(k), str) or not data.get(k)]
        if missing:
            self.blocked_reason = self.blocked_reason or \
                ("permission request missing identifiers: "
                 + ", ".join(missing))
            self._reply_permission(tr, msg_id, data.get("toolName", "?"),
                                   data.get("riskLevel"), "deny",
                                   "missing request identifiers")
            return
        if any(secret and secret in str(data[k])
               for secret in self.secrets for k in required):
            self.blocked_reason = "credential material in permission metadata"
            self._reply_permission(tr, msg_id, "[REDACTED]", "unknown",
                                   "deny", "invalid permission metadata")
            return
        if data.get("permissionType") == "permissionUpdates" or "permissionUpdates" in data:
            self.blocked_reason = "persistent permission changes are never granted"
            self._reply_permission(tr, msg_id, data["toolName"], data["riskLevel"],
                                   "deny", self.blocked_reason)
            return
        session = data["sessionId"]
        turn = data["turnId"]
        request_id = data["requestId"]
        call_id = data["toolCallId"]
        tool = data["toolName"]
        level = data["riskLevel"]

        if session != self.session_id:
            self.blocked_reason = self.blocked_reason or \
                f"permission request bound to unexpected session {session!r}"
            self._reply_permission(tr, msg_id, tool, level, "deny",
                                   "unexpected session")
            return
        if self.turn_id is None or turn != self.turn_id:
            self.blocked_reason = self.blocked_reason or \
                f"permission request bound to unexpected turn {turn!r}"
            self._reply_permission(tr, msg_id, tool, level, "deny",
                                   "unexpected turn")
            return

        input_obj = data.get("input")
        try:
            raw_input = json.dumps(input_obj, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            raw_input = repr(input_obj)
        identity = sha256_text(json.dumps(
            {"tool": tool, "level": level, "input": raw_input},
            sort_keys=True, ensure_ascii=False))
        key = f"{session}|{turn}|{request_id}|{call_id}"

        # Same logical request with CHANGED semantics (tool, level or input)
        # is an attack signal - never approved, always surfaced.
        if key in self.perm_identities and self.perm_identities[key] != identity:
            self.blocked_reason = self.blocked_reason or \
                f"permission request {key} reannounced with changed semantics"
            self._reply_permission(tr, msg_id, tool, level, "deny",
                                   "changed semantics")
            return

        if self._cleanup_started:
            # Never grant (or reuse a cached grant) during cleanup.
            self._reply_permission(tr, msg_id, tool, level, "deny",
                                   "cleanup in progress")
            return

        # Deduplicate exact reannouncements with the reviewed decision.
        if key in self.perm_cache:
            self._reply_permission(tr, msg_id, tool, level,
                                   self.perm_cache[key],
                                   "reused reviewed decision for identical request")
            return
        self.perm_identities[key] = identity

        if data.get("permissionType") == "permissionUpdates" \
                or "permissionUpdates" in data:
            self._reply_permission(tr, msg_id, tool, level, "deny",
                                   "persistent permission changes are never granted")
            return

        if level in ("low", "medium"):
            decision = "allow"
            reason = ("user-authorized local implementation task; one-shot "
                      "decision, no persistent permission changes")
        elif level == "high":
            decision = self.request_high_approval(data, key, raw_input, identity)
            reason = ("reviewed via pending-permission.json by Astra"
                      if decision == "allow" else "denied pending Astra review")
            if decision != "allow":
                self.blocked_reason = self.blocked_reason or \
                    f"high-risk request for {tool} was not approved"
        else:
            # critical / unknown risk level => blocked.
            self.blocked_reason = self.blocked_reason or \
                f"unsupported risk level {level!r} for {tool}; blocked for Astra"
            decision = "deny"
            reason = "unsupported risk level"
        self.perm_cache[key] = decision
        self._reply_permission(tr, msg_id, tool, level, decision, reason)

    def _reply_permission(self, tr, msg_id, tool, level, decision, reason) -> None:
        self.perm_log[f"{tool}|{level}|{reason}"] = decision
        ordinary = level in ("low", "medium") and decision == "allow"
        # Ordinary low/medium grants stay silent by default (audit data is
        # preserved in state.json); everything else is printed.
        if not ordinary or bool(getattr(self.args, "verbose", False)):
            print(redact_text(f"permission {tool} level={level} decision={decision}",
                              self.secrets), flush=True)
        try:
            tr.reply(msg_id, {"decision": decision, "reason": reason})
        except (OSError, RuntimeError):
            pass

    def request_high_approval(self, data: dict, key: str, raw_input: str,
                              identity: str) -> str:
        """Write the COMPLETE reviewed input (never truncated - an oversized
        input is refused outright, never approved from a clipped command),
        wait bounded for Astra's approval file, and clear both markers once
        the request is resolved."""
        if len(raw_input) > MAX_PERM_INPUT_CHARS:
            self.blocked_reason = self.blocked_reason or (
                f"oversized permission input exceeds {MAX_PERM_INPUT_CHARS} chars; "
                "refused rather than reviewed truncated")
            print("permission input oversized -> deny", flush=True)
            return "deny"
        safe_input = redact_obj(data.get("input"), self.secrets, clip=False)
        if safe_input != data.get("input"):
            self.blocked_reason = "permission input cannot be displayed completely; refused"
            return "deny"
        self.perm_pending = True
        req = {
            "stable_hash": key,
            "request_id": data.get("requestId"),
            "tool_call_id": data.get("toolCallId"),
            "tool_name": data.get("toolName"),
            "risk_level": data.get("riskLevel"),
            "input": safe_input,
            "input_hash": identity,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "requested_at": time.time(),
            "decision_file": "approval.json",
        }
        req_path = self.run_dir / "pending-permission.json"
        tmp = req_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(req, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(req_path)
        print("HIGH_PERMISSION_REQUEST " + str(req_path), flush=True)
        perm_deadline = min(time.monotonic() + PERM_WAIT_SECONDS, self.deadline)
        self.write_status("needs_approval")
        if self.notify is not None:
            # Notify only AFTER the exact pending file exists, once per
            # distinct request (the ledger dedupes identical events).
            self.notify_event("permission",
                              {"stable_hash": key, "input_hash": identity},
                              str(req_path))
        try:
            while time.monotonic() < perm_deadline:
                self.check_deadline("high permission approval wait")
                ap = self.run_dir / "approval.json"
                if ap.is_file():
                    try:
                        dec = load_json(ap)
                    except (OSError, ValueError):
                        dec = {}
                    if dec.get("stable_hash") == key \
                            and dec.get("request_id") == req["request_id"] \
                            and dec.get("tool_call_id") == req["tool_call_id"] \
                            and dec.get("input_hash") == identity \
                            and dec.get("session_id") == self.session_id \
                            and dec.get("decision") in ("allow", "deny"):
                        # Consume the approval so it cannot be replayed.
                        try:
                            ap.unlink()
                        except OSError:
                            pass
                        return dec["decision"]
                    print("approval.json present but identity mismatch "
                          "(stale?); still waiting", flush=True)
                time.sleep(min(0.5, max(0.05,
                                        perm_deadline - time.monotonic())))
            print("permission approval timeout/expiry -> deny", flush=True)
            self.blocked_reason = self.blocked_reason or \
                "high permission approval not granted in time"
            return "deny"
        finally:
            # The request is resolved (allow, deny or expiry): clear the
            # pending marker so a stale file can never grant anything later.
            self.perm_pending = False
            try:
                req_path.unlink(missing_ok=True)
            except (OSError, TypeError):
                pass
            if self.status_now == "needs_approval":
                self.write_status("running")

    # -- turn wait (uses the same pump; early completion is preserved)

    def wait_turn(self, tr: Transport, timeout: float) -> dict:
        end = min(time.monotonic() + timeout, self.deadline)
        # Events already stored during earlier RPC waits count too.
        term = self.terminal_event
        if term is not None:
            self.assert_terminal(term)
            return term
        while True:
            self.check_deadline("turn wait")
            self.pump(tr, None, end)
            term = self.terminal_event
            if term is not None:
                self.assert_terminal(term)
                return term

    def assert_terminal(self, ev: dict) -> None:
        # Required session/turn metadata: a terminal event without the exact
        # session and turn identifiers is a protocol failure, never success.
        ev_session = ev.get("sessionId")
        if not ev_session or ev_session != self.session_id:
            raise RuntimeError(
                f"terminal event for unrelated session {ev_session!r}")
        ev_turn = ev.get("turnId")
        if not ev_turn or ev_turn != self.turn_id:
            raise RuntimeError(f"terminal event for stale turn {ev_turn!r}")
        if ev.get("type") != "turn.completed":
            raise RuntimeError("turn.failed: "
                               + redact_text(json.dumps(ev), self.secrets))
        payload = ev.get("payload", {}) or {}
        if payload.get("resultType") != "success":
            raise RuntimeError("turn completed but resultType="
                               + repr(payload.get("resultType")))

    # -- outputs

    def write_outputs(self, status: str, detail: str) -> bool:
        """Returns True iff all artifacts were written; a failed artifact write
        must never be reported as success. Only sanitized data is serialized."""
        try:
            state = {
                "schema_version": SCHEMA_VERSION,
                "task_sha256": self.task_sha,
                "task_path": str(self.task),
                "workspace": str(self.workspace),
                "session_id": self.session_id,
                "model": MODEL_ID,
                "provider": PROVIDER_ID,
                "credential_provider": CREDENTIAL_PROVIDER_ID,
                "effort": self.effort,
                "status": status,
                "detail": redact_text(str(detail), self.secrets),
                "started": self.started_epoch,
                "finished": time.time(),
                "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
                "cleanup": self.cleanup_status,
                "usage": redact_obj(self.usage or {}, self.secrets),
                "worker_response": self.worker_response,
                "git_before": self.git_before,
                "git_after": self.git_after,
                "permission_decisions": self.perm_log,
                "blocked_reason": redact_text(str(self.blocked_reason),
                                              self.secrets)
                if self.blocked_reason else None,
            }
            (self.run_dir / "state.json").write_text(
                json.dumps(redact_obj(state, self.secrets), indent=2,
                           ensure_ascii=False), encoding="utf-8")
            with (self.run_dir / "events.ndjson").open("w", encoding="utf-8") as fh:
                for ev in self.events:
                    # Values are clipped/redacted BEFORE dumps so every line
                    # stays valid JSON (never slice serialized JSON).
                    fh.write(json.dumps(redact_obj(ev, self.secrets),
                                        ensure_ascii=False) + "\n")
            usage_safe = json.dumps(redact_obj(self.usage or {}, self.secrets))
            detail_safe = redact_text(str(detail), self.secrets)
            blocked_safe = redact_text(str(self.blocked_reason), self.secrets) \
                if self.blocked_reason else "none"
            response_safe = self.worker_response \
                if self.worker_response is not None else "none"
            (self.run_dir / "result.md").write_text(
                _redact_str(f"# Worker result\n\nStatus: **{status}** (never accepted)\n\n"
                f"Task: {self.task} (sha256 {self.task_sha})\n\n"
                f"Workspace: {self.workspace}\n\nSession: {self.session_id}\n\n"
                f"Model: {PROVIDER_ID}/{MODEL_ID} effort={self.effort}\n\n"
                f"Cleanup: {self.cleanup_status}\n\n"
                f"Usage: {usage_safe}\n\n"
                f"Worker response: {response_safe}\n\n"
                f"Blocked reason: {blocked_safe}\n\n"
                f"Detail: {detail_safe}\n", self.secrets, clip=False),
                encoding="utf-8")
            return True
        except Exception as wexc:  # noqa: BLE001
            print(f"CRITICAL: could not write outputs: "
                  f"{redact_text(str(wexc), self.secrets)}", file=sys.stderr)
            return False

    # -- snapshot validation (verified real shapes)

    def validate_creation(self, created: dict) -> None:
        if not isinstance(created, dict):
            raise RuntimeError("session/create returned a non-object result")
        sess = created.get("session")
        if not isinstance(sess, dict):
            raise RuntimeError("session/create result missing session object")
        self.session_id = sess.get("sessionId")
        if not self.session_id:
            raise RuntimeError("no sessionId returned")
        if self.preference_session and self.preference_session != self.session_id:
            raise RuntimeError("created session differs from runtime preference request")
        ws = sess.get("workspace", {})
        if not isinstance(ws, dict) or \
                ws.get("workspacePath") != str(self.workspace) or \
                ws.get("workspaceKey") != str(self.workspace):
            raise RuntimeError(f"returned workspace mismatch: {ws!r}")
        # Session model carries ONLY providerId/modelId (no options).
        sm = sess.get("model", {})
        if (sm.get("providerId"), sm.get("modelId")) != (PROVIDER_ID, MODEL_ID):
            raise RuntimeError(f"session model mismatch: {sm!r}")
        if "options" in sm:
            raise RuntimeError("session model unexpectedly carries options")
        status = sess.get("status")
        if status not in ("idle", "created", None):
            raise RuntimeError(f"unexpected initial session status {status!r}")

        settings = created.get("settings", {}) or {}
        cur = (settings.get("model", {}) or {}).get("current") or {}
        if (cur.get("providerId"), cur.get("modelId")) != (PROVIDER_ID, MODEL_ID) \
                or (cur.get("options") or {}).get("reasoningLevel") != self.effort:
            raise RuntimeError(f"settings.model.current mismatch: {cur!r}")
        # Catalog must list the requested effort.
        available = (settings.get("model", {}) or {}).get("available") or []
        levels = None
        for entry in available:
            ref = entry.get("ref", {})
            if (ref.get("providerId"), ref.get("modelId")) == (PROVIDER_ID, MODEL_ID):
                levels = [l.get("value")
                          for l in (entry.get("reasoning", {}).get("levels") or [])]
        if levels is None:
            raise RuntimeError("catalog entry for selected model missing")
        if self.effort not in levels:
            raise RuntimeError(f"effort {self.effort!r} not in catalog levels "
                               f"{levels!r}")
        runtime = created.get("runtime", {}) or {}
        pending = runtime.get("pendingRequestIds")
        if not isinstance(pending, list):
            # Missing is NOT empty: an absent runtime block is a failure.
            raise RuntimeError("runtime.pendingRequestIds missing after create")
        if pending:
            raise RuntimeError("unexpected pending requests after create")

    def validate_settled(self, settled) -> None:
        if not isinstance(settled, dict) or not isinstance(settled.get("session"), dict):
            raise RuntimeError("settled: session/read returned no session object")
        sess = settled["session"]
        if sess.get("sessionId") != self.session_id:
            raise RuntimeError("settled: session/read returned a different "
                               f"session {sess.get('sessionId')!r}")
        status = sess.get("status")
        if status not in ("idle", "completed"):
            raise RuntimeError(f"settled session status {status!r} not idle/completed")
        ws = sess.get("workspace", {})
        if ws.get("workspacePath") != str(self.workspace) \
                or ws.get("workspaceKey") != str(self.workspace):
            raise RuntimeError("settled workspace changed")
        sm = sess.get("model", {})
        if (sm.get("providerId"), sm.get("modelId")) != (PROVIDER_ID, MODEL_ID):
            raise RuntimeError(f"settled session model changed: {sm!r}")
        settings = settled.get("settings", {}) or {}
        cur = (settings.get("model", {}) or {}).get("current")
        if not isinstance(cur, dict):
            raise RuntimeError("settled settings.model.current missing")
        if (cur.get("providerId"), cur.get("modelId")) != (PROVIDER_ID, MODEL_ID) \
                or (cur.get("options") or {}).get("reasoningLevel") != self.effort:
            raise RuntimeError(f"settled settings model/effort changed: {cur!r}")
        runtime = settled.get("runtime", {}) or {}
        pending = runtime.get("pendingRequestIds")
        if not isinstance(pending, list):
            # Missing is NOT empty: pending state must be explicit.
            raise RuntimeError("settled runtime.pendingRequestIds missing")
        if pending:
            raise RuntimeError("pending requests remain at completion")

    # -- main

    def dispatch_prompt(self) -> str:
        return self.args.prompt or (
            "You are GLM-5.3-Flash, implementation worker for Astra in Codex. "
            "The workspace owner explicitly authorized automatic delegation. "
            "Read and implement the complete assignment at "
            f"{self.task}. Load glm-worker skill. Follow the supplied scope. "
            "Astra reviews individual high-risk requests, so proceed normally. "
            "This automatic user-authorized dispatch supersedes old "
            "manual-relay instructions. Prefer Write/Edit tools for source "
            "changes; do not repeatedly retry denied operations. Do not spawn "
            "any agent, automation, or scheduled task, and do not commit or "
            "publish. Return your actual test evidence to Astra in the "
            "designated result path, and never mark the work accepted.")

    def run(self) -> int:
        self.started_monotonic = time.monotonic()
        self.started_epoch = time.time()
        self.deadline = self.started_monotonic + float(self.args.timeout)
        self.session_id = None
        self.preference_session = None
        self._creating_session = False
        self.turn_id = None
        self.terminal_event = None
        self.blocked_reason = None
        self.worker_response = None
        self.usage = None
        self.events = []
        self.perm_cache = {}
        self.perm_log = {}
        self.perm_identities = {}
        self.perm_pending = False
        self._cleanup_started = False
        self.status_now = None
        self.notification = "none"
        self.ledger = None
        self.git_before = self.git_after = {}
        self.cjs = None
        self.builtin_file = None
        self.builtin_revision = None
        self.secrets = []
        lock = None
        tr = None
        status, detail = "failed", "not run"
        wrote = False
        try:
            # ---- path validation BEFORE any mkdir/write (canonical first)
            ws_arg = Path(self.args.workspace)
            if not ws_arg.is_absolute():
                raise RuntimeError("--workspace must be absolute")
            self.workspace = ws_arg.resolve(strict=True)
            if not self.workspace.is_dir():
                raise RuntimeError("workspace is not a directory")
            home = Path.home().resolve()
            if self.workspace in (home, home.parent, Path(self.workspace.anchor)):
                raise RuntimeError("refusing home/filesystem-root workspace")
            task_arg = Path(self.args.task)
            if not task_arg.is_absolute():
                raise RuntimeError("--task must be absolute")
            self.task = task_arg.resolve(strict=True)
            if not self.task.is_file() or not inside(self.workspace, self.task) \
                    or not no_symlink_escape(self.workspace, self.task):
                raise RuntimeError(
                    "task must be an existing regular file inside the "
                    "workspace, no symlink escapes")
            self.task_sha = sha256_text(self.task.read_text(encoding="utf-8"))
            rd_arg = Path(self.args.run_dir)
            if not rd_arg.is_absolute():
                raise RuntimeError("--run-dir must be absolute")
            # Canonical containment BEFORE any directory is created: the full
            # target is resolved non-strict and every existing component is
            # verified, so '..' or a parent link can never pre-create
            # directories outside the workspace.
            try:
                rd = resolve_contained_dir(self.workspace, rd_arg)
            except ValueError as exc:
                raise RuntimeError(f"invalid run-dir: {exc}") from None
            if self.launch_token:
                # Prepared by `start` for THIS exact launch: an existing
                # empty directory (plus launcher metadata) is adopted only
                # with the matching token; arbitrary old run directories are
                # never allowed.
                if not rd.is_dir():
                    raise RuntimeError("prepared run-dir is missing")
                launch = load_json(rd / LAUNCHER_LOG)
                if not isinstance(launch, dict) or any(launch.get(k) != v for k, v in {
                    "launch_token": self.launch_token, "workspace": str(self.workspace),
                    "task": str(self.task), "run_dir": str(rd),
                }.items()):
                    raise RuntimeError("prepared run-dir launch identity mismatch")
                unexpected = [p.name for p in rd.iterdir()
                              if p.name not in LAUNCHER_FILES]
                if unexpected:
                    raise RuntimeError(
                        "prepared run-dir is not fresh: "
                        + ", ".join(unexpected[:5]))
            else:
                try:
                    rd.mkdir(parents=True)
                except FileExistsError:
                    # An existing run dir (even empty) is refused so previous
                    # evidence is never overwritten.
                    raise RuntimeError(
                        "run-dir already exists; refusing to reuse or "
                        "overwrite previous evidence") from None
            self.run_dir = rd
            self.run_id = sha256_text(json.dumps(
                [str(self.task), self.task_sha, str(rd), self.started_epoch],
                sort_keys=True))[:16]

            lock = WorkspaceLock(self.workspace, rd)
            lock.acquire()
            # First live artifact immediately after the lock: this is the
            # real `start` handshake evidence (one writer, fresh run).
            self.write_status("starting")
            self.git_before = git_snapshot(
                self.workspace, budget=max(1.0, self.remaining()))

            # ---- ONE shared preflight before any spawn/send
            if self.notify_codex:
                self.notify_preflight()
            self.preflight()
            self.check_deadline("pre-spawn")
            tr = self.spawn()
            builtin_rev = ("zcode-builtin:" + str(self.builtin_revision) + ":"
                           + sha256_text(str(self.builtin_file.resolve())))

            sync = self.receive(tr, self.send(tr, "provider/updateAccountConfig", {
                "revision": "astra-" + self.task_sha[:12],
                "basedOnZCodeBuiltinRevision": builtin_rev,
                "providers": {PROVIDER_ID: {
                    "access": {"type": "zhipu-account", "entitled": True}}},
                "states": {PROVIDER_ID: {
                    "availability": "available", "entitled": True,
                    "current": True}}},
            ), 30)
            if not isinstance(sync, dict):
                raise RuntimeError("provider/updateAccountConfig returned a "
                                   "non-object result")

            self._creating_session = True
            created = self.receive(tr, self.send(tr, "session/create", {
                "workspace": {"workspacePath": str(self.workspace),
                              "workspaceKey": str(self.workspace)},
                "model": {"providerId": PROVIDER_ID, "modelId": MODEL_ID,
                          "options": {"reasoningLevel": self.effort}},
                "thoughtLevel": self.effort,
                "mode": "build",
                "titleGenerationEnabled": False,
                "toolDenylist": DENY_TOOLS,
                "dynamicWorkflowEnabled": False,
                "offPeakToolEnabled": False,
            }), 60)
            self._creating_session = False
            self.validate_creation(created)

            self.receive(tr, self.send(tr, "session/subscribe", {
                "sessionId": self.session_id,
                "deliveryKind": "desktop-continuous"}), 30)
            ack = self.receive(tr, self.send(tr, "session/send", {
                "sessionId": self.session_id,
                "content": self.dispatch_prompt(),
                "modelSelection": {"providerId": PROVIDER_ID,
                                   "modelId": MODEL_ID,
                                   "options": {"reasoningLevel": self.effort}},
            }), 60)
            # The send ack is required protocol evidence: accepted must be
            # true for our own session before the turn counts as dispatched.
            if not isinstance(ack, dict) or ack.get("accepted") is not True:
                raise RuntimeError("session/send was not accepted: "
                                   + redact_text(json.dumps(ack), self.secrets))
            if ack.get("sessionId") != self.session_id:
                raise RuntimeError("session/send ack for a different session "
                                   f"{ack.get('sessionId')!r}")
            self.write_status("running")
            self.wait_turn(tr, float(self.args.timeout))
            self.validate_settled(self.receive(tr, self.send(tr, "session/read", {
                "sessionId": self.session_id}), 30))
            if self.perm_pending:
                raise RuntimeError("a permission request is still pending")
            if self.blocked_reason:
                status, detail = "blocked", self.blocked_reason
            else:
                status, detail = "ready_for_review", (
                    f"turn completed successfully; {len(self.events)} events "
                    f"captured; permission decisions: {len(self.perm_log)}")
        except KeyboardInterrupt:
            status = "blocked"
            detail = "interrupted by Ctrl-C; cleaning owned process tree"
            self.blocked_reason = self.blocked_reason or detail
        except Exception as exc:  # noqa: BLE001 - top-level worker boundary
            if isinstance(exc, TimeoutError):
                detail = f"timeout: {exc}"
            else:
                detail = f"{type(exc).__name__}: {exc}"
            if self.blocked_reason:
                status = "blocked"
                detail = f"{detail} | blocked: {self.blocked_reason}"
            else:
                status = "failed"
        finally:
            self._cleanup_started = True
            try:
                if tr is not None and self.session_id:
                    # Bounded cleanup grace, separate from the run deadline.
                    try:
                        end = time.monotonic() + 8
                        self.pump(tr, self.send(tr, "session/stop",
                                                {"sessionId": self.session_id}),
                                  end)
                    except Exception:  # noqa: BLE001
                        pass
                if tr is not None:
                    self.cleanup_status = tr.stop()
                else:
                    self.cleanup_status = "not-started"
            except Exception as cexc:  # noqa: BLE001
                self.cleanup_status = "uncertain"
                detail += (" | cleanup error: "
                           + redact_text(str(cexc), self.secrets))
            if self.cleanup_status == "uncertain":
                detail += (" | cleanup_uncertain: child tree could not be "
                           "proven gone; workspace lock preserved")
                status = "blocked" if status == "ready_for_review" else status
                if status == "failed" and not self.blocked_reason:
                    self.blocked_reason = "cleanup uncertain"
                    status = "blocked"
                if lock:
                    lock.preserve()
            self.git_after = git_snapshot(
                self.workspace, budget=max(1.0, self.remaining())) \
                if self.workspace else {}
            if self.run_dir is not None:
                wrote = self.write_outputs(status, detail)
            if lock:
                # The ZCode tree has stopped (or its lock was preserved).
                # Release our writer lock before waking the next coordinator turn.
                lock.release()
                lock = None
            if wrote and self.run_dir is not None:
                # Terminal live status only AFTER evidence and cleanup are
                # recorded; only then may Astra be woken (once).
                self.write_status(status)
                if self.notify is not None:
                    self.notify_event("terminal", {"status": status},
                                      "result.md + state.json", grace=True)
                    self.write_status(status)   # outcome of the notification
            elif self.run_dir is not None and self.notify is not None:
                print("notify: terminal notification skipped; detailed "
                      "evidence could not be written", flush=True)
            if lock:
                # Always release: closes the descriptor even when the lock
                # file itself was preserved (held=False after preserve()).
                lock.release()
        if not wrote:
            status = "failed"
            detail += " | artifact write failed; success cannot be claimed"
        print(f"run status: {status}")
        print(f"detail: {redact_text(detail, self.secrets)}")
        return 0 if status == "ready_for_review" else 1


# ---------------------------------------------------------------- start/status

def read_status_artifact(path: Path) -> dict | None:
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stop_owned_child(child: subprocess.Popen) -> str:
    """Bounded tree stop of a process we own and never handed off."""
    try:
        if child.poll() is not None:
            return "uncertain"  # parent exit alone cannot prove descendants stopped
        if os.name == "nt":
            kill = subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                capture_output=True, timeout=TASKKILL_TIMEOUT)
            if kill.returncode != 0:
                return "uncertain"
        else:
            try:
                os.killpg(child.pid, 9)
            except (ProcessLookupError, PermissionError):
                child.kill()
        try:
            child.wait(timeout=STOP_WAIT_TIMEOUT)
            return "stopped"
        except subprocess.TimeoutExpired:
            return "uncertain"
    except (OSError, subprocess.TimeoutExpired):
        return "uncertain"


def cmd_start(args) -> int:
    """Convenience bounded launcher: cheap validation, read-only notification
    preflight (no send, before ANY paid GLM work), then ONE hidden background
    worker with --notify-codex. Returns a compact result only after a real
    handshake: the worker's own status.json carrying our launch token. On any
    startup failure request normal cleanup first; report uncertainty explicitly."""
    def emit(payload: dict) -> int:
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload.get("launched") else 1

    try:
        if not math.isfinite(args.launch_wait) or not 1 <= args.launch_wait <= 120:
            raise RuntimeError("--launch-wait must be between 1 and 120 seconds")
        ws = Path(args.workspace)
        task = Path(args.task)
        rd_arg = Path(args.run_dir)
        if not (ws.is_absolute() and task.is_absolute() and rd_arg.is_absolute()):
            raise RuntimeError("--workspace/--task/--run-dir must be absolute")
        ws = ws.resolve(strict=True)
        if not ws.is_dir():
            raise RuntimeError("workspace is not a directory")
        home = Path.home().resolve()
        if ws in (home, home.parent, Path(ws.anchor)):
            raise RuntimeError("refusing home/filesystem-root workspace")
        task = task.resolve(strict=True)
        if not task.is_file() or not inside(ws, task) \
                or not no_symlink_escape(ws, task):
            raise RuntimeError("task must be an existing file inside the "
                               "workspace, no symlink escapes")
        rd = resolve_contained_dir(ws, rd_arg)
        if rd.exists():
            raise RuntimeError("run-dir already exists; start requires a "
                               "fresh directory")
    except (OSError, ValueError, RuntimeError) as exc:
        return emit({"launched": False, "error": str(exc)})

    try:
        notify = load_notify_module()
        notify.read_host_env()
        server = Path(args.notify_server) if args.notify_server \
            else notify.find_server(notify.codex_home(None))
        client = notify.NotifyClient(server, node=args.notify_node or args.node or "node",
                                     timeout=20.0)
        try:
            client.start()
            client.initialize()
            client.verify_tool()
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        return emit({"launched": False,
                     "error": f"notification preflight failed: {exc}"})

    token = uuid.uuid4().hex
    try:
        rd.mkdir(parents=True)
        (rd / LAUNCHER_LOG).write_text(json.dumps({
            "launcher_pid": os.getpid(), "launch_token": token,
            "workspace": str(ws), "task": str(task), "run_dir": str(rd),
            "started": time.time()}) + "\n", encoding="utf-8")
    except OSError as exc:
        return emit({"launched": False, "error": f"cannot prepare run dir: {exc}"})

    worker = Path(__file__).resolve()
    argv = [sys.executable, "-B", str(worker)]
    if args.zcode_path:
        argv += ["--zcode-path", args.zcode_path]
    if args.node:
        argv += ["--node", args.node]
    if args.zcode_home:
        argv += ["--zcode-home", args.zcode_home]
    argv += ["run", "--workspace", str(ws), "--task", str(task),
             "--run-dir", str(rd), "--timeout", str(args.timeout),
             "--effort", args.effort, "--notify-codex",
             "--launch-token", token]
    if args.notify_server:
        argv += ["--notify-server", args.notify_server]
    if args.notify_node:
        argv += ["--notify-node", args.notify_node]

    log_path = rd / WORKER_LOG
    flags = 0
    kwargs = {}
    if os.name == "nt":
        # Hidden background launch on Windows; explicit argv, no shell.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    try:
        with open(log_path, "ab") as log:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, shell=False,
                                     cwd=str(ws), creationflags=flags, **kwargs)
    except OSError as exc:
        return emit({"launched": False, "error": f"cannot start worker: {exc}"})

    status_path = rd / STATUS_FILE
    end = time.monotonic() + max(1.0, float(args.launch_wait))
    data: dict | None = None
    while time.monotonic() < end:
        candidate = read_status_artifact(status_path)
        if candidate is not None \
                and candidate.get("launch_token") == token \
                and candidate.get("bridge_pid") == child.pid \
                and candidate.get("status") in ("running", "needs_approval", "ready_for_review"):
            data = candidate
            break
        if child.poll() is not None:
            break
        time.sleep(0.1)

    if data is None:
        # Ask the still-owned runner to use its normal ZCode tree cleanup first.
        # A forced stop is a fallback, not proof of an already-exited parent's tree.
        if child.poll() is None:
            atomic_write_json(rd / "launch-cancel.json", {"launch_token": token})
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                pass
        if child.poll() is not None:
            final = read_status_artifact(status_path) or {}
            proven = (final.get("launch_token") == token and
                      final.get("bridge_pid") == child.pid and
                      final.get("status") in ("ready_for_review", "blocked", "failed") and
                      final.get("cleanup") in ("stopped", "not-started"))
            stop = "stopped" if proven else "uncertain"
        else:
            stop = stop_owned_child(child)
        reason = ("worker did not report startup in time"
                  if child.poll() is None else
                  f"worker exited during startup (code {child.returncode}); "
                  "see worker log")
        return emit({"launched": False, "error": reason, "run_dir": str(rd),
                     "log": str(log_path), "worker_exit": child.returncode,
                     "stopped": stop})
    return emit({"launched": True, "run_dir": str(rd), "pid": child.pid,
                 "status": data.get("status"), "run_id": data.get("run_id"),
                 "status_path": str(status_path), "log": str(log_path)})


def cmd_status(args) -> int:
    """Print ONLY the compact artifact of one run directory. A missing or
    invalid artifact leaves liveness unknown; the recorded PID is checked
    separately and is never treated as durable identity."""
    rd = Path(args.run_dir)
    if not rd.is_absolute():
        print(json.dumps({"error": "--run-dir must be absolute",
                          "status": "invalid"}))
        return 2
    data = read_status_artifact(rd / STATUS_FILE)
    if data is None or data.get("schema_version") != STATUS_SCHEMA_VERSION \
            or data.get("status") not in KNOWN_STATUS:
        print(json.dumps({"run_dir": str(rd), "status": "absent",
                          "error": f"no valid {STATUS_FILE} artifact; the "
                                   "run liveness is unknown"}))
        return 1
    fields = {"schema_version", "run_id", "task_sha256", "workspace", "status",
              "timestamp", "bridge_pid", "cleanup", "notification", "launch_token"}
    data = {k: v for k, v in data.items() if k in fields}
    data["terminal"] = data["status"] in ("ready_for_review", "blocked", "failed")
    data["observed_pid_alive"] = pid_alive(data.get("bridge_pid"))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zcode_worker.py",
        description="Astra GLM-5.3-Flash one-turn ZCode worker (doctor | run)")
    p.add_argument("--zcode-path", default=None,
                   help="explicit path to zcode.cjs (must exist; no fallback)")
    p.add_argument("--node", default=None, help="node executable")
    p.add_argument("--zcode-home", default=None,
                   help="ZCode home directory (default ~/.zcode); honored by "
                        "both doctor and run")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="validate runtime and configuration (offline)")

    r = sub.add_parser("run", help="execute exactly one worker turn")
    r.add_argument("--workspace", required=True, help="absolute workspace path")
    r.add_argument("--task", required=True, help="absolute task file inside workspace")
    r.add_argument("--run-dir", required=True,
                   help="fresh absolute run dir inside workspace")
    r.add_argument("--timeout", type=int, default=900,
                   help="TOTAL run deadline in seconds")
    r.add_argument("--effort", default="high", choices=list(ALLOWED_EFFORTS),
                   help="reasoning effort: low|high|max (never medium)")
    r.add_argument("--prompt", default=None,
                   help="override dispatch prompt (testing only)")
    r.add_argument("--notify-codex", action="store_true",
                   help="opt-in: bounded Codex notifications for pending "
                        "high-risk permissions and the terminal outcome")
    r.add_argument("--notify-node", default=None,
                   help="Node executable for the desktop notification client")
    r.add_argument("--notify-server", default=None,
                   help="explicit codex-app-tools server.mjs path (default: "
                        "discovery under $CODEX_HOME or ~/.codex)")
    r.add_argument("--verbose", action="store_true",
                   help="also print ordinary (low/medium allow) permission "
                        "decisions to stdout")
    r.add_argument("--launch-token", default=None,
                   help=argparse.SUPPRESS)

    s = sub.add_parser("start",
                       help="preflight notifications and launch ONE hidden "
                            "background notified worker")
    s.add_argument("--workspace", required=True, help="absolute workspace path")
    s.add_argument("--task", required=True, help="absolute task file inside workspace")
    s.add_argument("--run-dir", required=True,
                   help="fresh absolute run dir inside workspace")
    s.add_argument("--timeout", type=int, default=900,
                   help="TOTAL run deadline in seconds for the worker")
    s.add_argument("--effort", default="high", choices=list(ALLOWED_EFFORTS),
                   help="reasoning effort: low|high|max (never medium)")
    s.add_argument("--notify-node", default=None,
                   help="Node executable for the desktop notification client")
    s.add_argument("--notify-server", default=None,
                   help="explicit codex-app-tools server.mjs path")
    s.add_argument("--launch-wait", type=float, default=LAUNCH_WAIT_DEFAULT,
                   help="bounded seconds to wait for the worker handshake")

    st = sub.add_parser("status",
                        help="print the compact live status.json of a run")
    st.add_argument("--run-dir", required=True,
                    help="absolute run directory to inspect")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "run":
        return Runner(args).run()
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "status":
        return cmd_status(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
