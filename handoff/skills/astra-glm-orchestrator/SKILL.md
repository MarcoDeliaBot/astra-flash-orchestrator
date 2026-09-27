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

Use the helper installed beside this skill, `scripts/zcode_worker.py`:

```text
python /absolute/skill/scripts/zcode_worker.py doctor
python /absolute/skill/scripts/zcode_worker.py run --workspace ABS --task ABS --run-dir ABS --timeout 900 --effort high
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

## Supervise execution

Keep the process handle, poll at bounded intervals and give useful progress
updates. A quiet period may be model reasoning; do not launch a duplicate worker.
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
revision, baseline and workspace. Independently check relevant behavior; do not
accept self-reported tests or model names as sole evidence. Record `accepted`,
`changes_requested` or `blocked` in REVIEW.md with reasons.

For implementation defects, give GLM **two or three guided correction attempts**
before taking over its code. Default to three; two suffice when evidence shows
another repetition will not help. Each correction gives a concrete failure,
expected behavior, a small example or reproduction, and relevant checks. Create
a revised task referencing the previous result/review, and dispatch it yourself.
Infrastructure failures do not count as implementation correction attempts.

If guided corrections still fail, state why Astra is intervening and fix the
remaining issue after confirming the worker is stopped. User requests to take
over earlier override this default. Never claim that Astra's fixes were GLM's.

Integrate accepted work under existing user authorization. Completion does not
grant permission to publish, deploy or modify production. Continue coherent
assignments until the objective is verified; save continuity checkpoints along
the way. Report the outcome, meaningful checks and material limitations.

The loop runs during the active Codex conversation. It is not an always-on service
and does not continue after the session stops. Manual handoff is an explicit
fallback only when requested or when automatic prerequisites cannot be met;
explain that limitation instead of silently reverting to user relay.
