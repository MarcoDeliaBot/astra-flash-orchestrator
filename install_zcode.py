#!/usr/bin/env python3
"""Preview/install the native ZCode GLM workflow using the selected session model."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required. No packages or settings were changed.")
sys.dont_write_bytecode = True

from install import BUNDLE, SetupError, apply_changes, contents, no_symlinks, undo_files

SKILL = "glm-orchestrator"
BUILDER = "glm-orchestrator-builder"


def config_directory(requested: str | None) -> Path:
    # Custom subagents currently have user scope only in ZCode.
    path = Path(requested).expanduser() if requested else Path.home() / ".zcode"
    path = Path(os.path.abspath(path))
    no_symlinks(path)
    return path


def target_files(directory: Path) -> set[Path]:
    return {directory / "skills" / SKILL / "SKILL.md",
            directory / "agents" / f"{BUILDER}.md"}


def plan_changes(directory: Path, replace: bool = False) -> list[dict]:
    no_symlinks(directory)
    instructions = contents(BUNDLE / "WORKER-INSTRUCTIONS.md")
    if instructions is None:
        raise SetupError("Missing shared worker instructions; use the complete repository.")
    worker = instructions.decode("utf-8").strip().replace("Astra", "coordinator")
    changes = []
    for path in sorted(target_files(directory)):
        source = BUNDLE / "zcode" / path.relative_to(directory)
        original = contents(source)
        if original is None:
            raise SetupError("Incomplete ZCode adapter; use the complete repository.")
        text = original.decode("utf-8").replace("\r\n", "\n")
        after = text.replace("{{WORKER_INSTRUCTIONS}}", worker).encode("utf-8")
        before = contents(path)
        if before == after:
            continue
        if before is not None and not replace:
            raise SetupError(f"Different content already exists at {path}. Review it, then use --replace to back it up and update it.")
        changes.append({"path": path, "before": before, "after": after,
                        "mode": path.stat().st_mode & 0o777 if before is not None else 0o600})
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zcode-home", help="explicit user configuration directory; default ~/.zcode")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="write changes; otherwise preview only")
    action.add_argument("--check", action="store_true", help="verify installed file contents offline")
    parser.add_argument("--replace", action="store_true", help="back up and update conflicting package files")
    parser.add_argument("--undo", type=Path, metavar="RECEIPT", help="preview undo; combine with --apply to restore")
    args = parser.parse_args()
    if args.check and (args.undo or args.replace):
        parser.error("--check cannot be combined with --undo or --replace")
    try:
        directory = config_directory(args.zcode_home)
        if args.undo:
            undo_files(args.undo.expanduser().absolute(), directory, args.apply,
                       tree=None, files=target_files(directory))
            return 0
        changes = plan_changes(directory, args.replace)
        if args.check:
            if changes:
                raise SetupError("ZCode adapter is missing or differs from this bundle. Preview installation first.")
            print("Installed files match this bundle. Runtime loading and model routing remain unverified.")
            return 0
        print(f"ZCode home: {directory}")
        print(f"Skill: ${SKILL}; native worker: {BUILDER}")
        print("Worker inherits the primary session's selected model and reasoning effort.")
        print("Runtime loading and model routing remain unverified; no model request is made.")
        for change in changes:
            print(f"{'UPDATE' if change['before'] is not None else 'CREATE'} {change['path']}")
        if not args.apply:
            print("Preview only. No files changed. Add --apply to install.")
            return 0
        receipt = apply_changes(changes, directory, {})
        print(f"Installed. Undo receipt: {receipt}" if receipt else "Already installed; no changes needed.")
        print("Settings, credentials, AGENTS.md and unrelated skills/agents were not changed.")
        print("Refresh Settings -> Skills, check Settings -> Subagents, then start a new ZCode Agent session.")
        return 0
    except (SetupError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else f"Local installation error ({type(exc).__name__}); inspect locally."
        print(f"INSTALL FAILED: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
