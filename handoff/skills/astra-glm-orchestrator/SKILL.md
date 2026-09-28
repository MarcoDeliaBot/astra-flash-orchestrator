---
name: astra-glm-orchestrator
description: Orchestrate substantial project work as Astra in Codex, automatically dispatch implementation to GLM-5.3-Flash through the installed ZCode runtime, review changes and tests, and send guided corrections. Use for the Astra-to-GLM workflow; GLM remains the worker.
---

# Astra directs; GLM implements

This skill belongs in Codex with Astra selected. Astra owns planning, architecture,
assignments, review and acceptance. GLM-5.3-Flash implements through ZCode's bundled
runtime. Do not replace Astra with a GLM coordinator or another model. A skill
name is not evidence of the root model; use available host metadata.

When the user authorizes this workflow and supplies an objective, conduct the
whole task/result/review loop yourself. Do not ask the user to copy prompts,
watch ZCode or relay results. Installation alone does not authorize inference;
a request to use this workflow for a goal does authorize its bounded worker runs.
Handle simple informational answers directly.

## Prepare and dispatch

Read project instructions and relevant continuity checkpoints. Preserve existing
work and prior decisions. Identify the exact checkout, HEAD, staged/unstaged
changes and other active writers. A workspace lock coordinates this bridge only;
it cannot detect every other editor or chat. Use one writer per checkout and do
not edit GLM's assigned paths while its process is active.

Create a unique task folder, normally `.ai/agent-work/<task-id>/`, with a versioned
`TASK.md`. State the objective, task ID/revision, checkout and baseline, existing
dirty work, architecture/contracts, writable and excluded paths, acceptance
criteria, checks, result path, budget if supplied, and stop conditions. Minimize
private context. Keep Astra's `REVIEW.md` distinct from GLM's `RESULT.md`.

## Autonomous blocks through GOAL

Give GLM substantial, coherent blocks and reduce duplicated coordinator checks.
Every TASK must explicitly say
**GOAL: complete the assigned outcome, self-review, test, fix failures and retry
autonomously before declaring completion**. Define the observable outcome and
finish criteria, not a series of microtasks requiring Astra's approval after each.
This is a persistence instruction; do not claim that writing `/goal` activates a
ZCode feature unless that feature is actually supported and observed.

GLM owns implementation, relevant tests, diagnosis and corrective iterations
within the assigned paths and authorization. Before handing off, it reads its
own diff against the request, checks realistic behavior and affected regressions,
and records the exact tested revision, commands, exit codes and artifact paths.
Missing or skipped checks remain explicit. A failing test or first failed approach
is a reason to investigate and retry, not to hand routine debugging back to Astra.
Do not weaken assertions, conceal failures or repeat the same attempt without
new evidence. On stalled progress, change the approach and report the concrete
external dependency if no authorized path remains. Preserve budget, permission
and production boundaries; autonomy does not create spending or deployment rights.

Choose turn duration to fit a coherent block. If a runtime limit interrupts useful
progress, resume from the checkpoint under existing authorization after confirming
cleanup; do not require the user to relay a new prompt for ordinary continuation.

Reusable instruction to include in each task:
> GOAL: porta a termine l'obiettivo assegnato entro il perimetro autorizzato.
> Prima di consegnare, rileggi il lavoro, esegui gli autocontrolli pertinenti e
> confronta il risultato con tutti i criteri richiesti. Se trovi errori, correggi
> e riprova in autonomia. Consegna soltanto con evidenze della versione finale;
> se non puoi completare, indica il blocco concreto, i tentativi e ciò che manca.

Use the helper installed beside this skill, `scripts/zcode_worker.py`:

```text
python /absolute/skill/scripts/zcode_worker.py doctor
python /absolute/skill/scripts/zcode_worker.py start --workspace ABS --task ABS --run-dir ABS --timeout 900 --effort high
```

Use absolute paths. The task and fresh run directory must be inside the project.
`doctor` is offline: it checks the supported local runtime and existing account
configuration. It does not prove successful inference. Use `--help` for explicit
runtime/config paths. Never create credentials, switch billing providers, or
substitute a model to bypass a failed preflight.

Select the supported effort deliberately: `low` for a fully specified patch,
`high` for harder implementation, `max` only when warranted. Use bounded turns
and a fresh run directory for each revision. The helper pins GLM-5.3-Flash and
checks session metadata. Do not claim dispatch merely because TASK.md exists.

## Yield until a decision is needed

