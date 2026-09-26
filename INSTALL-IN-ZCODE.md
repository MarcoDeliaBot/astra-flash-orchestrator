# Astra in Codex orchestrates; GLM-5.3-Flash in ZCode implements

The roles are fixed:

| Application | Model | Responsibility | Skill |
| --- | --- | --- | --- |
| Codex | Astra | Plan, assign, review and accept | `astra-glm-orchestrator` |
| ZCode Agent | GLM-5.3-Flash | Implement, test, debug and report | `glm-worker` |

GLM does not orchestrate, create a second worker, or accept its own work.
You select the model in each app. Skill files do not switch models or prove
model identity. No API credentials are needed by this file-based adapter; each
app uses its existing account connection.

**The handoff is manual.** Astra writes an assignment in the shared project;
you send the prepared instruction in ZCode. GLM writes its result; you tell Astra
to review it. Installing skills does not create an automatic connection between
the apps. For native automatic delegation inside Codex, use the separate
[Router integration](INSTALL-IN-CODEX.md) with an already-configured GLM route.
That integration is not ZCode control and does not reuse a ZCode login implicitly.

## Install

Requirements: Codex with Astra selected, ZCode Agent with GLM-5.3-Flash selected,
the same project checkout accessible in both apps, and Python 3.11+ for setup.
This adapter uses ordinary skills; it does not require ZCode custom subagents.
See the official [ZCode skills documentation](https://zcode.z.ai/en/docs/skill).

From the complete repository folder, run:

```sh
python -B install_zcode.py
python -B install_zcode.py --apply
python -B install_zcode.py --check
```

The first command previews; the second writes these two definitions plus an
undo receipt under `~/.zcode/astra-flash-install-backups/`:

- `~/.codex/skills/astra-glm-orchestrator/SKILL.md` for Codex (or under
  `CODEX_HOME/skills` when configured). This avoids ZCode's shared skill discovery.
- `~/.zcode/skills/glm-worker/SKILL.md` for ZCode.

Existing files with different contents require an explicit `--replace` after
review and are backed up. Repeating the same installation is a no-op. Settings,
credentials, AGENTS.md and unrelated skills/agents are preserved. Use `--home`
for the default user home and legacy lookup, `CODEX_HOME` for a custom Codex
directory, and `--zcode-home` for ZCode's configuration directory. Keep these
locations consistent for check and undo.
The check verifies file contents and absence of obsolete coordinator definitions,
not app discovery, model selection or successful inference. Setup makes no model
requests and does not submit a task in either app.

## Upgrade from the former GLM coordinator

Version 1.3.0-glm.2 installed `glm-orchestrator` and
`glm-orchestrator-builder`, incorrectly making GLM the coordinator. That design
is withdrawn. Upgrade with:

```sh
python -B install_zcode.py --migrate-legacy
python -B install_zcode.py --migrate-legacy --apply
python -B install_zcode.py --check
```

Migration creates the two correct skills and backs up/removes only the old
`~/.zcode/skills/glm-orchestrator/SKILL.md` and
`~/.zcode/agents/glm-orchestrator-builder.md`. Known file hashes are required;
customized old definitions stop the entire change for manual reconciliation.
Other files in those directories are preserved. `--replace` does not override
the protection for customized obsolete definitions.

The same migration also moves the known 1.3.0-glm.3 Astra skill out of the shared
`~/.agents/skills/astra-glm-orchestrator/SKILL.md` location into Codex's own skill
directory. This prevents its automatic discovery in ZCode. A customized shared
copy is preserved and requires manual reconciliation.

## Start using it

1. In ZCode **Settings -> Skills**, click **Refresh** and enable `glm-worker`.
   After an upgrade, refresh **Subagents** too: the old builder should disappear.
2. Open fresh conversations in both apps against the agreed project checkout;
   old conversations can retain old instructions. Keep **Astra in Codex** and
   **GLM-5.3-Flash in ZCode**. If Codex does not discover its new skill, reopen it.
3. In **Codex**, select `$astra-glm-orchestrator` and describe your objective.
   Astra prepares the plan and a bounded `TASK.md` plus a ZCode instruction.
4. In **ZCode**, send that instruction using `$glm-worker` and the exact task path.
   GLM implements the assignment and writes `RESULT.md` for Astra.
5. Tell **Astra in Codex** that the report is ready. Astra reviews the actual
   changes and checks, then writes `REVIEW.md` with acceptance or corrections.

Example in Codex:

```text
$astra-glm-orchestrator

Obiettivo: [descrivi la funzionalita].
Tu Astra sei l'orchestratore. Prepara il piano e l'incarico per
GLM-5.3-Flash in ZCode; dammi il messaggio da inviare all'operaio.
Conserva tu la revisione e l'accettazione finale.
```

Example in ZCode, after Astra has created the real file:

```text
$glm-worker

Esegui l'incarico di Astra in "[percorso assoluto del TASK.md]".
Usa GLM-5.3-Flash. Scrivi la consegna nel RESULT.md indicato.
Non orchestrare e non approvare il tuo lavoro: la revisione spetta ad Astra.
```

Use one writer in the shared checkout: Astra does not edit GLM's assigned paths
while execution is active. A result report must match task ID, revision and
baseline. Neither a file labeled "Astra" nor a worker's self-report proves
authorization, model identity or correct execution. Actual acceptance rests on
the brief, patch and verification evidence. Runtime execution, quality and cost
savings are not established by the offline test suite.

## Undo

Use the receipt path printed by the installer:

```sh
python -B install_zcode.py --undo PATH_TO_RECEIPT
python -B install_zcode.py --undo PATH_TO_RECEIPT --apply
```

Undo is restricted to the two new skill paths, the two legacy GLM definitions,
and the former shared Astra skill path. Undoing a migration restores retired
files and removes
newly created skills; use it only when that rollback is intended. Later user edits
or recreated legacy files block undo before restoration. Empty directories may
remain. No unrelated files or model/account settings are changed.
