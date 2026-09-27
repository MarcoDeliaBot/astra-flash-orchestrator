# Security and privacy

This package installs agent guidance and a native role. Guidance, file ownership and Git worktrees are not operating-system security boundaries. Existing sandbox, approval and repository restrictions continue to apply.

Installation does not run inference. A later delegated task sends its selected context and tool outputs to the configured provider. Obtain the appropriate authorization for private repositories and minimize shared context.

For native Router integration, use its private local credential prompt.
For the ZCode bridge, use the existing supported ZCode account configuration;
never paste a provider key into assistant chat. A package install does not
authorize `subagents certify`, `test-model --live`, smoke tests or other paid
inference probes; missing route certification is reported as a prerequisite.

Never publish authentication files, API keys, private Router capability URLs, local model catalogs, installation receipts, instruction backups or unredacted task/provider logs. Synthetic test credentials in this repository are deliberately fake.

For a suspected vulnerability, use GitHub's private vulnerability reporting on this repository when the maintainer has enabled it. If unavailable, open a minimal issue requesting a private contact without exploit details, secrets or private logs. Do not use public issues to transmit sensitive evidence. No response-time guarantee is currently offered.

Useful reports include the package/client/Python version, affected behavior and a synthetic reproduction. Preserve security checks while investigating; do not disable Router authentication or approval controls to make a test pass.

## Automatic ZCode worker

The installer reads package files and installs skills plus a helper. It does not
read account credentials or run inference. An authorized `zcode_worker.py run`
reads the supported local ZCode credential into memory and supplies it only to
the matching runtime authentication request. It validates the expected endpoint,
provider, session, workspace and model, and does not write the credential to
arguments, reports or public files. No login, key creation or settings changes
are performed. The offline doctor checks configuration without model inference.

The bridge uses the version-sensitive ZCode app-server protocol, currently gated
to CLI 0.16.9. It uses build mode, not unrestricted/yolo mode. Astra reviews each
high-risk tool request against the user's authorized task and supplies a matching
one-operation decision. This does not grant authority for unrelated external
writes or publishing. Low/medium tool requests are allowed within the workflow;
path scope in the brief is guidance, not a filesystem sandbox. Per-run runtime
preferences disable automatic answers to user questions and persistent memory.
The bridge rejects unsupported user interactions; it does not impersonate the
user. These preferences do not alter the desktop account configuration.

Each turn uses a fresh evidence directory and one workspace lock. A failed or
uncertain cleanup retains the lock to prevent another bridge writer. Do not
force-remove it until the owned process tree has been inspected. Other editors
and manually launched ZCode sessions do not participate in this lock.

Task briefs, tool inputs, results and Git file names may themselves be private.
Keep runtime evidence outside published source and do not treat credential
redaction as removal of every possible project secret. No background service or
recurring automation is installed.
