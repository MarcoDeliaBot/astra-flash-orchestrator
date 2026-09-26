# Changelog

## 1.3.0-glm.3 - Restore Astra orchestration with a GLM worker

- Replace the all-GLM ZCode design with `astra-glm-orchestrator` in Codex and
  `glm-worker` in ZCode. Astra plans, assigns, reviews and accepts; GLM-5.3-Flash
  implements, tests and reports without delegation or self-approval.
- Make manual cross-app dispatch explicit through TASK.md, RESULT.md and
  REVIEW.md. Installing the skills creates no automatic bridge or model route.
- Add guarded migration of known former GLM coordinator definitions, backups,
  exact-path undo and rollback of partial migration failures. Preserve customized
  legacy files, credentials, model settings and unrelated skills.
- Update public setup guidance and native Codex examples to distinguish ZCode
  handoff from an existing Router route. Keep upstream integrations compatible.

## 1.3.0-glm.2 - Native ZCode adapter

Historical release; its all-GLM coordination design is withdrawn in 1.3.0-glm.3.

- Add a native `glm-orchestrator` skill and `glm-orchestrator-builder` subagent
  for using the plan, implement and review workflow entirely inside ZCode.
- Inherit the primary session's selected model and effort; GLM can coordinate
  and implement without Astra, Codex Router or another account connection.
- Add an offline installer with preview, exact-file check, replacement backups,
  idempotency and guarded undo; preserve settings, credentials and AGENTS.md.
- Include the adapter in release archives and add installation/undo regression
  coverage. Live inference and quality remain unverified.
- Keep ZCode Browser Use with the coordinator, respecting the host's
  main-agent-only boundary while delegating code and supported automated checks.

## 1.3.0-glm.1 - Community fork

- Add explicit GLM-5.3 and GLM-5.3-Flash worker routes through Z.ai API,
  Z.ai Coding Plan and Ollama Cloud; preserve the DeepSeek default.
- Add offline route listing and neutral worker instructions while retaining
  installed skill/role names and plan schema compatibility.
- Recheck the model catalog hash before installation, preserving the verified
  route and effort when another process updates the catalog.
- Add GLM install/update/replace/undo regression tests, Linux/Windows CI and
  explicit skips for OS accounts unable to create symlinks.
- Normalize distributed text to LF for cross-platform release checks.
- Document attribution and distinguish offline validation from unmeasured live
  GLM routing, quality and cost.

## Unreleased

- Document desktop-only OpenCode setup, make CLI model listing optional, and
  provide file-only installation without Python or a separately installed CLI.

- Add native OpenCode primary and builder agents, with an inherited primary
  model, explicitly pinned worker model and no nested Task delegation.
- Add an offline OpenCode installer with global/project destinations, preview,
  replacement backups, idempotent updates and guarded undo. Preserve provider
  configuration and credentials; retain the existing Codex installation path.
- Document OpenCode model selection, native Task continuation, shared-workspace
  ownership and the distinction between offline checks and verified inference.

- Support explicit, reviewed DeepSeek V4.1 Flash routes through OpenRouter,
  opencode Go, Command Code, Nous Research and Ollama Cloud while retaining the
  direct DeepSeek API as the default. Existing alternate-route installations
  reuse their validated routing binding on doctor checks and updates.
- Make provider choice fail closed: no catalog auto-detection, silent fallback,
  credential handling or paid certification during package installation.
- State that users enter API keys only through the Router's private local prompt,
  and prohibit installation agents from running `subagents certify`,
  `test-model --live`, smoke tests or other paid probes.
- Detect keys absorbed into `[agents]` by shape instead of by a list of
  anticipated top-level names. A stray key there is read by Codex as an agent
  name and stops the whole config loading, and the previous check only covered
  five names, missing the realtime base-URL keys that caused a real failure.
- Accept a legitimate `[agents]` table containing the recognized scalar settings
  and agent role tables, and reject an agent name whose value is not a table.
- Tell installation agents to select an existing Python 3.11+ interpreter rather
  than assume `python3`, and never to edit `config.toml` to make a check pass.

## 1.2.0 — Measured workflow and simpler installation

- Add a measured efficiency graphic, per-token price comparison and transparent
  benchmark methodology to the README.
- Document thin orchestration as the only supported delegated workflow, not a
  user-selectable mode, while retaining direct handling for trivial work and
  targeted high-assurance review.
- Reduce the normal terminal installation path to a guarded preview and apply;
  keep the offline test suite as optional local verification.
- Remove the unnecessary global subagent-default prerequisite. The installer
  now relies only on its named role's pinned worker settings and explicitly
  warns installation agents not to edit shared Codex model defaults.
- Include SVG documentation assets in release archives.

## 1.1.0 — Thin-root orchestration by default

- Keep Astra to a planning batch, one worker dispatch/wait, one batched acceptance review and the final response for normal phases.
- Make Flash responsible for in-scope repository discovery, implementation, testing, debugging and routine browser/visual QA.
- Remove progress polling, duplicate root investigation and ritual full-suite reruns from the default workflow.
- Consolidate review findings into one correction request and one default correction cycle.
- Retain additional Astra investigation and verification for concrete high-assurance risks.

## 1.0.2 — Native delegation readiness

- Refuse installation when Flash is present but not advertised for native subagents.
- Explain full host-app restart, cached catalogs, and Router commands that can trigger paid verification.
- Distinguish local route selection from runtime capability evidence.

## 1.0.1 — Public repository preparation

- Support the Router's native authenticated `/v1` base path alongside capability paths; retain loopback and URL-shape validation.
- Preserve permissions on an existing installation-backup directory.
- Add regression tests for direct routing, rejected URL shapes and permission preservation.
- Exclude local backup/cache artifacts from installed skill files and release archives.
- Replace private handoff/audit notes with public installation, troubleshooting, contribution and security documentation.
- Add reproducible release inventory and ZIP packaging with integrity checks.

## 1.0.0 — Initial package

- Native Flash builder role, Astra orchestration skill, scoped personal policy, dry run and guarded undo.
- Offline installation tests, configuration doctor, task templates and optional plan validator.
