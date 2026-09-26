# Use the workflow entirely inside ZCode

This adapter uses **GLM as both coordinator and implementation worker**. It
preserves the plan -> implement -> review workflow without requiring Astra or
Codex Router. It uses the model/account already selected in ZCode; the installer
does not inspect account settings or request credentials.

The native worker inherits the primary session's model **and reasoning effort**.
Select **GLM-5.3-Flash** in ZCode to use it in both roles. Changing the primary
model also changes subsequent worker calls. This is intentional inheritance,
not a model pin. Separate contexts do not provide independent model judgment.

## Install

Requirements: ZCode Agent with custom subagents available, and Python 3.11+.
Custom subagents are a user-level beta feature; verify availability in your app.
Use this adapter for ZCode Agent, not a different agent framework running in the
same app. See the official [skills](https://zcode.z.ai/en/docs/skill) and
[subagents](https://zcode.z.ai/en/docs/subagents) documentation.

From the complete repository folder, run:

```sh
python -B install_zcode.py
python -B install_zcode.py --apply
python -B install_zcode.py --check
```

The first command previews; the second writes only these two definitions plus
an undo receipt:

- `~/.zcode/skills/glm-orchestrator/SKILL.md`
- `~/.zcode/agents/glm-orchestrator-builder.md`

Existing files with different contents require an explicit `--replace` after
review and are backed up. Repeating the same installation is a no-op. Settings,
credentials, AGENTS.md and other skills/agents are preserved. If needed, pass
`--zcode-home PATH` consistently to select another user configuration directory.
The check verifies file contents only; it does not prove runtime loading or
successful inference. No model request is made during installation or testing.

## Start using it

1. In **Settings -> Skills**, click **Refresh** and check that `glm-orchestrator`
   is enabled.
2. In **Settings -> Subagents**, check that `glm-orchestrator-builder` is enabled
   with **Inherit default** for its model.
3. Start a **new ZCode Agent session** in the project you want to work on and
   select **GLM-5.3-Flash**. Existing sessions do not reload custom agent files.
4. Type `$`, select **glm-orchestrator**, and describe the task. Do not invoke the
   builder directly for orchestration: the main conversation delegates to it.

Example in Italian:

```text
$glm-orchestrator

Nel progetto aperto, implementa [descrivi la funzionalita].
Leggi le istruzioni e i checkpoint del progetto, prepara il piano,
delega l'implementazione a glm-orchestrator-builder e verifica il risultato.
Mantieni GLM-5.3-Flash come modello selezionato e rispetta il lavoro esistente.
```

The coordinator handles planning, browser/UI checks and final review; the builder
handles code, tests and debugging. ZCode Browser Use is main-agent-only.
The first useful task is the opportunity to inspect the
host's worker metadata and actual results. No live GLM run or performance/cost
comparison is implied by the offline checks.

If the skill appears but the builder does not, check custom-subagent availability,
enablement and a new session. The skill reports a blocker instead of silently
substituting a different role or performing the delegated task itself.

## Undo

Use the receipt path printed by the installer:

```sh
python -B install_zcode.py --undo PATH_TO_RECEIPT
python -B install_zcode.py --undo PATH_TO_RECEIPT --apply
```

Undo only touches the exact two owned paths. It refuses to overwrite later user
edits or restore a receipt targeting other files. It can leave empty directories;
it does not delete unrelated files or change ZCode settings.
