# Choose the implementation model

For **Astra in Codex + GLM-5.3-Flash in the ZCode app**, use the
[two-app handoff guide](../INSTALL-IN-ZCODE.md). The roles stay fixed and the
handoff is manual. The routes below apply only to the separate native Codex
Router integration; a ZCode login does not configure those routes.

This fork keeps Astra responsible for architecture, task boundaries and final
acceptance. You can select DeepSeek V4.1 Flash, GLM-5.3 or GLM-5.3-Flash as the
implementation worker. A worker explores the repository, edits, tests and fixes
its assigned task; it is not a passive reference manual.

The skill and native role retain the names `astra-flash-orchestrator` and
`astra_flash_builder` for update compatibility. Here, "worker" means the model
pinned in the installed `routing.json`. Legacy plan fields such as
`max_flash_workers` and the logical `flash` assignee also refer to that worker;
they do not override the selected model.

## Codex routes

| Model | Provider | Exact `--worker-route` |
| --- | --- | --- |
| GLM-5.3 | Z.ai API | `zai-api/glm-5.3` |
| GLM-5.3-Flash | Z.ai API | `zai-api/glm-5.3-flash` |
| GLM-5.3 | Z.ai Coding Plan | `zai-coding/glm-5.3` |
| GLM-5.3-Flash | Z.ai Coding Plan | `zai-coding/glm-5.3-flash` |
| GLM-5.3 | Ollama Cloud | `ollama-cloud/glm-5.3` |
| GLM-5.3-Flash | Ollama Cloud | `ollama-cloud/glm-5.3-flash` |

These route names are documented by [Codex Router](https://github.com/duolahypercho/codex-router#models-and-authentication).
The models are documented by [Z.ai](https://github.com/zai-org/GLM-5).
This confirms the identifiers, not successful inference on your installation.
Account access, available routes and provider terms can differ.

List all supported routes, including the original six DeepSeek routes, without
reading local configuration or contacting a provider:

```sh
python -B install.py --list-worker-routes
```

Use an existing Python 3.11+ interpreter; `python3` is also suitable where it
points to that version. First configure the chosen provider separately in Codex
Router. Its catalog must contain the exact route with `multi_agent_version: v2`.
The installer never enables models, collects credentials or certifies a route.

For a first installation with GLM-5.3:

```sh
python -B install.py --worker-route zai-api/glm-5.3
python -B install.py --worker-route zai-api/glm-5.3 --apply
```

To change an existing installation to GLM-5.3-Flash, preview the replacement and
then apply the same selection:

```sh
python -B install.py --worker-route zai-api/glm-5.3-flash --replace
python -B install.py --worker-route zai-api/glm-5.3-flash --replace --apply
```

Use the same `--home`, `--codex-home` and `--profile` overrides on every command
when your installation requires them. Changing a model also changes its billing
route when the provider prefix differs. The worker's effort comes from the exact
catalog entry; Astra's model and effort are preserved. Existing installations
reuse their saved route when `--worker-route` is omitted; new ones still default
to direct DeepSeek. No automatic provider or model fallback is implemented.

Use the printed undo receipt to restore the previous installation. Undo restores
package files, not provider accounts or external Router settings.

After installation, fully reopen the host app. Run the installed doctor or pass
the exact route to the source copy:

```sh
python -B skill/astra-flash-orchestrator/scripts/doctor.py --worker-route zai-api/glm-5.3-flash
```

This is a static check. Verify host/router request metadata and actual tool/test
results during the first authorized useful task. A model's self-reported name,
an installation success or a passing offline test is not runtime verification.

## OpenCode

The existing OpenCode adapter already accepts an explicit `--worker-model`.
Choose the exact provider/model ID shown by your OpenCode installation and follow
[its installation guide](../INSTALL-IN-OPENCODE.md). Codex Router prefixes above
are not a promise that OpenCode uses the same IDs. Keep your chosen primary model
and independently verify the child session after installation.

## Evidence and limits

Offline tests cover installation of all six GLM routes, exact provider/model and
effort pinning, updates, model replacement and undo, missing routes, incompatible
catalogs, unchanged user configuration, and catalog changes during installation.
CI runs these checks on Linux and Windows. Symlink checks are explicitly skipped
only where creating their fixture is unsupported; normal checks still run.

No paid GLM inference or quality/cost comparison has been performed for this
fork. The upstream DeepSeek benchmark is historical evidence for a different
model and workload; it does not establish GLM savings or quality. Compare models
on the same tasks with acceptance checks and actual provider usage before making
performance claims.
