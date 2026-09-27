"""Offline bridge tests for handoff/skills/astra-glm-orchestrator/scripts/zcode_worker.py.

Everything here is synthetic and isolated: fake config homes, a scripted fake
app-server transport (NDJSON-frame-shaped) plus real-subprocess end-to-end
cases against a Python fake runtime (bridge_fixtures). No live inference, no
reads of the real ~/.zcode, no Node install, no network. The suite is
Python-stdlib-only and keeps real exit statuses.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "handoff" / "skills" / "astra-glm-orchestrator" / "scripts" / "zcode_worker.py"
spec = importlib.util.spec_from_file_location("zcode_worker", SCRIPT)
zw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zw)

import bridge_fixtures as fx

SECRET = "SK-FAKE-KEY-0123456789abcdefSECRET"
SESSION_ID = "sess_fake_1"
TURN_ID = "turn_fake_1"
PROVIDER = zw.PROVIDER_ID
MODEL = zw.MODEL_ID


# ---------------------------------------------------------------- fixtures

def make_home(tmp: Path, *, api_key: str = SECRET, kind: str = "anthropic",
              enabled: bool = True, base: str = zw.BASE_URL) -> Path:
    home = tmp / "zcode-home"
    (home / "v2").mkdir(parents=True)
    (home / "v2" / "config.json").write_text(json.dumps({
        "provider": {zw.CREDENTIAL_PROVIDER_ID: {
            "enabled": enabled, "kind": kind,
            "models": [MODEL],
            "options": {"apiKey": api_key, "baseURL": base},
        }}}), encoding="utf-8")
    (home / "v2" / "provider_config.json").write_text("{}", encoding="utf-8")
    return home


def make_builtin(directory: Path, *, revision: int = 7,
                 endpoint: str = zw.BASE_URL, access: dict | None = None,
                 api: dict | None = None, model_enabled: bool = True) -> Path:
    """Valid builtin config at the verified runtime layout
    <runtime root>/config/provider/zcode-builtin.json."""
    if access is None:
        access = {"type": "zhipu-account", "mode": zw.ACCOUNT_MODE,
                  "accountType": "zai"}
    if api is None:
        api = {"type": "anthropic-messages", "baseUrl": endpoint}
    p = directory / "zcode-builtin.json"
    p.write_text(json.dumps({
        "revision": revision,
        "config": {"providerConfigRules": {"providerRules": [{
            "providerId": PROVIDER,
            "config": {
                "builtinModelIds": [MODEL],
                "access": access,
                "api": api,
            }}]},
            "modelConfigRules": {
                "builtinProviderModelRules": [{
                    "providerId": PROVIDER, "modelId": MODEL,
                    "config": {"enabled": model_enabled}}],
                "modelRules": [{
                    "modelId": MODEL,
                    "config": {"reasoning": {"enabled": True,
                                             "defaultVariant": "max"}}}]},
        }}), encoding="utf-8")
    return p


def creation_result(workspace: Path, *, effort: str = "high",
                    session_model_options: dict | None = "default",
                    current: dict | None = "default",
                    levels: list | None = None,
                    session_id: str = SESSION_ID,
                    pending: list | None = "default") -> dict:
    levels = levels if levels is not None else [
        {"value": "low"}, {"value": "high"}, {"value": "max"}]
    smodel = {"providerId": PROVIDER, "modelId": MODEL}
    if session_model_options == "default":
        session_model_options = None
    if session_model_options:
        smodel["options"] = session_model_options
    if current == "default":
        current = {"providerId": PROVIDER, "modelId": MODEL,
                   "options": {"reasoningLevel": effort}}
    if pending == "default":
        pending = []
    return {
        "session": {
            "sessionId": session_id,
            "workspace": {"workspacePath": str(workspace),
                          "workspaceKey": str(workspace)},
            "model": smodel,
            "status": "idle",
        },
        "settings": {"model": {
            "current": current,
            "available": [{"ref": {"providerId": PROVIDER, "modelId": MODEL},
                           "reasoning": {"levels": levels}}],
        }},
        "runtime": {"pendingRequestIds": pending},
    }


def settled_result(workspace: Path, *, effort: str = "high", status: str = "idle",
                   pending: list | None = "default",
                   session_id: str = SESSION_ID,
                   current: dict | None = "default") -> dict:
    if current == "default":
        current = {"providerId": PROVIDER, "modelId": MODEL,
                   "options": {"reasoningLevel": effort}}
    result = {
        "session": {
            "sessionId": session_id, "status": status,
            "workspace": {"workspacePath": str(workspace),
                          "workspaceKey": str(workspace)},
            "model": {"providerId": PROVIDER, "modelId": MODEL},
        },
        "settings": {"model": {"current": current}},
    }
    result["runtime"] = {"pendingRequestIds": [] if pending == "default" else pending}
    return result


def event_frame(kind: str, payload: dict, *, session: str = SESSION_ID,
                turn: str = TURN_ID) -> dict:
    """Real wire shape: identifiers live on the event params (outer object)."""
    return {"method": "session/event",
            "params": {"sessionId": session, "turnId": turn,
                       "type": kind, "payload": payload}}


class FakeTransport:
    """Scripted app-server. The runner's poll() drives serve(); scripts append
    NDJSON-shaped frames. Runner outgoing frames are recorded for asserts."""

    def __init__(self, script=None, stop_result: str = "stopped"):
        self.incoming: list[dict] = []
        self.sent: list[dict] = []        # {"id","method","params"}
        self.replies: list[dict] = []     # {"id","result"} or {"id","error"}
        self.protocol_error = None
        self.script = script or happy_script()
        self.stop_result = stop_result
        self.stopped = False
        self._served = 0

    # runner-facing interface (mirrors zw.Transport)
    def send(self, msg_id, method, params):
        self.sent.append({"id": msg_id, "method": method, "params": params})

    def reply(self, msg_id, result):
        self.replies.append({"id": msg_id, "result": result})

    def reply_error(self, msg_id, code, message):
        self.replies.append({"id": msg_id,
                             "error": {"code": code, "message": message}})

    def poll(self, timeout):
        # Serve exactly once per new outgoing message so scripts never loop.
        while self._served < len(self.sent):
            self.script(self)
            self._served += 1
        if self.incoming:
            return self.incoming.pop(0)
        time.sleep(min(0.05, max(0.01, timeout)))
        return None

    def stop(self):
        self.stopped = True
        return self.stop_result

    # script helpers
    def push(self, *frames):
        self.incoming.extend(frames)

    def methods(self):
        return [m["method"] for m in self.sent]


def happy_script(workspace: Path | None = None, effort: str = "high",
                 with_headers_check: bool = True, **creation_kw):
    """Default script: account sync ok, create ok, subscribe ok, completion
    BEFORE the send ack (early order), headers check, settled read, stop."""

    def script(tr: FakeTransport):
        last = tr.sent[-1] if tr.sent else None
        if last is None:
            return
        rid, method, params = last["id"], last["method"], last["params"]
        done = {m["method"] for m in tr.sent}
        if method == "provider/updateAccountConfig":
            tr.push({"id": rid, "result": {"ok": True}})
        elif method == "session/create":
            tr.push({"id": rid,
                     "result": creation_result(workspace or Path.cwd(),
                                               effort=effort, **creation_kw)})
        elif method == "session/subscribe":
            tr.push({"id": rid, "result": {"ok": True}})
        elif method == "session/send":
            # Completion arrives BEFORE the send ack.
            tr.push(
                event_frame("turn.started", {"sessionId": SESSION_ID,
                                             "turnId": TURN_ID}),
                event_frame("turn.completed", {
                    "sessionId": SESSION_ID, "turnId": TURN_ID,
                    "resultType": "success",
                    "usage": {"input_tokens": 10, "output_tokens": 5}}),
                {"id": rid, "result": {"accepted": True, "sessionId": SESSION_ID}})
            if with_headers_check and "interaction_headers" not in done:
                done.add("interaction_headers")
                tr.push({"id": 9000, "method":
                         "interaction/requestProviderRuntimeHeaders",
                         "params": {"providerId": PROVIDER,
                                    "sessionId": SESSION_ID,
                                    "reason": "model-request",
                                    "modelSelection": {"providerId": PROVIDER,
                                                       "modelId": MODEL},
                                    "workspace": {
                                        "workspacePath": str(workspace),
                                        "workspaceKey": str(workspace)}}})
        elif method == "session/read":
            tr.push({"id": rid,
                     "result": settled_result(workspace or Path.cwd(),
                                              effort=effort)})
        elif method == "session/stop":
            tr.push({"id": rid, "result": {"ok": True}})
        else:
            tr.push({"id": rid, "result": {"ok": True}})
    return script


# ---------------------------------------------------------------- harness

def make_runner(tmp: Path, workspace: Path, task: Path, run_dir: Path, *,
                effort: str = "high", timeout: int = 30,
                home: Path | None = None, prompt: str | None = None):
    args = argparse.Namespace(
        cmd="run", workspace=str(workspace), task=str(task),
        run_dir=str(run_dir), timeout=timeout, effort=effort,
        zcode_path=str(tmp / "resources" / "glm" / "zcode.cjs"),
        node="node",
        zcode_home=str(home or (tmp / "zcode-home")),
        prompt=prompt)
    return zw.Runner(args)


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.home = make_home(self.tmp)
        self.ws = self.tmp / "ws"
        (self.ws / "pkg").mkdir(parents=True)
        (self.ws / ".git").mkdir()
        # Synthetic offline runtime entrypoint + builtin config at the real
        # layout, so discovery stays inside the tmp tree (never the real
        # installation).
        cjs_dir = self.tmp / "resources" / "glm"
        cjs_dir.mkdir(parents=True)
        self.cjs = cjs_dir / "zcode.cjs"
        self.cjs.write_text("print('fake cjs')\n", encoding="utf-8")
        builtin_dir = self.tmp / "resources" / "config" / "provider"
        builtin_dir.mkdir(parents=True)
        make_builtin(builtin_dir)
        self.task = self.ws / "TASK.md"
        self.task.write_text("do the fake thing", encoding="utf-8")
        self.run_dir = self.ws / "runs" / "r1"

    def tearDown(self):
        self._tmp.cleanup()

    def run_worker(self, tr: FakeTransport, *, effort="high", timeout=30,
                   run_dir: Path | None = None, home: Path | None = None,
                   workspace: Path | None = None, task: Path | None = None,
                   mock_version=True):
        runner = make_runner(self.tmp, workspace or self.ws,
                             task or self.task, run_dir or self.run_dir,
                             effort=effort, timeout=timeout, home=home)
        # The shared preflight still runs for real (synthetic builtin config,
        # synthetic credential) but the version gate is mocked so the suite
        # stays stdlib-only; a dedicated test exercises the real gate.
        recorded = {"version_calls": 0}
        orig_version = zw.runtime_version

        def fake_version(node, cjs, timeout=zw.VERSION_SUBPROC_TIMEOUT):
            recorded["version_calls"] += 1
            self.assertLessEqual(timeout, zw.VERSION_SUBPROC_TIMEOUT)
            return zw.RUNTIME_VERSION
        if mock_version:
            zw.runtime_version = fake_version
        runner.spawn = lambda: tr   # FakeTransport instead of a real child
        try:
            code = runner.run()
        finally:
            zw.runtime_version = orig_version
        runner.test_version_calls = recorded["version_calls"]
        return code, runner

    # ------------------------------------------------------ happy path

    def test_happy_path_early_completion_before_ack(self):
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 0, runner.blocked_reason)
        self.assertEqual(runner.cleanup_status, "stopped")
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(state["schema_version"], zw.SCHEMA_VERSION)
        self.assertEqual(state["usage"], {"input_tokens": 10,
                                          "output_tokens": 5})
        self.assertTrue(state["task_sha256"])
        lines = (self.run_dir / "events.ndjson").read_text().splitlines()
        self.assertTrue(lines)
        for ln in lines:                       # every line valid JSON
            json.loads(ln)
        types = [json.loads(ln).get("type") for ln in lines]
        self.assertIn("turn.completed", types)
        result_md = (self.run_dir / "result.md").read_text()
        self.assertIn("ready_for_review", result_md)
        # lock released
        self.assertFalse((self.ws / zw.LOCK_NAME).exists())

    def test_late_completion_after_ack_succeeds(self):
        """Completion AFTER the send ack (the other wire order) must also
        reach ready_for_review."""
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    event_frame("turn.started", {}),
                    {"id": last["id"], "result": {"accepted": True,
                                                  "sessionId": SESSION_ID}},
                    event_frame("turn.completed", {"resultType": "success",
                                                   "usage": {"n": 1}}))
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 0, runner.blocked_reason)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "ready_for_review")

    def test_send_scope_and_preflight_ran(self):
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 0)
        self.assertEqual(runner.test_version_calls, 1)
        create = next(m for m in tr.sent if m["method"] == "session/create")
        self.assertEqual(create["params"]["model"]["providerId"], PROVIDER)
        self.assertEqual(create["params"]["model"]["modelId"], MODEL)
        self.assertEqual(
            create["params"]["model"]["options"]["reasoningLevel"], "high")
        self.assertEqual(create["params"]["mode"], "build")
        for tool in ("Agent", "Task", "CronCreate"):
            self.assertIn(tool, create["params"]["toolDenylist"])
        send = next(m for m in tr.sent if m["method"] == "session/send")
        self.assertEqual(send["params"]["sessionId"], SESSION_ID)
        self.assertEqual(
            send["params"]["modelSelection"]["options"]["reasoningLevel"],
            "high")
        # scoped headers got the key; scope mismatch would not
        hdr_reply = next(r for r in tr.replies if "result" in r
                         and isinstance(r["result"], dict)
                         and "headersApplied" in r["result"])
        self.assertTrue(hdr_reply["result"]["headersApplied"])

    def test_builtin_revision_uses_integer_plus_path_hash(self):
        tr = FakeTransport(happy_script(self.ws))
        self.run_worker(tr)
        sync = next(m for m in tr.sent
                    if m["method"] == "provider/updateAccountConfig")
        based = sync["params"]["basedOnZCodeBuiltinRevision"]
        self.assertTrue(based.startswith("zcode-builtin:7:"),
                        f"revision must come from the validated config: {based}")
        self.assertIn(zw.sha256_text(str(zw.builtin_config_path(
            self.tmp / "resources" / "glm" / "zcode.cjs").resolve())), based)

    def test_preflight_rejects_unofficial_endpoint_before_spawn(self):
        builtin_dir = self.tmp / "resources" / "config" / "provider"
        make_builtin(builtin_dir, endpoint="https://wrong.invalid")
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertEqual(tr.sent, [], "no RPC may happen on a bad builtin")
        self.assertEqual(runner.cleanup_status, "not-started")
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("official endpoint", state["detail"])

    def test_preflight_rejects_revision_zero_or_missing(self):
        for bad in (0, None, "x"):
            builtin_dir = self.tmp / "resources" / "config" / "provider"
            make_builtin(builtin_dir, revision=bad)
            tr = FakeTransport(happy_script(self.ws))
            rd = self.ws / ("revision-" + str(bad))
            code, runner = self.run_worker(tr, run_dir=rd)
            self.assertEqual(code, 1)
            self.assertEqual(tr.sent, [])
            state = json.loads((rd / "state.json").read_text())
            self.assertIn("revision", state["detail"])

    def test_preflight_rejects_wrong_access_and_api_type(self):
        builtin_dir = self.tmp / "resources" / "config" / "provider"
        make_builtin(builtin_dir, access={"type": "zhipu-account",
                                          "mode": zw.ACCOUNT_MODE,
                                          "accountType": "individual"})
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertEqual(tr.sent, [])
        make_builtin(builtin_dir, api={"type": "anthropic",
                                       "baseUrl": zw.BASE_URL})
        tr = FakeTransport(happy_script(self.ws))
        code, _ = self.run_worker(tr)
        self.assertEqual(code, 1)

    def test_preflight_rejects_disabled_model_rule(self):
        builtin_dir = self.tmp / "resources" / "config" / "provider"
        make_builtin(builtin_dir, model_enabled=False)
        tr = FakeTransport(happy_script(self.ws))
        code, _ = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertEqual(tr.sent, [])

    # ------------------------------------------------------ snapshot checks

    def test_wrong_effort_in_settings_blocks_send(self):
        tr = FakeTransport(happy_script(
            self.ws, current={"providerId": PROVIDER, "modelId": MODEL,
                              "options": {"reasoningLevel": "low"}}))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertNotIn("session/send", tr.methods())
        self.assertIn("session/create", tr.methods())

    def test_session_model_with_options_rejected(self):
        tr = FakeTransport(happy_script(self.ws,
                                        session_model_options={"reasoningLevel": "high"}))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertNotIn("session/send", tr.methods())

    def test_catalog_missing_effort_fails_closed(self):
        tr = FakeTransport(happy_script(self.ws, levels=[
            {"value": "low"}, {"value": "max"}]))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertNotIn("session/send", tr.methods())

    def test_returned_workspace_mismatch_fails(self):
        tr = FakeTransport(happy_script(workspace=Path("C:/somewhere/else")))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)

    def test_creation_missing_pending_ids_fails(self):
        tr = FakeTransport(happy_script(self.ws, pending=None))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertNotIn("session/send", tr.methods())

    def test_settled_pending_requests_fail(self):
        base = happy_script(self.ws)

        def script(t):
            before = len(t.incoming)
            base(t)
            if t.sent and t.sent[-1]["method"] == "session/read":
                for i, f in enumerate(t.incoming[before:], start=before):
                    if f.get("id") and "result" in f and \
                            isinstance(f["result"], dict) and "session" in f["result"]:
                        t.incoming[i] = {
                            "id": f["id"],
                            "result": settled_result(self.ws, pending=["p1"])}
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("pending", state["detail"].lower())

    def test_settled_missing_pending_ids_is_not_empty(self):
        base = happy_script(self.ws)

        def script(t):
            before = len(t.incoming)
            base(t)
            if t.sent and t.sent[-1]["method"] == "session/read":
                for i, f in enumerate(t.incoming[before:], start=before):
                    if f.get("id") and "result" in f and \
                            isinstance(f["result"], dict) and "session" in f["result"]:
                        t.incoming[i] = {
                            "id": f["id"],
                            "result": settled_result(self.ws, pending=None)}
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("pendingRequestIds", state["detail"])

    def test_settled_different_session_fails(self):
        base = happy_script(self.ws)

        def script(t):
            before = len(t.incoming)
            base(t)
            if t.sent and t.sent[-1]["method"] == "session/read":
                for i, f in enumerate(t.incoming[before:], start=before):
                    if f.get("id") and "result" in f and \
                            isinstance(f["result"], dict) and "session" in f["result"]:
                        t.incoming[i] = {
                            "id": f["id"],
                            "result": settled_result(self.ws,
                                                     session_id="sess_OTHER")}
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("different session", state["detail"])

    # ------------------------------------------------------ version gate

    def test_runtime_version_gate_real_subprocess(self):
        """The real gate runs [node, cjs, version]; here node is
        sys.executable and the cjs is a tiny Python program (stdlib only)."""
        good = self.tmp / "good_version.py"
        good.write_text(f"print('{zw.RUNTIME_VERSION}')\n", encoding="utf-8")
        self.assertEqual(zw.runtime_version(sys.executable, good, timeout=15),
                         zw.RUNTIME_VERSION)
        bad = self.tmp / "bad_version.py"
        bad.write_text("print('0.17.0')\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            zw.runtime_version(sys.executable, bad, timeout=15)
        slow = self.tmp / "slow_version.py"
        slow.write_text("import time; time.sleep(5)\n", encoding="utf-8")
        with self.assertRaises(subprocess.TimeoutExpired):
            zw.runtime_version(sys.executable, slow, timeout=1)

    def test_version_gate_failure_blocks_start(self):
        orig = zw.runtime_version

        def bad_version(node, cjs, timeout=20):
            raise RuntimeError("runtime version '0.17.0' != required exact "
                               "'0.16.9'")
        zw.runtime_version = bad_version
        try:
            tr = FakeTransport(happy_script(self.ws))
            code, runner = self.run_worker(tr, mock_version=False)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertIn("0.17.0", state["detail"])
            self.assertNotIn("session/create", tr.methods())
        finally:
            zw.runtime_version = orig

    # ------------------------------------------------------ turn outcomes

    def test_turn_failed_is_failed(self):
        base = happy_script(self.ws)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    event_frame("turn.failed",
                                {"sessionId": SESSION_ID, "turnId": TURN_ID,
                                 "error": "model exploded"}),
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertIn("turn.failed", state["detail"])

    def test_event_missing_session_or_turn_is_a_failure(self):
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"method": "session/event",
                     "params": {"type": "turn.completed",
                                "payload": {"resultType": "success"}}},
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertIn("missing sessionId/turnId", state["detail"])

    def test_eof_mid_turn_is_failure(self):
        base = happy_script(self.ws)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push({"eof": True})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertIn("EOF", state["detail"])

    def test_timeout_is_failure_and_reported(self):
        def script(t):
            pass  # never responds
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr, timeout=1)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertIn("timeout", state["detail"].lower())

    def test_send_ack_not_accepted_fails(self):
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    event_frame("turn.started", {}),
                    {"id": last["id"], "result": {"accepted": False}})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("not accepted", state["detail"])

    def test_send_ack_different_session_fails(self):
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    event_frame("turn.started", {}),
                    {"id": last["id"], "result": {"accepted": True,
                                                  "sessionId": "sess_EVIL"}})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("different session", state["detail"])

    def test_terminal_event_for_unrelated_session_rejected(self):
        base = happy_script(self.ws)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    event_frame("turn.started", {}),
                    {"method": "session/event",
                     "params": {"sessionId": "sess_OTHER",
                                "turnId": "turn_other",
                                "type": "turn.completed",
                                "payload": {"resultType": "success"}}},
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}})
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr, timeout=5)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "failed")
        self.assertIn("unrelated", state["detail"])

    # ------------------------------------------------------ transport bounds

    def test_transport_overflow_fails_closed(self):
        tr = zw.Transport.__new__(zw.Transport)
        tr.q = __import__("queue").Queue(maxsize=1)
        tr.protocol_error = None
        tr._put({"seq": 1})
        tr._put({"seq": 2})   # overflows: must surface, never drop silently
        self.assertIsNotNone(tr.protocol_error)
        self.assertIn("overflow", tr.protocol_error)

    def test_transport_stop_keeps_stdin_open_until_killed(self):
        child_code = "import time,sys; from pathlib import Path; Path(sys.argv[1]).write_text('alive'); time.sleep(120)"
        root_code = "import subprocess,sys; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL); Path(sys.argv[3]).write_text(str(p.pid)); sys.stdin.read()"
        marker = self.tmp / "child_marker.txt"
        pid_file = self.tmp / "child_pid.txt"
        tr = zw.Transport([sys.executable, '-B', '-c', root_code, child_code,
                           str(marker), str(pid_file)], env=None, cwd=self.tmp)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "grandchild never started")
            self.assertIsNone(tr.proc.poll())
            status = tr.stop()
            self.assertEqual(status, "stopped")
            self.assertIsNotNone(tr.proc.poll())
            self.assertTrue(tr.proc.stdin.closed)
            child_pid = int(pid_file.read_text())
            if os.name == 'nt':
                # os.kill(pid, 0) is unsafe on Windows; query a process handle.
                import ctypes
                kernel = ctypes.windll.kernel32
                handle = kernel.OpenProcess(0x1000, False, child_pid)
                if handle:
                    try:
                        code = ctypes.c_ulong()
                        self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                        self.assertNotEqual(code.value, 259)
                    finally:
                        kernel.CloseHandle(handle)
            else:
                self.assertTrue(zw._group_gone(tr.proc.pid))
        finally:
            if tr.proc.poll() is None:
                tr.stop()

    @unittest.skipUnless(os.name == 'nt', 'Windows unexpected-parent-exit behavior')
    def test_unexpected_parent_exit_does_not_block_on_inherited_stdout(self):
        pid_file = self.tmp / 'orphan.pid'
        code = "import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],stdin=subprocess.DEVNULL);Path(sys.argv[1]).write_text(str(p.pid))"
        tr = zw.Transport([sys.executable, '-c', code, str(pid_file)], None, self.tmp)
        child_pid = None
        try:
            tr.proc.wait(timeout=5)
            child_pid = int(pid_file.read_text())
            started = time.monotonic()
            self.assertEqual(tr.stop(), 'uncertain')
            self.assertLess(time.monotonic() - started, 3)
        finally:
            if child_pid is not None:
                subprocess.run(['taskkill', '/PID', str(child_pid), '/T', '/F'],
                               capture_output=True, timeout=10, check=True)
            if tr.proc.poll() is None:
                tr.stop()
            tr.reader.join(timeout=3)
            tr._close_pipes()

    def test_catalog_does_not_require_a_per_model_builtin_override(self):
        cfg = fx.make_builtin_config()
        cfg['config']['modelConfigRules']['modelRules'] = []
        self.assertEqual(zw.builtin_config_problems(cfg), [])

    def test_missing_send_session_is_rejected(self):
        base = happy_script(self.ws, with_headers_check=False)
        def script(tr):
            base(tr)
            if tr.sent[-1]['method'] == 'session/send':
                for frame in tr.incoming:
                    if isinstance(frame.get('result'), dict) and frame['result'].get('accepted'):
                        frame['result'].pop('sessionId', None)
        code, runner = self.run_worker(FakeTransport(script))
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / 'state.json').read_text())
        self.assertIn('session/send ack', state['detail'])

    # ------------------------------------------------------ permissions

    def perm_script(self, payload_overrides=None, second=None):
        """High-risk permission after turn.started; the ack and completion
        follow (real wire order)."""
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                params = {"providerId": PROVIDER, "sessionId": SESSION_ID,
                          "turnId": TURN_ID,
                          "requestId": "req-1", "toolCallId": "call-1",
                          "toolName": "Bash", "riskLevel": "high",
                          "input": {"command": "rm -rf x"}}
                if payload_overrides:
                    params.update(payload_overrides)
                frames = [event_frame("turn.started", {}),
                          {"id": 9100, "method": "interaction/requestPermission",
                           "params": params},
                          {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                          event_frame("turn.completed",
                                      {"resultType": "success"})]
                if second:
                    frames.insert(2, {"id": 9101,
                                      "method": "interaction/requestPermission",
                                      "params": second})
                t.push(*frames)
                return
            base(t)
        return script

    def test_denied_high_permission_blocks_and_clears_marker(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.4
        try:
            tr = FakeTransport(self.perm_script())
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            # the pending marker is cleared once the request is resolved
            self.assertFalse((self.run_dir / "pending-permission.json").exists())
            self.assertFalse(runner.perm_pending)
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_granted_high_permission_via_approval_file(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 15
        orig_request = zw.Runner.request_high_approval

        def patched(self_, data, key, raw_input, identity):
            # simulate Astra writing a matching approval while we wait
            ap = self_.run_dir / "approval.json"
            ap.write_text(json.dumps({
                "stable_hash": key,
                "request_id": data.get("requestId"),
                "tool_call_id": data.get("toolCallId"),
                "input_hash": identity,
                "session_id": self_.session_id,
                "decision": "allow"}), encoding="utf-8")
            return orig_request(self_, data, key, raw_input, identity)
        zw.Runner.request_high_approval = patched
        try:
            tr = FakeTransport(self.perm_script())
            code, runner = self.run_worker(tr, timeout=20)
            self.assertEqual(code, 0, runner.blocked_reason)
            self.assertFalse((self.run_dir / "approval.json").exists(),
                             "approval must be consumed after use")
            self.assertFalse((self.run_dir / "pending-permission.json").exists(),
                             "pending marker must be cleared on resolution")
        finally:
            zw.PERM_WAIT_SECONDS = old
            zw.Runner.request_high_approval = orig_request

    def test_stale_approval_not_accepted(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.5
        orig_request = zw.Runner.request_high_approval

        def patched(self_, data, key, raw_input, identity):
            ap = self_.run_dir / "approval.json"
            ap.write_text(json.dumps({
                "stable_hash": "WRONG", "decision": "allow"}), encoding="utf-8")
            return orig_request(self_, data, key, raw_input, identity)
        zw.Runner.request_high_approval = patched
        try:
            tr = FakeTransport(self.perm_script())
            code, runner = self.run_worker(tr, timeout=15)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
        finally:
            zw.PERM_WAIT_SECONDS = old
            zw.Runner.request_high_approval = orig_request

    def test_oversized_high_input_refused_not_truncated(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.3
        try:
            big = "X" * (zw.MAX_PERM_INPUT_CHARS + 10)
            tr = FakeTransport(self.perm_script(payload_overrides={
                "input": {"command": big}}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertIn("oversized", state["detail"])
            pp = self.run_dir / "pending-permission.json"
            if pp.exists():
                self.assertNotIn(big[:50], pp.read_text(),
                                 "oversized input must never be stored")
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_high_input_stored_complete_under_limit(self):
        import threading
        cmd = 'local-check ' + 'Y' * 4000
        observed = []
        def reviewer():
            path = self.run_dir / 'pending-permission.json'
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if path.exists():
                    data = json.loads(path.read_text())
                    observed.append(data['input']['command'])
                    data['decision'] = 'allow'
                    approval = self.run_dir / 'approval.tmp'
                    approval.write_text(json.dumps(data))
                    approval.replace(self.run_dir / 'approval.json')
                    return
                time.sleep(0.02)
        reviewer_thread = threading.Thread(target=reviewer, daemon=True)
        reviewer_thread.start()
        try:
            tr = FakeTransport(self.perm_script(payload_overrides={
                "input": {"command": cmd}}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 0)
            self.assertEqual(observed, [cmd])
            pp = self.run_dir / "pending-permission.json"
            self.assertFalse(pp.exists(),
                             "resolved request marker must be cleared")
        finally:
            reviewer_thread.join(timeout=6)

    def test_redacted_permission_input_is_refused_without_review_file(self):
        tr = FakeTransport(self.perm_script(payload_overrides={
            'input': {'command': 'echo ' + SECRET}}))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertIn('cannot be displayed completely', runner.blocked_reason)
        self.assertFalse((self.run_dir / 'pending-permission.json').exists())

    def test_missing_personal_config_blocks_spawn(self):
        (self.home / 'v2/provider_config.json').unlink()
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertEqual(tr.sent, [])
        self.assertIn('personal provider config',
                      json.loads((self.run_dir / 'state.json').read_text())['detail'])

    def test_repeated_identical_permission_reuses_decision(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.3
        try:
            tr = FakeTransport(self.perm_script(second={
                "providerId": PROVIDER, "sessionId": SESSION_ID,
                "turnId": TURN_ID,
                "requestId": "req-1", "toolCallId": "call-1",
                "toolName": "Bash", "riskLevel": "high",
                "input": {"command": "rm -rf x"}}))
            code, runner = self.run_worker(tr, timeout=15)
            perm_replies = [r for r in tr.replies
                            if isinstance(r.get("result"), dict)
                            and "decision" in r["result"]]
            self.assertEqual(len(perm_replies), 2)
            self.assertIn("reused", perm_replies[1]["result"]["reason"])
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_same_permission_changed_input_denied_and_blocked(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.3
        try:
            tr = FakeTransport(self.perm_script(second={
                "providerId": PROVIDER, "sessionId": SESSION_ID,
                "turnId": TURN_ID,
                "requestId": "req-1", "toolCallId": "call-1",
                "toolName": "Bash", "riskLevel": "high",
                "input": {"command": "DIFFERENT"}}))
            code, runner = self.run_worker(tr, timeout=15)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertTrue(any("changed semantics" in k for k
                                in state["permission_decisions"]),
                            state["permission_decisions"])
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_same_permission_changed_tool_denied(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.3
        try:
            tr = FakeTransport(self.perm_script(second={
                "providerId": PROVIDER, "sessionId": SESSION_ID,
                "turnId": TURN_ID,
                "requestId": "req-1", "toolCallId": "call-1",
                "toolName": "Write", "riskLevel": "high",
                "input": {"command": "rm -rf x"}}))
            code, runner = self.run_worker(tr, timeout=15)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertTrue(any("changed semantics" in k for k
                                in state["permission_decisions"]))
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_permission_missing_identifiers_denied(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.2
        try:
            tr = FakeTransport(self.perm_script(payload_overrides={
                "requestId": None}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertIn("missing identifiers", state["detail"])
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_permission_for_wrong_turn_denied(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.2
        try:
            tr = FakeTransport(self.perm_script(payload_overrides={
                "turnId": "turn_other"}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertIn("turn", state["detail"])
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_critical_risk_level_blocks(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.2
        try:
            tr = FakeTransport(self.perm_script(payload_overrides={
                "riskLevel": "critical"}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
        finally:
            zw.PERM_WAIT_SECONDS = old

    def test_permission_for_wrong_session_denied(self):
        old = zw.PERM_WAIT_SECONDS
        zw.PERM_WAIT_SECONDS = 0.2
        try:
            tr = FakeTransport(self.perm_script(payload_overrides={
                "sessionId": "sess_other"}))
            code, runner = self.run_worker(tr, timeout=10)
            self.assertEqual(code, 1)
            state = json.loads((self.run_dir / "state.json").read_text())
            self.assertEqual(state["status"], "blocked")
            self.assertIn("session", (state["blocked_reason"] or "").lower())
        finally:
            zw.PERM_WAIT_SECONDS = old

    def _runner_for_cleanup_checks(self):
        runner = make_runner(self.tmp, self.ws, self.task, self.run_dir)
        runner.workspace = self.ws
        runner.session_id = SESSION_ID
        runner.turn_id = TURN_ID
        runner.run_dir = self.run_dir
        runner.secrets = [SECRET]
        return runner

    def test_cleanup_headers_never_get_auth(self):
        runner = self._runner_for_cleanup_checks()
        runner._cleanup_started = True
        tr = FakeTransport(script=lambda t: None)
        runner.handle_headers(tr, 1, {
            "providerId": PROVIDER, "sessionId": SESSION_ID,
            "reason": "model-request",
            "modelSelection": {"providerId": PROVIDER, "modelId": MODEL},
            "workspace": {"workspacePath": str(self.ws),
                          "workspaceKey": str(self.ws)}})
        self.assertTrue(all("result" not in r for r in tr.replies),
                        "no auth may be granted during cleanup")
        self.assertEqual(tr.replies[0]["error"]["code"], -32000)

    def test_runtime_preferences_disable_automatic_user_answers(self):
        runner = self._runner_for_cleanup_checks()
        tr = FakeTransport(script=lambda t: None)
        runner.interaction(tr, {'id': 'preferences',
            'method': 'session/requestRuntimePreferences',
            'params': {'sessionId': SESSION_ID, 'scope': 'runtime-materialization'}})
        result = tr.replies[0]['result']
        self.assertFalse(result['askUserQuestionAutoResolutionEnabled'])
        self.assertFalse(result['memoryEnabled'])
        self.assertFalse(result['nativeSearchEnhancementsEnabled'])
        self.assertEqual(result['modelContextBudgetStrategy'], 'preflight-v1')
        self.assertIsNone(runner.blocked_reason)

    def test_runtime_preferences_reject_foreign_session(self):
        runner = self._runner_for_cleanup_checks()
        tr = FakeTransport(script=lambda t: None)
        runner.interaction(tr, {'id': 'preferences',
            'method': 'session/requestRuntimePreferences',
            'params': {'sessionId': 'foreign', 'scope': 'user-execution'}})
        self.assertIn('error', tr.replies[0])
        self.assertIsNotNone(runner.blocked_reason)

    def test_runtime_preferences_before_create_ack_are_bound_to_snapshot(self):
        base = happy_script(self.ws)
        def script(tr):
            if tr.sent[-1]['method'] == 'session/create':
                tr.push({'id': 'prefs', 'method': 'session/requestRuntimePreferences',
                         'params': {'sessionId': SESSION_ID, 'scope': 'runtime-materialization'}})
            base(tr)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 0)
        self.assertEqual(runner.preference_session, SESSION_ID)
        self.assertFalse(next(r['result'] for r in tr.replies if r['id'] == 'prefs')
                         ['askUserQuestionAutoResolutionEnabled'])

    def test_runtime_preference_hint_cannot_replace_returned_session(self):
        base = happy_script(self.ws)
        def script(tr):
            if tr.sent[-1]['method'] == 'session/create':
                tr.push({'id': 'prefs', 'method': 'session/requestRuntimePreferences',
                         'params': {'sessionId': 'foreign', 'scope': 'runtime-materialization'}})
            base(tr)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertNotIn('session/send', tr.methods())

    def test_cleanup_permission_never_reuses_cached_grant(self):
        runner = self._runner_for_cleanup_checks()
        runner._cleanup_started = True
        data = {"sessionId": SESSION_ID, "turnId": TURN_ID,
                "requestId": "req-1", "toolCallId": "call-1",
                "toolName": "Bash", "riskLevel": "low", "input": {}}
        identity = zw.sha256_text(json.dumps(
            {"tool": "Bash", "level": "low", "input": "{}"},
            sort_keys=True, ensure_ascii=False))
        key = f"{SESSION_ID}|{TURN_ID}|req-1|call-1"
        runner.perm_cache[key] = "allow"
        runner.perm_identities[key] = identity
        tr = FakeTransport(script=lambda t: None)
        runner.handle_permission(tr, 2, data)
        decision = tr.replies[0]["result"]
        self.assertEqual(decision["decision"], "deny")
        self.assertIn("cleanup", decision["reason"])

    # ------------------------------------------------------ auth scope

    def test_headers_wrong_session_gets_top_level_error_and_blocks(self):
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"id": 9200, "method":
                     "interaction/requestProviderRuntimeHeaders",
                     "params": {"providerId": PROVIDER,
                                "sessionId": "sess_EVIL",
                                "reason": "model-request",
                                "modelSelection": {"providerId": PROVIDER,
                                                   "modelId": MODEL},
                                "workspace": {
                                    "workspacePath": str(self.ws),
                                    "workspaceKey": str(self.ws)}}},
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                    event_frame("turn.completed", {"resultType": "success"}))
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr, timeout=10)
        err = next((r for r in tr.replies if "error" in r), None)
        self.assertIsNotNone(err, "rejection must be a top-level {id,error}")
        self.assertEqual(err["error"]["code"], -32000)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "blocked")
        # no auth was ever supplied
        for r in tr.replies:
            if isinstance(r.get("result"), dict):
                self.assertNotIn("requestAuth", r["result"])

    def test_captcha_retry_never_gets_auth(self):
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"id": 9300, "method": "interaction/solveCaptcha",
                     "params": {"challenge": "x"}},
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                    event_frame("turn.completed", {"resultType": "success"}))
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr, timeout=10)
        err = next((r for r in tr.replies if "error" in r), None)
        self.assertIsNotNone(err)
        self.assertEqual(err["error"]["code"], -32601)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "blocked")

    # ------------------------------------------------------ redaction

    def test_secret_redacted_even_at_truncation_boundary(self):
        # Craft a turn payload embedding the key so it straddles the clip
        # boundary; redaction happens BEFORE clipping so no fragment survives.
        pad = "P" * (zw.MAX_VALUE_LEN - len(SECRET) // 2)
        leaked = pad + SECRET + "TAILTAILTAIL"
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                    event_frame("turn.completed",
                                {"resultType": "success", "response": leaked}))
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        # no fragment of the key may appear anywhere in artifacts
        needle = SECRET[:6]
        for name in ("state.json", "events.ndjson", "result.md"):
            text = (self.run_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(needle, text, f"{name} leaks key fragment")
            self.assertNotIn(SECRET[-8:], text)
        self.assertIn("[REDACTED]", text)

    def test_secret_dictionary_key_redacted_everywhere(self):
        """A secret used as a dictionary KEY (e.g. inside usage) must be
        sanitized in state.json, events.ndjson AND result.md."""
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                    event_frame("turn.completed", {
                        "resultType": "success",
                        "usage": {SECRET: 6, "inputTokens": 4}}))
                return
            base(t)
        tr = FakeTransport(script)
        code, runner = self.run_worker(tr)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["usage"].get("[REDACTED_KEY]"), "[REDACTED]",
                         "the secret key must be redacted in state.json")
        for name in ("state.json", "events.ndjson", "result.md"):
            text = (self.run_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(SECRET, text, f"{name} leaks a secret key")
            self.assertNotIn(SECRET[:8], text, f"{name} leaks a key fragment")
        # the innocent sibling usage values survive
        self.assertEqual(state["usage"], {"[REDACTED_KEY]": "[REDACTED]",
                                          "inputTokens": 4})

    def test_secret_in_blocked_reason_stays_out_of_artifacts_and_stdout(self):
        import contextlib
        import io
        base = happy_script(self.ws, with_headers_check=False)

        def script(t):
            last = t.sent[-1] if t.sent else None
            if last and last["method"] == "session/send":
                t.push(
                    {"id": 9400, "method":
                     "interaction/requestProviderRuntimeHeaders",
                     "params": {"providerId": PROVIDER,
                                "sessionId": SECRET,   # attacker-controlled
                                "reason": "model-request"}},
                    {"id": last["id"], "result": {"accepted": True, "sessionId": SESSION_ID}},
                    event_frame("turn.completed", {"resultType": "success"}))
                return
            base(t)
        tr = FakeTransport(script)
        runner = make_runner(self.tmp, self.ws, self.task, self.run_dir)
        orig_version = zw.runtime_version
        zw.runtime_version = \
            lambda node, cjs, timeout=zw.VERSION_SUBPROC_TIMEOUT: zw.RUNTIME_VERSION
        runner.spawn = lambda: tr
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = runner.run()
        zw.runtime_version = orig_version
        self.assertEqual(code, 1)
        out = buf.getvalue()
        self.assertNotIn(SECRET, out, "stdout leaks the secret session id")
        for name in ("state.json", "result.md", "events.ndjson"):
            text = (self.run_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(SECRET, text, f"{name} leaks the secret")
        self.assertIn("outside expected scope", out)

    # ------------------------------------------------------ locks & cleanup

    def test_concurrent_lock_refused_and_preserved(self):
        lock_path = self.ws / zw.LOCK_NAME
        lock_path.parent.mkdir(exist_ok=True)
        lock_path.write_text(json.dumps({"pid": 999999,
                                         "run_dir": "other",
                                         "token": "foreign"}), encoding="utf-8")
        before = lock_path.read_text()
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("locked", state["detail"])
        self.assertEqual(lock_path.read_text(), before,
                         "another owner's lock must never be unlinked")
        self.assertEqual(tr.sent, [])
        self.assertEqual(runner.cleanup_status, "not-started")

    def test_uncertain_cleanup_preserves_lock_and_blocks(self):
        tr = FakeTransport(happy_script(self.ws), stop_result="uncertain")
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["cleanup"], "uncertain")
        self.assertTrue((self.ws / zw.LOCK_NAME).exists(),
                        "lock must be preserved on uncertain cleanup")
        # descriptor hygiene: the preserved lock must not hold an open handle
        # (TemporaryDirectory cleanup below would fail on Windows otherwise)

    def test_lock_descriptor_closed_on_preserve_and_release(self):
        lock = zw.WorkspaceLock(self.ws, self.run_dir)
        lock.acquire()
        self.assertTrue(lock.held)
        self.assertIsNotNone(lock.fd)
        lock.preserve()
        self.assertIsNone(lock.fd, "preserve must close the descriptor")
        self.assertTrue((self.ws / zw.LOCK_NAME).exists(),
                        "preserve keeps the lock file")
        lock.release()
        self.assertTrue((self.ws / zw.LOCK_NAME).exists(),
                        "release after preserve must not unlink")
        other_ws = self.tmp / "second-workspace"
        other_ws.mkdir()
        lock2 = zw.WorkspaceLock(other_ws, other_ws / "run")
        lock2.acquire()
        lock2.release()
        self.assertFalse((other_ws / zw.LOCK_NAME).exists(),
                         "release of a held lock unlinks it")

    def test_lock_through_symlinked_ai_dir_refused(self):
        link = self.ws / ".ai"
        outside = self.tmp / "outside-ai"
        outside.mkdir()
        created = False
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(outside), str(link))
                created = True
            else:
                os.symlink(outside, link)
                created = True
        except (OSError, ImportError):
            self.skipTest("cannot create links on this platform")
        self.assertTrue(created)
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn(".ai", state["detail"])
        self.assertEqual(tr.sent, [])

    # ------------------------------------------------------ paths

    def test_existing_run_dir_refused_without_writes(self):
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "state.json").write_text("PREVIOUS EVIDENCE",
                                                 encoding="utf-8")
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        self.assertEqual(code, 1)
        self.assertEqual((self.run_dir / "state.json").read_text(),
                         "PREVIOUS EVIDENCE")
        self.assertNotIn("session/create", tr.methods())

    def test_run_dir_outside_workspace_refused(self):
        outside = self.tmp / "outside-run"
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr, run_dir=outside)
        self.assertEqual(code, 1)
        self.assertFalse(outside.exists())

    def test_run_dir_dotdot_escape_creates_nothing(self):
        rd = self.ws / ".." / "escaped-run" / "attempt-01"
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr, run_dir=rd)
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "escaped-run").exists(),
                         "'..' run-dir must not create anything")
        state = json.loads((self.ws / "runs" / "r1" / "state.json").read_text()) \
            if (self.ws / "runs" / "r1").exists() else {}
        self.assertEqual(tr.sent, [])

    def test_run_dir_through_parent_link_refused(self):
        link = self.ws / "link"
        outside = self.tmp / "outside-link-target"
        outside.mkdir()
        try:
            if os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(outside), str(link))
            else:
                os.symlink(outside, link)
        except (OSError, ImportError):
            self.skipTest("cannot create links on this platform")
        rd = self.ws / "link" / "attempt-01"
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr, run_dir=rd)
        self.assertEqual(code, 1)
        self.assertFalse((outside / "attempt-01").exists(),
                         "run-dir through a parent link must not be created")
        self.assertEqual(tr.sent, [])

    def test_missing_workspace_refused(self):
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(
            tr, workspace=self.tmp / "nope" / "missing")
        self.assertEqual(code, 1)

    def test_task_outside_workspace_refused(self):
        outside_task = self.tmp / "outside-task.md"
        outside_task.write_text("x", encoding="utf-8")
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr, task=outside_task)
        self.assertEqual(code, 1)

    # ------------------------------------------------------ home override

    def test_zcode_home_is_honored_by_run(self):
        # A home whose provider kind is wrong must fail: proves run read the
        # custom home, not the real ~/.zcode.
        bad_home = make_home(self.tmp / "h2", kind="openai")
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr, home=bad_home)
        self.assertEqual(code, 1)
        state = json.loads((self.run_dir / "state.json").read_text())
        self.assertIn("kind", state["detail"])

    def test_prompt_has_no_real_user_name(self):
        tr = FakeTransport(happy_script(self.ws))
        code, runner = self.run_worker(tr)
        send = next(m for m in tr.sent if m["method"] == "session/send")
        content = send["params"]["content"]
        self.assertNotIn("Marco explicitly authorized", content)
        self.assertIn("The workspace owner explicitly authorized", content)


# ---------------------------------------------------------------- e2e

class BridgeEndToEnd(unittest.TestCase):
    """Real-subprocess cases adapted from Astra's independent acceptance
    harness: the worker CLI runs against the Python fake runtime from
    bridge_fixtures (no Node, no account, no network)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name).resolve()
        cls.runtime = fx.make_runtime(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_case(self, case: str, *, escape=False, mutate_endpoint=False,
                 timeout=4):
        fx.set_builtin_endpoint(self.runtime["builtin"],
                                "https://wrong.invalid" if mutate_endpoint else fx.ENDPOINT)
        return fx.run_worker(self.base, case, self.runtime, timeout=timeout,
                             escape=escape)

    def assert_no_secret(self, *texts):
        for text in texts:
            self.assertNotIn(fx.SECRET, text, "synthetic credential leaked")

    def case_output(self, ws, rd, proc):
        state = json.loads((rd / "state.json").read_text(encoding="utf-8")) \
            if (rd / "state.json").exists() else {}
        result_md = (rd / "result.md").read_text(encoding="utf-8") \
            if (rd / "result.md").exists() else ""
        return state, result_md

    def test_happy(self):
        proc, ws, rd = self.run_case("happy")
        state, result_md = self.case_output(ws, rd, proc)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(state.get("status"), "ready_for_review")
        self.assertIn("FAKE_WORKER_DELIVERY", result_md)
        self.assertEqual(state.get("cleanup"), "stopped")
        self.assertFalse((ws / zw.LOCK_NAME).exists(),
                         "lock must be removed after clean success")
        self.assert_no_secret(proc.stdout, proc.stderr, result_md,
                              (rd / "state.json").read_text(encoding="utf-8"),
                              (rd / "events.ndjson").read_text(encoding="utf-8"))

    def test_early(self):
        proc, ws, rd = self.run_case("early")
        state, result_md = self.case_output(ws, rd, proc)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(state.get("status"), "ready_for_review")
        self.assertIn("FAKE_WORKER_DELIVERY", result_md)

    def test_wrong_final(self):
        proc, ws, rd = self.run_case("wrong_final")
        state, _ = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("settled", str(state.get("detail")),
                      "final snapshot mismatch must be recognized")

    def test_failed(self):
        proc, ws, rd = self.run_case("failed")
        state, _ = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("turn.failed", str(state.get("detail")),
                      "failure must be recognized before timeout")

    def test_denied(self):
        proc, ws, rd = self.run_case("denied")
        state, _ = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(state.get("status"), "blocked")

    def test_wrong_endpoint_spawns_nothing(self):
        proc, ws, rd = self.run_case("wrong_endpoint", mutate_endpoint=True)
        state, _ = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((ws / "spawned.marker").exists(),
                         "spawned despite unvalidated builtin endpoint")
        self.assertFalse((ws / "sent.marker").exists(),
                         "sent despite invalid builtin endpoint")
        self.assertIn("official endpoint", str(state.get("detail")))

    def test_path_escape_creates_nothing(self):
        proc, ws, rd = self.run_case("path_escape", escape=True)
        escaped = self.base / "escaped" / "attempt-01"
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(escaped.exists(),
                         "outside run-dir created before containment validation")

    def test_secret_usage_key_never_persists(self):
        proc, ws, rd = self.run_case("secret_usage")
        state, result_md = self.case_output(ws, rd, proc)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(state.get("status"), "ready_for_review")
        for p in rd.glob("*"):
            if p.is_file():
                self.assertNotIn(
                    fx.SECRET, p.read_text(encoding="utf-8", errors="replace"),
                    f"key persisted in {p.name}")
        self.assert_no_secret(proc.stdout, proc.stderr, result_md)

    def test_secret_metadata_session_never_leaks(self):
        proc, ws, rd = self.run_case("secret_metadata")
        state, result_md = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        marker = ws / "auth.marker"
        if marker.exists():
            self.assertFalse(json.loads(marker.read_text())["secret_received"],
                             "credential sent to an unrelated session")
        self.assert_no_secret(proc.stdout, proc.stderr, result_md,
                              (rd / "state.json").read_text(encoding="utf-8"),
                              (rd / "events.ndjson").read_text(encoding="utf-8"))

    def test_wrong_auth_never_sends_key(self):
        proc, ws, rd = self.run_case("wrong_auth")
        state, _ = self.case_output(ws, rd, proc)
        self.assertNotEqual(proc.returncode, 0)
        marker = ws / "auth.marker"
        if marker.exists():
            self.assertFalse(json.loads(marker.read_text())["secret_received"])
        self.assert_no_secret(proc.stdout, proc.stderr)

    def test_eof_keeps_lock_on_windows(self):
        proc, ws, rd = self.run_case("eof")
        self.assertNotEqual(proc.returncode, 0)
        if sys.platform == "win32":
            self.assertTrue((ws / zw.LOCK_NAME).exists(),
                            "root exited pre-cleanup: lock must be preserved")


if __name__ == "__main__":
    unittest.main()
