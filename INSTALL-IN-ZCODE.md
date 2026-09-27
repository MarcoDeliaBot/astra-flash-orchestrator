# Astra directs GLM automatically

Give the objective to Astra in Codex. Astra prepares the assignment, starts
GLM-5.3-Flash through the ZCode runtime, waits for the result, reviews the patch
and tests, and sends corrections itself. No prompt copying between applications
is required in automatic mode.

| Component | Responsibility |
| --- | --- |
| Astra in Codex | Plan, assign, review, request corrections, accept and integrate |
| GLM-5.3-Flash in ZCode's bundled runtime | Implement, test, debug and report |
| Local Python bridge | Start the worker, check routing, collect evidence and stop it |

The bridge contains no second orchestrator model. Astra is this Codex
conversation; it can send multiple bounded assignments until the objective is
complete. After an implementation needs changes, Astra normally gives GLM up to
three corrective attempts with specific evidence and examples. Two can suffice
when the task warrants it. Only after those attempts fail does Astra take over
the remaining code. A missing runtime or expired access is an infrastructure
blocker, not a completed correction attempt.

## Requirements

- Codex with Astra selected and Python 3.11+.
- ZCode installed with its bundled CLI **0.16.9**, plus Node.js on PATH.
- An existing Z.ai Coding Plan connection in the supported local ZCode desktop
  configuration. The initial adapter supports the existing
  `builtin:zai-coding-plan` entry in `~/.zcode/v2/config.json` and its
  `GLM-5.3-Flash` catalog entry. It does not decrypt credential stores, log in,
  create API keys, change providers, or silently use a different model.
- A project checkout available locally to both processes.

The transport uses ZCode's bundled `app-server` protocol. That protocol is
version-sensitive; the adapter deliberately rejects unverified CLI versions.
Windows is the platform used for the real execution check. Cross-platform unit
tests do not establish live macOS/Linux support.

## Installation and updates

From the full repository, preview installation before applying:

```sh
python -B install_zcode.py
python -B install_zcode.py --apply
python -B install_zcode.py --check
```

When upgrading different existing package files, add `--replace` to preview and
apply. The installer backs up every replaced file and prints an undo receipt.
It installs the Astra skill and bridge under `~/.codex/skills/` (or
`CODEX_HOME/skills`) and the worker skill under `~/.zcode/skills/`. It preserves
model settings, credentials, AGENTS.md and unrelated skills. Installation and
the offline file check make no model requests.

Use `--home`, `--zcode-home` and `CODEX_HOME` consistently when customizing
installation paths. The runtime's `--zcode-home` is a separate invocation option
when the worker uses a nonstandard ZCode configuration directory.

In a new Codex chat, invoke the installed skill with the objective. In an existing
chat, ask Astra to reread the updated astra-glm-orchestrator/SKILL.md and continue
from the current project state. If the client keeps old instructions cached, open
a fresh chat against the same checkout and recover the continuity checkpoint. ZCode's
desktop chat does not need to remain open: the bridge starts the installed
runtime itself.

## Give Astra a goal

In Codex:

```text
$astra-glm-orchestrator

Obiettivo: [descrivi il risultato desiderato].
Tu Astra pianifichi e verifichi. Delega automaticamente l'implementazione
a GLM-5.3-Flash, controlla il risultato e invia tu i correttivi con esempi.
Fagli fare 2-3 tentativi di correzione prima di intervenire sul codice.
Continua fino al completamento verificato dell'obiettivo.
```

Astra reads the current project state and creates a versioned `TASK.md`, then
uses the helper below. These commands are for Astra or troubleshooting; the
user does not have to run them for every assignment.

```sh
python /absolute/path/to/astra-glm-orchestrator/scripts/zcode_worker.py doctor
python /absolute/path/to/astra-glm-orchestrator/scripts/zcode_worker.py run \
  --workspace /absolute/project \
  --task /absolute/project/.ai/agent-work/task-01/TASK.md \
  --run-dir /absolute/project/.ai/agent-work/task-01/attempt-01 \
  --timeout 900
```

The task brief states goal, exact checkout and baseline, existing dirty work,
allowed paths, contracts, checks, report path and stop conditions. A new attempt
directory preserves each execution's evidence. The bridge's completion status
means the worker returned for review. Astra accepts only after inspecting the
real changes and relevant checks, records `REVIEW.md`, and sends the next
revision automatically if necessary.

## Permission review by Astra

When the helper prints `HIGH_PERMISSION_REQUEST`, Astra reads the complete
`pending-permission.json` from that attempt directory, checks the exact tool
input against the authorized assignment, and writes `approval.json` there.
The approval copies `stable_hash`, `request_id`, `tool_call_id`, `input_hash`,
`session_id` and `turn_id` from that exact request and adds
`"decision": "allow"` or `"decision": "deny"`. Write to a temporary file and
rename it to `approval.json` so the helper cannot read a partial write.

The helper consumes a matching approval once and reuses it only for identical
retransmissions of that operation. Changed tool, risk or input is refused.
Oversized input, input that cannot be displayed completely, critical/unknown
requests and unsupported interactions block the run. An expired request is
never approval. The user does not need to operate this file protocol.

## Execution limits and evidence

One worker writes in a checkout at a time. Astra waits without editing the
assigned files. The bridge verifies the configured session model, bounds each
turn by a timeout, and handles process cleanup before releasing its lock.
Task scope is an instruction, not an operating-system sandbox. Existing tool
permissions still apply; high-risk or unsupported interactions return to Astra
for assessment rather than inventing a user answer.

The existing account credential is supplied in memory to the matching ZCode
runtime authentication request. It is not placed in command-line arguments,
reports or public source. Do not publish raw provider logs, private task briefs,
installation backups or worker artifacts. `.ai/` is ignored in this repository.

This runs while Astra's Codex session is active. It installs no recurring job or
always-on service, and cannot promise continued work after Codex stops or account
limits are reached. A project checkpoint lets a later session recover progress.
Delegating implementation does not grant new permission to publish, deploy or
change production systems.

## Manual fallback and legacy migration

Manual handoff remains available when explicitly requested or when runtime
prerequisites cannot be met. Astra writes the brief; the user sends
`$glm-worker` with its absolute path in ZCode and returns the result to Astra.
Do not claim automatic execution in that case.

Version 1.3.0-glm.2's `glm-orchestrator` and `glm-orchestrator-builder` made GLM
the coordinator and are withdrawn. `--migrate-legacy` backs up and removes only
known versions of those definitions, and the known shared Astra definition
previously installed under `~/.agents/skills/`. Customized legacy files are
preserved and reported. `--replace` does not override that protection.

## Undo

```sh
python -B install_zcode.py --undo PATH_TO_RECEIPT
python -B install_zcode.py --undo PATH_TO_RECEIPT --apply
```

Undo previews first, is limited to package-owned paths, and refuses to overwrite
later edits. Undoing a legacy migration restores retired definitions, so use it
only when that rollback is intended. Credentials and model settings are not
part of installation or undo.
