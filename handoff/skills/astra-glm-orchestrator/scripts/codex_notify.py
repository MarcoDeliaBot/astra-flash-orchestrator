#!/usr/bin/env python3
"""Bounded stdlib MCP client for the bundled codex-app-tools server.

Launches the OFFICIAL bundled server already present under
$CODEX_HOME (default ~/.codex):
  plugins/cache/openai-bundled/codex-app-tools/<version>/server.mjs
over newline-delimited JSON-RPC stdio, exactly like the verified read-only
probe. The thread is taken from the CURRENT host environment (CODEX_THREAD_ID)
and passed per call via params._meta {"openai/threadId": ...}; the pipe
location comes from CODEX_APP_TOOLS_PIPE_PATH. --interaction-client-id is
never used and the bundled server is never patched.

Subcommands:
  preflight            initialize + tools/list only; verifies the server and
                       the send_message_to_thread tool WITHOUT any send or
                       model inference. Read-only; never dumps the catalog.
  send --text TEXT     one bounded send_message_to_thread call to the same
                       validated CODEX_THREAD_ID.

Both subcommands require CODEX_THREAD_ID and CODEX_APP_TOOLS_PIPE_PATH in the
environment; missing metadata is a hard failure, never silent success.
Outcome is one JSON line on stdout: ok/outcome/detail. Exit 0 on success.
Delivered is claimed only for a valid, non-error result; a timeout/EOF after
the request was written is "uncertain" (the host API has no demonstrated
idempotency key, so exactly-once delivery is never promised).

Python 3.11 stdlib only. Bounded waits everywhere; clean child shutdown.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

SERVER_GLOB = "plugins/cache/openai-bundled/codex-app-tools/*/server.mjs"
NOTIFY_TOOL = "send_message_to_thread"
PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "astra-glm-notify"
CLIENT_VERSION = "1"
DEFAULT_TIMEOUT = 20.0      # total wall-clock budget per invocation
MAX_TIMEOUT = 120.0
MAX_FRAME_CHARS = 200_000   # responses are tiny; larger = protocol error
SHUTDOWN_WAIT = 3.0         # bounded clean child shutdown
THREAD_ID_MAX = 200


class NotifyError(RuntimeError):
    """Definite client-side or JSON-RPC error (no request in flight or the
    host answered with an explicit error)."""


class NotifyUncertain(RuntimeError):
    """The request may or may not have reached the host (timeout, EOF or a
    malformed reply after the request was written). Never auto-retried."""


def codex_home(explicit: str | None = None) -> Path:
    root = explicit or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(root).expanduser()


def find_server(codex_home: Path) -> Path:
    """Newest bundled codex-app-tools server.mjs under the Codex home. An
    explicit existing path (deployment/testing) bypasses discovery."""
    candidates: list[tuple[tuple[int, ...], str, Path]] = []
    try:
        matches = sorted(codex_home.glob(SERVER_GLOB))
    except OSError as exc:
        raise NotifyError(f"cannot scan {codex_home}: {exc}") from None
    for path in matches:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        version_part = path.parent.name
        try:
            key = tuple(int(x) for x in version_part.split("."))
        except ValueError:
            key = (0,)
        candidates.append((key, str(path), path))
    if not candidates:
        raise NotifyError(
            f"no bundled codex-app-tools server found under {codex_home} "
            f"(looked for {SERVER_GLOB}); pass --server explicitly")
    return max(candidates)[2]


def read_host_env() -> tuple[str, str]:
    """Require valid CURRENT host metadata; no false success if absent."""
    thread_id = (os.environ.get("CODEX_THREAD_ID") or "").strip()
    pipe = (os.environ.get("CODEX_APP_TOOLS_PIPE_PATH") or "").strip()
    if not thread_id:
        raise NotifyError("CODEX_THREAD_ID is missing or empty in the "
                          "environment; refusing to guess a destination")
    if not pipe:
        raise NotifyError("CODEX_APP_TOOLS_PIPE_PATH is missing or empty in "
                          "the environment; the app pipe is unavailable")
    if len(thread_id) > THREAD_ID_MAX or any(c.isspace() or ord(c) < 33
                                             for c in thread_id):
        raise NotifyError("CODEX_THREAD_ID has an invalid shape")
    return thread_id, pipe


class NotifyClient:
    """One bounded MCP session over stdio with the bundled server."""

    def __init__(self, server: Path, node: str = "node", timeout: float = DEFAULT_TIMEOUT):
        self.server = Path(server)
        self.node = node
        self.deadline = time.monotonic() + max(1.0, float(timeout))
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue(maxsize=64)
        self.protocol_error: str | None = None
        self._seq = 0
        self.server_info: dict | None = None

    # -- plumbing

    def _remaining(self) -> float:
        return self.deadline - time.monotonic()

    def start(self) -> None:
        if not self.server.is_file():
            raise NotifyError(f"server path does not exist: {self.server}")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen(
                [self.node, str(self.server)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                encoding="utf-8", shell=False, creationflags=flags)
        except OSError as exc:
            raise NotifyError(f"cannot launch node server: {exc}") from None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            for line in iter(lambda: self.proc.stdout.readline(MAX_FRAME_CHARS + 1), ""):
                if len(line) > MAX_FRAME_CHARS:
                    if self.protocol_error is None:
                        self.protocol_error = "oversized frame from server"
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    if self.protocol_error is None:
                        self.protocol_error = "malformed JSON frame from server"
                    continue
                try:
                    self.q.put_nowait(frame)
                except queue.Full:
                    if self.protocol_error is None:
                        self.protocol_error = "response queue overflow"
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.q.put_nowait({"__eof__": True})
            except queue.Full:
                pass

    def _send(self, message: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        if self.proc.stdin.closed or self.proc.poll() is not None:
            raise NotifyUncertain("server stdin closed before write")
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise NotifyUncertain(f"write failed: {exc}") from None

    def _await(self, msg_id) -> dict:
        while True:
            left = self._remaining()
            if left <= 0:
                raise NotifyUncertain("timeout waiting for server response")
            try:
                frame = self.q.get(timeout=min(left, 0.25))
            except queue.Empty:
                continue
            if not isinstance(frame, dict):
                raise NotifyUncertain("server response is not an object")
            if frame.get("__eof__"):
                raise NotifyUncertain("server exited before responding")
            if self.protocol_error:
                raise NotifyUncertain(self.protocol_error)
            if frame.get("id") != msg_id:
                continue  # unrelated notification/response
            if "error" in frame:
                err = frame["error"]
                raise NotifyError(f"jsonrpc error {err.get('code')}: "
                                  f"{err.get('message')}")
            if not isinstance(frame.get("result"), dict):
                raise NotifyUncertain("server response has no object result")
            return frame["result"]

    def call(self, method: str, params: dict) -> dict:
        self._seq += 1
        msg_id = self._seq
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method,
                    "params": params})
        return self._await(msg_id)

    # -- protocol

    def initialize(self) -> None:
        result = self.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}})
        info = result.get("serverInfo") if isinstance(result, dict) else None
        self.server_info = info if isinstance(info, dict) else None
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def verify_tool(self) -> None:
        """tools/list then a shape check for the one tool we need. The catalog
        itself is never printed or persisted."""
        result = self.call("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise NotifyError("tools/list returned no tool list")
        tool = next((t for t in tools if isinstance(t, dict)
                     and t.get("name") == NOTIFY_TOOL), None)
        if tool is None:
            raise NotifyError(f"server does not expose {NOTIFY_TOOL}")
        props = ((tool.get("inputSchema") or {}).get("properties") or {})
        missing = [k for k in ("threadId", "prompt") if k not in props]
        if missing:
            raise NotifyError(f"{NOTIFY_TOOL} schema missing {missing}")

    def send(self, thread_id: str, text: str) -> None:
        """One send_message_to_thread call. Model/thinking are deliberately
        omitted so the destination thread keeps its existing settings."""
        if not text or not text.strip():
            raise NotifyError("refusing to send an empty notification")
        result = self.call("tools/call", {
            "name": NOTIFY_TOOL,
            "arguments": {"threadId": thread_id, "prompt": text},
            "_meta": {"openai/threadId": thread_id}})
        if result.get("isError") is True:
            raise NotifyError("host reported tool error: "
                              + _first_text(result)[:200])
        try:
            receipt = json.loads(_first_text(result))
        except (ValueError, TypeError):
            raise NotifyUncertain("host did not return a valid delivery receipt") from None
        if not isinstance(receipt, dict) or receipt.get("threadId") != thread_id:
            raise NotifyUncertain("host receipt does not confirm the intended thread")

    # -- shutdown

    def close(self) -> str:
        """Clean bounded shutdown: close stdin, wait, then kill. Never raises
        (close runs in cleanup paths); 'uncertain' is reported, not hidden."""
        if self.proc is None:
            return "stopped"
        try:
            try:
                if self.proc.stdin and not self.proc.stdin.closed:
                    self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=SHUTDOWN_WAIT)
                return "stopped"
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=SHUTDOWN_WAIT)
                return "stopped"
        except (OSError, subprocess.TimeoutExpired):
            return "uncertain"


def _first_text(result: dict) -> str:
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text") or "")
    return ""


def _run(mode: str, text: str | None, args) -> dict:
    """Shared bounded invocation. Returns the outcome dict; never sends
    anything in preflight mode."""
    thread_id, _pipe = read_host_env()
    server = Path(args.server) if args.server else find_server(codex_home(args.codex_home))
    client = NotifyClient(server, node=args.node, timeout=args.timeout)
    try:
        client.start()
        client.initialize()
        client.verify_tool()
        if mode == "preflight":
            return {"ok": True, "outcome": "ready", "server": str(server),
                    "server_info": client.server_info, "tool": NOTIFY_TOOL}
        client.send(thread_id, text or "")
        return {"ok": True, "outcome": "delivered", "server": str(server),
                "thread": "CODEX_THREAD_ID"}
    except NotifyError as exc:
        return {"ok": False, "outcome": "failed", "error": str(exc),
                "server": str(server)}
    except NotifyUncertain as exc:
        return {"ok": False, "outcome": "uncertain", "error": str(exc),
                "server": str(server)}
    finally:
        client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="codex_notify.py",
        description="Bounded MCP notify client for the bundled codex-app-tools server")
    parser.add_argument("--server", default=None,
                        help="explicit existing server.mjs path (skips discovery)")
    parser.add_argument("--codex-home", default=None,
                        help="Codex home for bundled-server discovery "
                             "(default $CODEX_HOME or ~/.codex)")
    parser.add_argument("--node", default="node", help="node executable")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"total wall-clock budget in seconds "
                             f"(default {DEFAULT_TIMEOUT:g}, max {MAX_TIMEOUT:g})")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight", help="read-only server/tool verification (no send)")
    send = sub.add_parser("send", help="send one short notification")
    send.add_argument("--text", required=True, help="notification text (kept short)")
    args = parser.parse_args(argv)
    timeout = min(max(1.0, float(args.timeout)), MAX_TIMEOUT)
    args.timeout = timeout
    mode = "preflight" if args.cmd == "preflight" else "send"
    try:
        outcome = _run(mode, getattr(args, "text", None), args)
    except NotifyError as exc:                 # env/discovery failures
        outcome = {"ok": False, "outcome": "failed", "error": str(exc)}
    except NotifyUncertain as exc:
        outcome = {"ok": False, "outcome": "uncertain", "error": str(exc)}
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
