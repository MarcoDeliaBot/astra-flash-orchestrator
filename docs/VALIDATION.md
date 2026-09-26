# Validation evidence

## Community fork: 1.3.0-glm.1

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

No live GLM inference, end-to-end client routing, model quality or cost comparison
has been performed for this fork. The historical evidence below belongs to the
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
