# Token economy for Astra and GLM

The goal is less coordinator work per accepted outcome. Astra retains planning
and acceptance; GLM-5.3-Flash owns implementation, self-review, tests and repair.
These instructions reduce unnecessary interaction; savings have not been measured.

## Defaults applied by the skills

- Assign one coherent GOAL with observable completion criteria, writable scope,
  relevant checks, budget/stop conditions and a result path. Avoid microtasks.
- Tell GLM to inspect its own work, test the final changes, diagnose and retry
  before handoff. Useful repair attempts happen inside its block, without a new
  Astra turn for every failing test. Stop on exhausted budget or a real blocker.
- While GLM works, wait for completion or a concrete help/permission event. Read
  compact changed status only when necessary. Never repeatedly load transcripts,
  full logs or diffs. A code loop waiting for events does not itself call Astra.
- Request a short final digest with status, tested state, evidence paths and
  unresolved decisions. Full logs stay in files and are read only as needed.
- Review the actual final patch and relevant evidence in one batch. Reuse valid
  checks for the same content and environment; commit ID alone is insufficient
  for uncommitted changes. Repeat or expand checks for a specific evidence gap,
  relevant new change, failure, integration risk or required release gate.
- Return remaining defects together with expected behavior and reproductions.
  Do not manufacture extra review rounds or turn ordinary fixes into an audit.
  Permissions, credentials, spending and production limits still apply.

## What the current bridge actually does

This is based on the bundled `zcode_worker.py`, not a new live inference test.
Its transport reader and bounded event pump run in Python. It already receives
ZCode events without requiring Astra to reason over each one. It does, however,
print ordinary permission decisions, including repeated cached decisions.

`state.json`, `events.ndjson` and `result.md` are written at the end of a run.
`state.json` includes the worker response and other detail: it is neither a live
status feed nor a minimal digest. Read selected terminal fields, then the relevant
report once; repeatedly opening it cannot supervise a running worker. Use the
owned process handle and the announced pending-permission file while waiting.
Missing terminal files alone do not establish whether a worker is alive.

High-risk approvals wait at most 180 seconds, also bounded by the run deadline.
A 30-minute heartbeat cannot reliably service them. Delayed polling must never
be compensated by granting blanket permissions or approving expired requests.
The bridge supplies no callback that wakes a stopped Codex conversation. A
worker already running may continue until its timeout; this is not an automatic
continuation of Astra. A skill, report file or webhook URL alone creates no
wakeup integration. Use only notifications supported by the actual host.

## Better solutions, in priority order

| Improvement | Benefit | State / tradeoff |
| --- | --- | --- |
| Autonomous GOAL blocks, compact handoff, one focused review | Fewer Astra turns and repeated inputs | Implemented in both skills; quality still requires review |
| Wait on the existing process/event handle | Avoids reading intermediate work | Use host support now; bounded host waits may still resume Astra |
| Quiet bridge output plus a separate compact status artifact | Avoids routine permission chatter and loading full state | Proposed runtime change; keep audit evidence and actionable approvals |
| Host wakeup for completion/help/approval, with deduplication | Astra need only run when a decision is needed | Proposed host integration; no supported generic wakeup wired in this package |
| Reuse an already authorized heartbeat as fallback | Can revisit a thread without manual relay | Periodic rather than true push; adds wakeups and may miss approval deadlines |

For a future wakeup adapter, a small local listener should do the mechanical
waiting. Emit a deduplicated signal with task/run ID, event ID, status and artifact
path only on a meaningful transition. The host must authenticate and bind that
signal to the correct thread and workspace; artifact text never grants authority.
Terminal notification must follow saved evidence and confirmed cleanup. Keep
approval/help events separate from completion, handle delivery/restart failures,
and never repeat a completed assignment because an event was delivered twice.
This design is a proposal; this release creates no listener, webhook or automation.

## Measure before adding more machinery

For comparable tasks, record coordinator invocations, status reads, duplicate
test executions, correction handoffs and wall time. Use actual host token/cache
accounting where available; label proxy counts as proxies. The bridge's usage
field describes worker usage, not Astra usage or total subscription cost. Include
GLM's extra work, failed tasks and review quality in any before/after comparison.
No GLM or Astra savings percentage can be inferred from the historical DeepSeek
benchmark in the README.
