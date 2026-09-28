# Validation evidence

## Event-driven desktop wakeup: 1.5.0-glm.1

Checked September 28, 2026 on Windows with Python 3.11.9, Node 24.12.0 and
the installed official `codex-app-tools` MCP server 0.1.5.

- The full local suite ran **211 tests: 206 passed, five skipped** for Windows
  symlink privileges. The 22 new notification tests exercise synthetic MCP
  receipts, exact-thread matching, bounded errors, durable deduplication,
  permission ordering, startup confirmation/cancellation and helper installation.
  The first CI run exposed a deduplication test relying on the local desktop
  environment; its setup now supplies synthetic host metadata and a fake server.
  Consult the published commit's Actions run for Linux/Windows matrix results.
- The new client's read-only preflight recognized the real installed host tool.
  An earlier actual self-notification arrived while the owning chat was active.
- For the final integration check, `start` launched a bounded synthetic ZCode
  process and returned `running` before completion. Astra saved a checkpoint and
  ended its turn. After about 45 seconds, the real desktop notification resumed
  that same idle chat. Astra matched the event, task and workspace, verified the
  delivered receipt, stopped process and released workspace lock. Only one
  terminal event was sent. This checks the actual background-to-chat route,
  without making a model inference request in the wakeup fixture.

GLM implemented the initial runtime changes and tests in bounded assignments.
After stalled correction attempts, Astra fixed the remaining receipt, ledger,
startup, cleanup and test-race defects. The offline suite and live desktop probe
apply to the final code, not just GLM's intermediate delivery.

This proves idle resumption in the tested desktop installation while Codex and
the computer remain available. It does not establish delivery through shutdown,
sleep, host failure, or every future app version. Permission-event delivery was
tested synthetically; real timed approval resumption and token savings have not
been benchmarked. A host receipt means accepted messaging, not accepted work.
The real GLM execution evidence below belongs to the earlier bridge release.
Private task paths, messages, account data and logs are excluded from distribution.

## Token economy instructions: 1.4.1-glm.1

Checked September 28, 2026. Changes are limited to the two ZCode workflow skills,
usage/analysis documentation and release metadata; bridge/installer code is
unchanged. Both skills pass the official format validator. Focused existing
suites ran 30 tests: 28 passed, two Windows symlink fixtures skipped (24 installer
and six release tests). See the commit's CI for the full platform matrix.

The installed coordinator's local GOAL/evidence-reuse rules were incorporated
into the public skill, and the worker received matching self-check/retry and
compact-report instructions. Runtime claims in [token economy](TOKEN-ECONOMY.md)
were checked against the existing bridge source. No new paid inference, token
benchmark, notification adapter or always-on scheduler was run or implemented.
The older live execution evidence below is historical, not a live test of this
policy update. Actual savings and policy adherence remain unmeasured.

## Automatic ZCode bridge: 1.4.0-glm.1

Checked September 27, 2026 on Windows, Python 3.11.9, Node 24.12.0 and the
installed ZCode CLI 0.16.9.

- The repository suite ran **189 tests: 184 passed, five skipped** because this
  local Windows account cannot create their symlink fixtures. The bridge's
  Windows junction and real process-tree tests ran. CI also covers Linux and
  Windows with Python 3.11/3.13; consult the commit's Actions run for its results.
- The example plan passes structural validation. Both role skills passed the
  official skill-format validator; that check does not prove model execution.
- Offline bridge tests use synthetic configuration and Python child processes
  speaking the observed NDJSON protocol. They cover event ordering, routing
  mismatch, denied/stale permissions, complete approval input, malformed/overflow
  transport, failed turns, timeouts, missing metadata, private-value redaction,
  path containment, locks and process cleanup. No account or Node installation
  is required for these tests.
- The offline doctor passed against the actual local ZCode installation. This
  establishes prerequisites only.

### Real task, review and correction

Astra dispatched a bounded assignment through the production bridge to
`account:zai-individual-coding-plan / GLM-5.3-Flash`, using `low` effort, in an
isolated Git fixture. The worker wrote a Decimal-based total function, unit tests
and a result report. Astra independently found loss of precision for
`['1e28', '0.01', '-1e28']`, wrote a review with expected results and sent the
correction assignment through the bridge without user prompt relay.

