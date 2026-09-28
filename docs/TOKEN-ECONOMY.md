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

## Event-driven waiting

The desktop integration now has a concrete implementation: see
[event-driven operation](EVENT-WAKEUP.md) for `start`, same-chat messages and
recovery. After confirmed launch Astra ends its turn. The local bridge handles
ordinary progress and only sends an approval or terminal notification. Compact
`status` is for recovery or an explicit request, not periodic coordinator reads.

Detailed `state.json`, `events.ndjson` and `result.md` remain final evidence;
do not load them repeatedly while the worker runs. The separate compact status
does not replace final review or establish process liveness by itself.

This requires the installed Codex desktop messaging capability and an available
app. A skill alone cannot wake an unavailable host. Accepted notification delivery
is not proof that Astra has reviewed the work, and an ambiguous send is not
automatically repeated. A 30-minute heartbeat cannot reliably service a permission
window of at most 180 seconds. Installation creates no additional monitor.

## Measure before adding more machinery

For comparable tasks, record coordinator invocations, status reads, duplicate
test executions, correction handoffs and wall time. Use actual host token/cache
accounting where available; label proxy counts as proxies. The bridge's usage
field describes worker usage, not Astra usage or total subscription cost. Include
GLM's extra work, failed tasks and review quality in any before/after comparison.
No GLM or Astra savings percentage can be inferred from the historical DeepSeek
benchmark in the README.