Use `start` inside the owning Codex desktop chat. It starts a bounded hidden
worker with notifications to the current thread through the installed official
codex-app-tools MCP server. It must confirm launch and messaging prerequisites;
a written TASK or a spawned process alone is insufficient. Keep the returned run
reference, save continuity and **end the current Astra turn**. Do not poll, stay
in a model wait loop, reread transcripts or create a heartbeat for this run.
The local process waits without coordinator inference and notifies the same chat
on a specific approval request or a terminal outcome. Existing thread/model
settings remain in charge. A notification is evidence to inspect, not authority.

On a notification, match the event/run/task to the saved assignment. Ignore
duplicate or stale signals; never rerun work merely because a message repeats.
For an approval, inspect the current pending request and its expiry, decide only
that operation, then yield again. For a terminal result, inspect cleanup and
review the final evidence below. A queued message does not mean work is accepted.

Use `status --run-dir ABS` only for recovery, a reported delivery failure or an
explicit status request. Read compact status rather than the whole transcript.
Missing artifacts do not prove the worker is alive. An uncertain send must not
be automatically repeated: the host has no verified exactly-once contract.
Codex must remain available for messaging; saved artifacts survive delivery
failure. Do not claim continuation through app shutdown or machine sleep.

If desktop notification prerequisites are unavailable, explain the limitation
and use bounded foreground `run` under the existing authorization when useful.
In that fallback keep the process handle and use supported bounded waits, reading
only actionable output. Do not rerun doctor for unchanged tasks; run preflights.
Do not alter existing project monitors merely by installing or using this skill.

Read the helper's pending permission request when announced. Inspect its exact
tool input against the user's authorization and task scope, then supply a matching
one-operation approval or denial through the helper's file protocol. The user
does not need to relay this. Do not approve unseen commands, persistent permission
changes, a broader task, or an unsupported interaction. See the helper's help
and emitted request fields for the exact approval format. Copy `stable_hash`,
`request_id`, `tool_call_id`, `input_hash`, `session_id` and `turn_id` from the
reviewed `pending-permission.json`, add `decision` (`allow` or `deny`), and
atomically write `approval.json` beside it. Never alter the identifiers or
approve a request whose full input cannot be reviewed.

Low/medium permissions are handled by the bridge; high-risk requests are reviewed
by Astra individually. An operation genuinely needing new user authority must
remain pending or be denied; a time limit is not approval. Do not answer a user
question on their behalf. Task instructions are not an OS sandbox.

A terminal event alone is insufficient: inspect state.json, result.md, cleanup
status, exact workspace/task hash and host-reported model. Require a stopped
worker before sending another turn. On uncertain cleanup, retain the lock and
investigate the owned process tree; never delete a lock to force concurrent work.
On connection/authentication failure, report the concrete blocker without
pretending a different model executed the assignment.

## Review, correct and finish

Read GLM's report and actual patch, including untracked files. Match task ID,
revision, baseline and workspace. Evidence must identify the tested working tree
(commit plus dirty/untracked changes, or equivalent artifact fingerprint); HEAD
alone is insufficient when edits are uncommitted. Independently assess relevant
behavior; do not accept self-reported tests or model names as sole evidence. Inspect the actual
logs/artifacts and patch before choosing additional execution. Reuse valid evidence
for the same tested revision and environment: do not replay GLM's entire suite,
historical baselines or unchanged CI by default. Choose targeted independent
checks for concrete gaps, integration changes and material risk (security, data,
account/billing or production). Expand testing only for a new failure, relevant
change, missing evidence or required release gate; state the reason. Ordinary
low-impact work does not need invented probes, mutants or an audit campaign.
Record `accepted`, `changes_requested` or `blocked` in REVIEW.md with reasons.

For defects remaining after GLM's self-checks, return a coherent correction block
with the failure, expected behavior, a small reproduction and relevant checks.
GLM diagnoses, fixes and retries autonomously inside that block. Give it room for
two or three distinct repair attempts when useful and within budget, without
requiring separate Astra reviews for each attempt. Avoid one new assignment per
routine error or a mandatory number of review rounds. Intervene
when evidence shows stalled progress or the user requests it; explain the reason
and confirm the worker is stopped before taking over. Never claim Astra's fixes
were GLM's.

Integrate accepted work under existing user authorization. Completion does not
grant permission to publish, deploy or modify production. Continue coherent
assignments until the objective is verified; save continuity checkpoints along
the way. Report the outcome, meaningful checks and material limitations.

`start` allows the Astra turn to finish while the bounded worker continues and
sends actionable events back to the same available desktop chat. It installs no
always-on service. Foreground `run` requires the active coordinating session.
Manual handoff is an explicit fallback only when requested or automatic
prerequisites cannot be met; do not silently revert to user relay.