The corrected run returned `ready_for_review`, with a matching task hash,
validated initial/final workspace and model metadata, a successful terminal
event, no remaining interaction, confirmed process cleanup and lock release.
Astra independently reran **12 worker tests** and checked five basic totals,
five rejected values, and five precision cases under three ambient contexts.
All passed. A model's self-reported identity was not used as routing evidence.

Initial development runs exposed a runtime-preferences exchange and were marked
blocked. The adapter now handles that versioned exchange explicitly, including
its occurrence before the create acknowledgement, and disables automatic user
answers. The successful correction run took about five minutes. This is a small
integration exercise, not a throughput or quality benchmark.

GLM produced the bridge's initial implementation and installer. Astra supplied
two guided bridge correction cycles, then fixed residual protocol, cleanup and
test-fixture defects. Private transcripts, credentials, local paths and run
artifacts are excluded from the release.

### Limits

Live execution is verified only for the supported Windows installation and
existing Coding Plan configuration. Live macOS/Linux execution, alternate ZCode
versions/accounts, the separate native Router GLM routes and cost savings are
unverified. The bridge runs during an active Codex conversation; it installs no
always-on service. File checks and tests do not establish future provider access
or correctness of a new worker task. Astra must review every delivery.

## Historical community fork: 1.3.0-glm.1

Checked September 26, 2026 on Windows with Python 3.11.9. The offline suite
covers the GLM routes, saved model/provider selection, effort, replacement/undo,
installed doctor, and catalog changes before writes. Tests use temporary
synthetic homes and do not make inference requests.

The upstream baseline ran 77 tests: 73 passed and four could not create symlink
fixtures because the Windows account lacks that privilege. This fork reports
that platform limitation as an explicit skip, preserving the real symlink checks
for environments that support them. See the test output and GitHub Actions for
the current total and each platform's outcome.

[CI run 36271005994](https://github.com/MarcoDeliaBot/astra-flash-orchestrator/actions/runs/36271005994)
passed all **86 tests in each of four jobs**: Linux and Windows, each with Python
3.11 and 3.13. This includes the real symlink tests without skips. The example
plan and 46-file release inventory also passed in every job. Release inventories
use LF text and explicit portable path ordering across platforms. Consult the
latest commit's Actions run for subsequent revisions.

At that revision, no live GLM inference, end-to-end client routing, model quality
or cost comparison had been performed for this fork. The historical evidence below belongs to the
upstream project and must not be interpreted as validation of GLM workers.

## Historical upstream evidence

Unreleased candidate based on version 1.2.0. Checked September 20, 2026 on macOS
with Python 3.14.3.

## Verified

- 63 offline tests passed. Coverage includes installation dry runs, idempotence,
  original configuration preservation, scoped policy handling,
  profile/collision/symlink checks, URL validation, fake-secret redaction,
  generated role TOML, rollback, guarded undo, plan validation and release-file
  filtering.
- Every documented V4.1 Flash route is accepted only when explicitly selected or
  preserved from an existing valid package binding and advertised as
  `multi_agent_version: "v2"`. Tests cover OpenRouter role generation, remembered
  update behavior, unreviewed-route rejection, uncertified-route rejection and
  refusal to fall back from direct DeepSeek to an available alternate provider.
- Native `/v1` and capability-path Router configurations are accepted; non-loopback hosts and unsupported URL shapes are rejected.
- Existing backup-directory permissions are preserved.
- Backup files and caches are excluded from skill installation. Release tests also cover private artifact exclusion, symlink rejection and inventory changes.

All tests use synthetic configuration, temporary directories and a local HTTP fixture. They do not require a provider account or invoke model inference. Python 3.11 is the minimum supported syntax/runtime target, but this release's local suite was run on 3.14.3; other versions and operating systems have not been tested here.

## Prior local installation evidence

The preceding package revision was installed in a macOS Codex setup using an Astra root and the exact Flash worker route. Static configuration checks passed; root configuration and authentication bytes were preserved. Its optional unauthenticated local catalog GET was rejected. The public revision's installer is verified with synthetic homes; this report does not claim it was reapplied to that real installation.

## Still unverified

Actual delegated inference through the newly supported alternate providers,
native role loading for those routes in a fresh session, provider request
attribution, long-running build quality and cost savings remain unverified for
this candidate. A static report, a model catalog entry, or a worker naming itself
cannot establish these facts.

Validate real routing during the first authorized useful task, using host/router request metadata. Do not run an extra paid test as part of installation, and do not publish raw private logs or local configuration as evidence.
