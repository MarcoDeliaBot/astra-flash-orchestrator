"""Offline event-wakeup tests: codex_notify MCP client, worker notification
behavior and `start`/`status`, all against SYNTHETIC processes.

The "codex-app-tools server" is a Python script launched exactly like the
production client launches the bundled server.mjs (node -> script over
newline JSON-RPC stdio; here "node" is sys.executable). The ZCode app-server
is the shared bridge fake runtime. No real notification, no network, no
inference, no account.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from bridge_fixtures import FAKE_SERVER_SOURCE, make_runtime, run_worker

HERE = Path(__file__).resolve().parent
WORKER = HERE.parent / "handoff" / "skills" / "astra-glm-orchestrator" / "scripts" / "zcode_worker.py"
NOTIFY = WORKER.parent / "codex_notify.py"

FAKE_MCP_SOURCE = '''
import sys, json, os
from pathlib import Path
record = Path(os.environ["FAKE_MCP_RECORD"])
behavior = os.environ.get("FAKE_MCP_CASE", "ok")
def emit(x):
    sys.stdout.write(json.dumps(x) + "\\n"); sys.stdout.flush()
for line in sys.stdin:
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if not isinstance(m, dict) or "id" not in m:
        continue  # notification frame (e.g. notifications/initialized)
    method = m.get("method")
    if method == "initialize":
        if behavior == "init_error":
            emit({"id": m["id"], "error": {"code": -32000, "message": "no"}}); continue
        emit({"id": m["id"], "result": {"protocolVersion": "2024-11-05",
             "serverInfo": {"name": "fake-app-tools", "version": "0"}}})
    elif method == "tools/list":
        emit({"id": m["id"], "result": {"tools": [{"name": "send_message_to_thread",
             "inputSchema": {"type": "object", "properties": {
                 "threadId": {"type": "string"}, "prompt": {"type": "string"}}}}]}})
    elif method == "tools/call":
        args = (m["params"] or {}).get("arguments") or {}
        meta = ((m["params"] or {}).get("_meta") or {}).get("openai/threadId")
        if behavior == "send_timeout":
            continue  # never answer: the client must report "uncertain"
        if behavior == "send_eof":
            sys.stdout.flush(); raise SystemExit
        if behavior == "send_error":
            emit({"id": m["id"], "result": {"isError": True, "content": [
                {"type": "text", "text": "synthetic tool error"}]}}); continue
        with open(record, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"threadId": args.get("threadId"),
                                 "prompt": args.get("prompt"),
                                 "meta_thread": meta}) + "\\n")
        receipt = {"threadId": args.get("threadId")}
        if behavior == "wrong_thread": receipt["threadId"] = "another_thread"
        if behavior == "empty_receipt": receipt = {}
        emit({"id": m["id"], "result": {"content": [{"type": "text", "text": json.dumps(receipt)}]}})
    else:
        emit({"id": m["id"], "error": {"code": -32601, "message": "unsupported"}})
'''

HOST_ENV_VARS = ("CODEX_THREAD_ID", "CODEX_APP_TOOLS_PIPE_PATH")


def make_mcp_server(base: Path, case: str = "ok") -> Path:
    base.mkdir(parents=True, exist_ok=True)
    server = base / "fake-app-tools.server.py"
    server.write_text(FAKE_MCP_SOURCE, encoding="utf-8")
    (base / "mcp-case.txt").write_text(case, encoding="utf-8")
    return server


def host_env(case: str | None = None) -> dict:
    env = os.environ.copy()
    env["CODEX_THREAD_ID"] = "thread_fake_current"
    env["CODEX_APP_TOOLS_PIPE_PATH"] = str(Path(env.get("TEMP", "/tmp")) / "fake.pipe")
    return env


def read_record(base: Path) -> list[dict]:
    path = base / "notifications-record.ndjson"
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def approval_for(run_dir: Path) -> None:
    """Write a fully-bound approval.json for the fixture high-risk request."""
    raw_input = json.dumps({"path": "synthetic.txt"}, sort_keys=True)
    identity = hashlib.sha256(json.dumps(
        {"tool": "Edit", "level": "high", "input": raw_input},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    (run_dir / "approval.json").write_text(json.dumps({
        "stable_hash": "sess_fake|turn_fake|perm_h|tool_h",
        "request_id": "perm_h", "tool_call_id": "tool_h",
        "input_hash": identity, "session_id": "sess_fake",
        "decision": "allow"}), encoding="utf-8")


def run_worker_args(ws: Path, runtime: dict, mcp: Path | None, timeout: int,
                    extra: list[str] = ()) -> list[str]:
    args = [sys.executable, "-B", str(WORKER),
            "--node", sys.executable, "--zcode-path", str(runtime["cjs"]),
            "--zcode-home", str(runtime["home"])]
    args += ["run", "--workspace", str(ws), "--task", str(ws / "TASK.md"),
             "--run-dir", str(ws / "attempt-01"), "--timeout", str(timeout)]
    args += list(extra)
    if mcp is not None:
        args += ["--notify-server", str(mcp)]
    return args


def setup_case(base: Path, case: str) -> tuple[Path, dict]:
    ws = base / case
    ws.mkdir(parents=True)
    (ws / "case.txt").write_text(case, encoding="utf-8")
    (ws / "TASK.md").write_text("Synthetic offline task", encoding="utf-8")
    return ws, make_runtime(base / ("runtime-" + case))


class NotifyClientCliTests(unittest.TestCase):
    """Actual codex_notify.py CLI paths against the fake MCP server."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, case: str, argv: list[str], env_extra: dict | None = None,
            timeout: float = 60, use_server: bool = True) -> subprocess.CompletedProcess:
        make_mcp_server(self.base / "mcp", case)
        env = host_env()
        env["FAKE_MCP_RECORD"] = str(self.base / "notifications-record.ndjson")
        env["FAKE_MCP_CASE"] = case
        env.update(env_extra or {})
        cmd = [sys.executable, "-B", str(NOTIFY)]
        if use_server:
            cmd += ["--node", sys.executable, "--server",
                    str(self.base / "mcp" / "fake-app-tools.server.py")]
        return subprocess.run(cmd + argv, env=env, capture_output=True,
                              text=True, timeout=timeout)

    def test_preflight_is_read_only_and_never_sends(self):
        result = self.cli("ok", ["preflight"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        outcome = json.loads(result.stdout)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["outcome"], "ready")
        self.assertEqual(outcome["tool"], "send_message_to_thread")
        self.assertEqual(read_record(self.base), [])  # no message was sent

    def test_preflight_fails_without_host_metadata(self):
        result = self.cli("ok", ["preflight"],
                          {"CODEX_THREAD_ID": "", "CODEX_APP_TOOLS_PIPE_PATH": ""})
        self.assertEqual(result.returncode, 1)
        outcome = json.loads(result.stdout)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["outcome"], "failed")
        self.assertIn("CODEX_THREAD_ID", outcome["error"])

    def test_preflight_fails_without_needed_tool(self):
        result = self.cli("ok", ["--codex-home", str(self.base / "empty-home"),
                                 "preflight"], use_server=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no bundled codex-app-tools server", json.loads(result.stdout)["error"])

    def test_send_delivers_to_current_thread_with_meta(self):
        result = self.cli("ok", ["send", "--text", "synthetic notification body"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["outcome"], "delivered")
        record = read_record(self.base)
        self.assertEqual(len(record), 1)
        self.assertEqual(record[0]["threadId"], "thread_fake_current")
        self.assertEqual(record[0]["meta_thread"], "thread_fake_current")
        self.assertIn("synthetic notification body", record[0]["prompt"])

    def test_send_timeout_is_uncertain_and_never_delivered(self):
        result = self.cli("send_timeout", ["--timeout", "2", "send",
                                           "--text", "synthetic"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["outcome"], "uncertain")
        self.assertEqual(read_record(self.base), [])

    def test_incomplete_or_wrong_thread_receipt_is_uncertain(self):
        for case in ("empty_receipt", "wrong_thread"):
            with self.subTest(case=case):
                result = self.cli(case, ["send", "--text", "synthetic"])
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertEqual(json.loads(result.stdout)["outcome"], "uncertain")

    def test_send_tool_error_is_failed_not_uncertain(self):
        result = self.cli("send_error", ["send", "--text", "synthetic"])
        self.assertEqual(result.returncode, 1)
        outcome = json.loads(result.stdout)
        self.assertEqual(outcome["outcome"], "failed")
        self.assertIn("synthetic tool error", outcome["error"])


class WorkerNotifyEndToEndTests(unittest.TestCase):
    """Real worker CLI against the fake app-server plus the fake MCP server."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.mcp = make_mcp_server(self.base / "mcp")
        self.record = self.base / "notifications-record.ndjson"

    def tearDown(self):
        self.temp.cleanup()

    def launch(self, case: str, timeout: int = 25, env_extra: dict | None = None,
               extra: list[str] = ()) -> tuple[subprocess.Popen, Path, Path, dict]:
        ws, runtime = setup_case(self.base, case)
        env = host_env()
        env["FAKE_MCP_RECORD"] = str(self.record)
        env["FAKE_MCP_CASE"] = env_extra.pop("FAKE_MCP_CASE", "ok") \
            if env_extra else "ok"
        env.update(env_extra or {})
        proc = subprocess.Popen(run_worker_args(ws, runtime, self.mcp, timeout, extra),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        return proc, ws, ws / "attempt-01", runtime

    def communicate_or_die(self, proc: subprocess.Popen) -> str:
        out, _ = proc.communicate(timeout=90)
        self.assertIsNotNone(out)
        return out

    def test_ordinary_progress_is_quiet_and_sends_only_terminal(self):
        proc, ws, rd, _ = self.launch("high", extra=["--notify-codex"])
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 0, out)
        # No permission/progress wake; exactly one terminal notification.
        self.assertNotIn("notify: permission", out)
        self.assertNotIn("permission decisions", out.split("notify:")[0])
        record = read_record(self.base)
        self.assertEqual(len(record), 1)
        self.assertIn("event_type: terminal", record[0]["prompt"])
        ledger = json.loads((rd / "notifications.json").read_text(encoding="utf-8"))
        self.assertEqual([e["event_type"] for e in ledger["entries"]],
                         ["terminal"])
        status = json.loads((rd / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "ready_for_review")
        # The one required terminal notification is what status reports.
        self.assertEqual(status["notification"], "delivered")
        for field in ("run_id", "task_sha256", "workspace", "timestamp",
                      "bridge_pid", "cleanup"):
            self.assertIn(field, status)

    def test_permission_notified_once_after_pending_file_then_terminal(self):
        proc, ws, rd, _ = self.launch("high_perm", extra=["--notify-codex"])
        pending = rd / "pending-permission.json"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not pending.is_file():
            self.assertIsNone(proc.poll(), "worker exited before pending file")
            time.sleep(0.1)
        self.assertTrue(pending.is_file(), "notify must follow the pending file")
        # The file must precede the message; wait for the latter separately.
        # Existence of the earlier file cannot imply that sending has finished.
        record = read_record(self.base)
        while time.monotonic() < deadline and not record:
            self.assertIsNone(proc.poll(), "worker exited before notification")
            time.sleep(0.05)
            record = read_record(self.base)
        self.assertTrue(any("event_type: permission" in r["prompt"] for r in record))
        self.assertTrue(any(str(pending) in r["prompt"] for r in record))
        approval_for(rd)
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertFalse(pending.exists(), "resolved request must be cleared")
        ledger = json.loads((rd / "notifications.json").read_text(encoding="utf-8"))
        by_type = {e["event_type"]: e["status"] for e in ledger["entries"]}
        self.assertEqual(by_type, {"permission": "delivered",
                                   "terminal": "delivered"})
        record = read_record(self.base)
        self.assertEqual(len(record), 2)
        self.assertIn("event_type: terminal", record[-1]["prompt"])
        self.assertIn("ready_for_review", record[-1]["prompt"])
        status = json.loads((rd / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "ready_for_review")
        self.assertEqual(status["notification"], "delivered")

    def test_uncertain_send_stays_visible_and_never_fails_the_run(self):
        proc, ws, rd, _ = self.launch("high_perm",
                                      env_extra={"FAKE_MCP_CASE": "send_timeout"},
                                      extra=["--notify-codex"])
        pending = rd / "pending-permission.json"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not pending.is_file():
            self.assertIsNone(proc.poll())
            time.sleep(0.1)
        self.assertTrue(pending.is_file())
        approval_for(rd)
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 0, out)
        ledger = json.loads((rd / "notifications.json").read_text(encoding="utf-8"))
        by_type = {e["event_type"]: e["status"] for e in ledger["entries"]}
        self.assertEqual(by_type, {"permission": "uncertain",
                                   "terminal": "uncertain"})
        status = json.loads((rd / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["notification"], "uncertain")
        self.assertEqual(status["status"], "ready_for_review")
        self.assertEqual(read_record(self.base), [])  # nothing was delivered

    def test_missing_host_metadata_fails_before_spawn(self):
        out = None
        proc, ws, rd, _ = self.launch(
            "high", env_extra={"CODEX_THREAD_ID": ""},
            extra=["--notify-codex"])
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("CODEX_THREAD_ID", out)
        self.assertFalse((ws / "spawned.marker").exists(),
                         "no paid GLM work may start after preflight failure")
        self.assertEqual(read_record(self.base), [])

    def test_foreground_run_without_notify_never_touches_notifications(self):
        proc, ws, rd, _ = self.launch("high", env_extra={
            "CODEX_THREAD_ID": "", "CODEX_APP_TOOLS_PIPE_PATH": ""})
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 0, out)
        self.assertFalse((rd / "notifications.json").exists())
        self.assertEqual(read_record(self.base), [])

    def test_second_writer_lock_is_refused(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("zw_lock", WORKER)
        zw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zw)
        ws, runtime = setup_case(self.base, "lockcase")
        lock_dir = ws / ".ai"
        lock_dir.mkdir()
        (lock_dir / "astra-glm.lock").write_text(json.dumps(
            {"pid": 1, "run_dir": "someone-else", "token": "foreign"}),
            encoding="utf-8")
        env = host_env()
        env["FAKE_MCP_RECORD"] = str(self.record)
        proc = subprocess.Popen(
            run_worker_args(ws, runtime, self.mcp, 25, ["--notify-codex"]),
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = self.communicate_or_die(proc)
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("workspace locked", out)
        self.assertFalse((ws / "spawned.marker").exists())

    def test_ledger_dedupe_skips_delivered_and_uncertain_events(self):
        """Same event_id is never retransmitted, whatever its recorded state."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("zw_dedupe", WORKER)
        zw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zw)
        notify = zw.load_notify_module()

        sent = []

        class StubClient:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def __getattr__(self, name):
                return lambda *a, **k: None
            def send(self, thread_id, text):
                sent.append(text)

        ws, runtime = setup_case(self.base, "dedupe")
        args = zw.build_parser().parse_args(
            ["--node", sys.executable, "--zcode-path", str(runtime["cjs"]),
             "--zcode-home", str(runtime["home"]), "run",
             "--workspace", str(ws), "--task", str(ws / "TASK.md"),
             "--run-dir", str(ws / "attempt-01"), "--timeout", "60",
             "--notify-codex"])
        runner = zw.Runner(args)
        runner.workspace = ws
        runner.run_dir = ws / "attempt-01"
        (ws / "attempt-01").mkdir()
        runner.notify = notify
        identity = {"stable_hash": "h", "input_hash": "i"}
        with patch.object(notify, "NotifyClient", StubClient):
            runner.notify_event("permission", identity, "evidence.md")
            runner.notify_event("permission", identity, "evidence.md")
        self.assertEqual(len(sent), 1)
        # A pending or uncertain outcome also blocks automatic retransmission.
        runner.notify = notify
        runner.ledger = None
        ledger = json.loads((ws / "attempt-01" / "notifications.json")
                            .read_text(encoding="utf-8"))
        ledger["entries"][0]["status"] = "uncertain"
        (ws / "attempt-01" / "notifications.json").write_text(
            json.dumps(ledger), encoding="utf-8")
        with patch.object(notify, "NotifyClient", StubClient):
            runner.notify_event("permission", identity, "evidence.md")
        self.assertEqual(len(sent), 1)

        with patch.object(notify, "NotifyClient", StubClient), patch.object(
                runner, "save_ledger", side_effect=OSError("synthetic disk failure")):
            runner.notify_event("permission", {"stable_hash": "new"}, "evidence.md")
        self.assertEqual(len(sent), 1, "no send without a durable pending entry")
        self.assertEqual(runner.notification, "failed")
        (runner.run_dir / "notifications.json").write_text("{", encoding="utf-8")
        runner.ledger = None
        with patch.object(notify, "NotifyClient", StubClient):
            runner.notify_event("permission", {"stable_hash": "again"}, "evidence.md")
        self.assertEqual(len(sent), 1, "corrupt ledger must not permit a resend")


class StartStatusTests(unittest.TestCase):
    """`start` handshake, owned-process cleanup and `status` on real CLI paths."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.mcp = make_mcp_server(self.base / "mcp")
        self.record = self.base / "notifications-record.ndjson"

    def tearDown(self):
        self.temp.cleanup()

    def start_cli(self, case: str, launch_wait: float,
                  env_extra: dict | None = None,
                  prepare=None) -> subprocess.CompletedProcess:
        ws, runtime = setup_case(self.base, case)
        if case == "startok":
            source = runtime["cjs"].read_text(encoding="utf-8")
            runtime["cjs"].write_text(source.replace("def complete():\n", "def complete():\n __import__('time').sleep(1)\n"), encoding="utf-8")
        if case == "slowstart":
            source = runtime["cjs"].read_text(encoding="utf-8")
            runtime["cjs"].write_text(source.replace("if sys.argv[-1]=='version':\n", "if sys.argv[-1]=='version':\n __import__('time').sleep(4)\n"), encoding="utf-8")
        if prepare is not None:
            prepare(ws)
        env = host_env()
        env["FAKE_MCP_RECORD"] = str(self.record)
        env.update(env_extra or {})
        args = [sys.executable, "-B", str(WORKER),
                "--node", sys.executable, "--zcode-path", str(runtime["cjs"]),
                "--zcode-home", str(runtime["home"]),
                "start", "--workspace", str(ws), "--task", str(ws / "TASK.md"),
                "--run-dir", str(ws / "attempt-01"), "--timeout", "30",
                "--launch-wait", str(launch_wait),
                "--notify-server", str(self.mcp)]
        return subprocess.run(args, env=env, capture_output=True, text=True,
                              timeout=120)

    def status_cli(self, rd: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-B", str(WORKER), "status",
                               "--run-dir", str(rd)],
                              capture_output=True, text=True, timeout=30)

    def test_start_handshake_status_and_terminal_notification(self):
        result = self.start_cli("startok", launch_wait=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["launched"], payload)
        rd = Path(payload["run_dir"])
        self.assertIn(payload["status"], ("running", "needs_approval", "ready_for_review"))
        self.assertTrue((rd / "status.json").is_file())
        self.assertTrue(payload["log"] and Path(payload["log"]).is_file())
        # Wait for the background worker's terminal state.
        deadline = time.monotonic() + 60
        terminal = None
        while time.monotonic() < deadline:
            data = json.loads((rd / "status.json").read_text(encoding="utf-8"))
            if data["status"] in ("ready_for_review", "blocked", "failed") and data["notification"] == "delivered":
                terminal = data
                break
            time.sleep(0.3)
        self.assertIsNotNone(terminal, "worker never reached a terminal status")
        self.assertEqual(terminal["status"], "ready_for_review")
        record = read_record(self.base)
        self.assertTrue(any("event_type: terminal" in r["prompt"] for r in record))
        status_result = self.status_cli(rd)
        self.assertEqual(status_result.returncode, 0)
        shown = json.loads(status_result.stdout)
        self.assertTrue(shown["terminal"])
        self.assertEqual(shown["cleanup"], "stopped")
        self.assertEqual(shown["notification"], "delivered")
        self.assertFalse((rd.parent / ".ai" / "astra-glm.lock").exists())

    def test_missing_status_leaves_liveness_unknown(self):
        result = self.status_cli(self.base / "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("liveness is unknown", json.loads(result.stdout)["error"])

    def test_start_timeout_cancels_before_zcode_spawn(self):
        result = self.start_cli("slowstart", launch_wait=1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["stopped"], "stopped")
        ws = self.base / "slowstart"
        self.assertFalse((ws / "spawned.marker").exists())
        self.assertFalse((ws / ".ai" / "astra-glm.lock").exists())
        state = json.loads((ws / "attempt-01" / "state.json").read_text(encoding="utf-8"))
        self.assertIn("cancelled startup", state["detail"])

    def test_launch_token_must_match_recorded_metadata(self):
        ws, runtime = setup_case(self.base, "badtoken")
        rd = ws / "attempt-01"
        rd.mkdir()
        launch = {"launch_token": "original", "workspace": str(ws),
                  "task": str(ws / "TASK.md"), "run_dir": str(rd)}
        (rd / "start.log").write_text(json.dumps(launch), encoding="utf-8")
        proc = subprocess.run(run_worker_args(ws, runtime, None, 10,
                              ["--launch-token", "different"]),
                              capture_output=True, text=True, timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("launch identity mismatch", proc.stdout)
        self.assertFalse((ws / "spawned.marker").exists())
        self.assertEqual(json.loads((rd / "start.log").read_text(encoding="utf-8")), launch)

    def test_start_refuses_existing_run_dir_and_stale_status(self):
        result = self.start_cli(
            "startfresh", launch_wait=5,
            prepare=lambda ws: (ws / "attempt-01").mkdir())
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["launched"])
        self.assertIn("already exists", payload["error"])

    def test_start_preflight_failure_never_spawns_a_worker(self):
        result = self.start_cli("startnoenv", launch_wait=5,
                                env_extra={"CODEX_THREAD_ID": ""})
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["launched"])
        self.assertIn("notification preflight failed", payload["error"])

    def test_start_worker_exit_during_handshake_stops_owned_child(self):
        # A foreign workspace lock makes the spawned child fail fast, before
        # any status.json handshake: start must account for it and stop it.
        def place_foreign_lock(ws: Path) -> None:
            (ws / ".ai").mkdir()
            (ws / ".ai" / "astra-glm.lock").write_text(json.dumps(
                {"pid": 1, "run_dir": "someone-else", "token": "foreign"}),
                encoding="utf-8")

        result = self.start_cli("startdead", launch_wait=20,
                                prepare=place_foreign_lock)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["launched"])
        self.assertIn("worker exited during startup", payload["error"])
        self.assertEqual(payload.get("stopped"), "stopped")


class InstallerHelperTests(unittest.TestCase):
    """The installer ships and undoes BOTH helpers (worker + notify)."""

    def test_notify_helper_installed_and_undoable(self):
        import install
        import install_zcode as zcode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            config = root / ".zcode"
            config.mkdir()
            with patch.dict("os.environ",
                            {"CODEX_HOME": str(root / ".codex")}):
                changes = zcode.plan_changes(root, config)
                targets = {c["path"].name for c in changes}
                self.assertIn("codex_notify.py", targets)
                self.assertIn("zcode_worker.py", targets)
                receipt = install.apply_changes(changes, config, {})
                helper = (root / ".codex" / "skills" / zcode.ORCHESTRATOR
                          / "scripts" / "codex_notify.py")
                self.assertTrue(helper.is_file())
                self.assertEqual(
                    helper.read_bytes(),
                    NOTIFY.read_bytes().replace(b"\r\n", b"\n"))
                self.assertEqual(subprocess.run(
                    [sys.executable, "-B", str(ROOT_INSTALL), "--check",
                     "--home", str(root), "--zcode-home", str(config)],
                    capture_output=True, timeout=60).returncode, 0)
                install.apply_changes(
                    zcode.plan_changes(root, config), config, {})  # idempotent
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT_INSTALL), "--undo",
                     str(receipt), "--apply", "--home", str(root),
                     "--zcode-home", str(config)],
                    capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(helper.exists())


ROOT_INSTALL = HERE.parent / "install_zcode.py"

if __name__ == "__main__":
    unittest.main()
