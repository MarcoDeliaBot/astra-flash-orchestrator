---
name: glm-orchestrator
description: Plan substantial features, refactors and migrations in ZCode, delegate implementation to a native GLM worker, then review the patch and test evidence. Use with the user's selected GLM model for multi-file project work. Skip trivial edits and explicit single-agent requests; worker children must not invoke this skill.
---

# GLM coordinates, implements and reviews in ZCode

The main ZCode conversation owns scope, architecture and acceptance. The native
`glm-orchestrator-builder` subagent owns implementation, tests and debugging.
Both use the user's selected model, normally GLM-5.3-Flash. Separate contexts
separate responsibilities; they do not provide an independent model's judgment.
This is the ZCode adaptation of Astra Flash Orchestrator. Astra is not involved.

## Confirm the host

Use ZCode Agent and its actual native Agent delegation tool. Confirm the custom
`glm-orchestrator-builder` role is available before delegating. It uses
`model: inherit`, so both its model and reasoning effort follow the primary
conversation. Switching the main model also switches subsequent worker calls.
Preserve the user's model choice and existing account configuration.

No Codex Router, API key, external agent CLI or separate provider connection is
needed by this adapter. Do not run the Codex installer or doctor from this skill.
Do not infer runtime routing from the worker's own model-name claim. On the first
useful task, report any host-supplied worker model metadata and distinguish it
from the configured inheritance. Never invent routing evidence or cost savings.

If the role is unavailable, finish the plan and explain the setup blocker. The
user can check Settings -> Subagents and start a new session after installation.
Do not silently substitute another role, model or a single-agent implementation.

## Plan one coherent assignment

Read the project's instructions and continuity records. Preserve existing work
and use prior user decisions and authorizations. Planning-only requests remain
planning-only; handle trivial edits directly rather than creating a worker.

For substantial work, inspect enough repository context to establish the design
and acceptance criteria. Reuse an existing approved plan. Resolve consequential
interface, security, data and production decisions before dispatch; leave routine
implementation choices to the worker. Ask only about material missing decisions.

Capture the working directory and the actual starting state, including staged,
unstaged and untracked work. A commit alone is not a baseline for a dirty tree.
For a large project, maintain dependency-ordered phases using the repository's
planning convention, or `docs/agent-work/<feature>/`. A short task can use a brief
in the conversation. Do not create a second continuity system when one exists.

Give the worker a self-contained brief with:

- Objective, task ID and exact working directory.
- Relevant existing work, contracts and dependent results.
- Writable paths and any paths owned by another task.
- Observable acceptance criteria, relevant checks and completion evidence.
- A bounded scope and any real time, cost or retry budget supplied by the user.

Prefer an end-to-end feature slice over one assignment per file. The worker owns
the discovery, edit, test and fix loop within that contract.

ZCode Browser Use is main-agent-only: keep browser interaction and UI acceptance
with the coordinator. Delegate code and tests that the worker's tools support;
perform required browser checks after the worker returns. Respect any similar
host restrictions on desktop tools rather than asking the worker to bypass them.

## Delegate through ZCode

Invoke `glm-orchestrator-builder` through the native Agent tool using the actual
tool schema exposed by the session; do not invent a command or parameter name.
Use one implementation worker at a time in a shared checkout. The main agent
must not edit the worker's assigned paths while that assignment is active.
Separate context does not create a worktree or a filesystem sandbox.

Let the worker complete its task. Use native completion/continuation facilities
when exposed instead of repeated status requests. A timeout is not proof that
the worker failed. Do not start another writer for the same unfinished work.
Workers must not delegate again. Follow project continuity requirements and use
distinct checkpoint identifiers where multiple conversations share a project.

## Review and accept

Treat a worker report as ready for review. Inspect the actual changes relative to
the captured starting state, including new files and pre-existing edits. Review
specification compliance, correctness and relevant security risks together.
Check the reported test commands, outcomes and limitations; perform independent
targeted checks when evidence is missing or the risk warrants them. Do not rerun
a full suite merely to duplicate adequate evidence.

Consolidate corrections into one follow-up to the same worker when continuation
is available. Repeated failures require reassessing the cause and scope before
another attempt; never silently relax acceptance criteria or change models.
Accept only after the necessary checks and review succeed. Same-model review
can still miss correlated mistakes, so ground acceptance in code and evidence.

Integrate only accepted work with existing user authorization. Do not assume
that completing a phase authorizes a commit, push, deployment or production
migration. In a shared checkout the edits already exist; do not invent a merge.
Save the resumable state using the project's continuity procedure.

Report what changed, what actually passed, remaining limitations and the next
step if incomplete. Keep unverified routing and unexecuted checks explicit.
