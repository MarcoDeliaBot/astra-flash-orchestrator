# Event-driven Astra and GLM

Give a GOAL to Astra using `$astra-glm-orchestrator`. Astra prepares a coherent
assignment and uses `zcode_worker.py start` inside the owning Codex desktop chat.
After a confirmed background launch, it saves the run reference and ends its turn.
The bounded local worker continues without coordinator inference or a periodic
heartbeat. No second Astra or GLM coordinator is created.

## When the chat resumes

- A specific high-risk operation needs Astra's decision. The exact pending
  request is saved before the notification. Astra reads it, checks scope and
  expiry, approves or denies only that operation, then yields again.
- GLM returns ready for review, fails or encounters a blocker. Final evidence
  and cleanup state are recorded before the notification. Astra reviews the
  result and patch, reuses valid checks and sends a coherent correction if needed.

Ordinary tool grants and progress do not cause notifications. A help request may
end the current worker run when the existing ZCode protocol cannot continue it;
notification support does not invent a new interactive protocol.

Notifications contain run/event identifiers and artifact references, not worker
transcripts, commands to execute or new authority. Astra must match them to its
saved assignment. Duplicate or stale events do not authorize another worker.

## Host connection and limits

The adapter uses the installed official `codex-app-tools` MCP server over stdio,
with the actual current thread identity and desktop connection inherited from
Codex. Its `send_message_to_thread` tool targets that same thread without model
overrides. It does not patch the app, use a private raw pipe protocol, create API
credentials or modify model settings. Host capabilities and app versions can
change; successful preflight does not prove that a later message will arrive.

Keep Codex and the computer available. Starting GLM in the background does not
provide execution through sleep, reboot or a closed app. A successful send means
the host accepted the message; it does not prove Astra has processed it, approved
anything or accepted the work. The host may queue a message while the chat is busy.

The existing permission window is at most 180 seconds, bounded by the worker's
deadline. A busy/unavailable chat can miss it; never approve stale requests or
grant blanket permissions to compensate. Terminal results remain reviewable.

## Delivery failure and recovery

Delivery state and compact status remain local. If a send has an ambiguous outcome,
do not blindly retry: the host tool has no verified message-idempotency contract.
Local deduplication cannot guarantee exactly-once delivery across a host failure.
Read `status --run-dir ABS` on demand to recover; missing files do not prove a
worker is alive. Detailed evidence remains in the run's final artifacts.

If messaging prerequisites fail, `start` must report that failure before paid
work. Bounded foreground `run` is the explicit fallback; it requires an active
coordinator and does not promise a later wakeup. Installation creates no service,
scheduled task or recurring automation and does not change existing monitors.

## Evidence

See [validation](VALIDATION.md) for the actual tested revision and limits. The
general lifecycle of threads, turns and notifications is documented in the
[official Codex App Server guide](https://learn.chatgpt.com/docs/app-server).
This desktop adapter uses the installed app-tools capability rather than starting
a separate app-server conversation. No token-savings percentage is claimed.
