#!/usr/bin/env python3
"""Install Astra coordination in Codex and a GLM-only worker skill in ZCode."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11+ is required. No packages or settings were changed.")
sys.dont_write_bytecode = True

from install import BUNDLE, SetupError, apply_changes, contents, digest, no_symlinks, undo_files

ORCHESTRATOR = "astra-glm-orchestrator"
WORKER = "glm-worker"
SHARED_COORDINATOR_HASH = "8af5a7964972c4addefaceb46b110a4ed363316bc3c7e407a904484321e6d66e"
# Published v1.3.0-glm.2 plus the initial local build. Never retire unknown edits.
LEGACY_HASHES = {
    "skills/glm-orchestrator/SKILL.md": {
        "8b8001d02cfab20fef30abf48748e3061727669e7bab38e3449524fbd4910b95",
        "b63f16a0f684ede1135066735c6d865d68eb887b88ec44007118b1e11d12ee15",
    },
    "agents/glm-orchestrator-builder.md": {
        "ff915ef1dd0d3dd3cc9bf30b154f5346ab5888f52a139082d9ac4c4ade64e9bb",
        "344b6284ab13cebecca75bcb4d2b1380f0e051e9e98a8c665800045351a68c51",
    },
}


def config_directory(requested: str | None) -> Path:
    path = Path(requested).expanduser() if requested else Path.home() / ".zcode"
    path = Path(os.path.abspath(path))
    no_symlinks(path)
    return path


def user_home(requested: str | None) -> Path:
    path = Path(requested).expanduser() if requested else Path.home()
    path = Path(os.path.abspath(path))
    no_symlinks(path)
    return path


def coordinator_file(home: Path) -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or home / ".codex").expanduser()
    return Path(os.path.abspath(codex_home)) / "skills" / ORCHESTRATOR / "SKILL.md"


def helper_file(home: Path) -> Path:
    return coordinator_file(home).parent / "scripts" / "zcode_worker.py"


def notify_file(home: Path) -> Path:
    return coordinator_file(home).parent / "scripts" / "codex_notify.py"


def target_files(home: Path, directory: Path) -> set[Path]:
    return {coordinator_file(home),
            helper_file(home),
            notify_file(home),
            directory / "skills" / WORKER / "SKILL.md"}


def legacy_definitions(home: Path, directory: Path) -> dict[Path, set[str]]:
    return {**{directory / name: hashes for name, hashes in LEGACY_HASHES.items()},
            home / ".agents" / "skills" / ORCHESTRATOR / "SKILL.md": {SHARED_COORDINATOR_HASH}}


def legacy_files(home: Path, directory: Path) -> set[Path]:
    return set(legacy_definitions(home, directory))


def plan_changes(home: Path, directory: Path, replace: bool = False,
                 migrate_legacy: bool = False) -> list[dict]:
    no_symlinks(home)
    no_symlinks(directory)
    requested = {
        coordinator_file(home):
            BUNDLE / "handoff" / "skills" / ORCHESTRATOR / "SKILL.md",
        helper_file(home):
            BUNDLE / "handoff" / "skills" / ORCHESTRATOR / "scripts" / "zcode_worker.py",
        notify_file(home):
            BUNDLE / "handoff" / "skills" / ORCHESTRATOR / "scripts" / "codex_notify.py",
        directory / "skills" / WORKER / "SKILL.md":
            BUNDLE / "zcode" / "skills" / WORKER / "SKILL.md",
    }
    changes = []
    for path, source in requested.items():
        original = contents(source)
        if original is None:
            raise SetupError("Incomplete handoff adapter; use the complete repository.")
        after = original.replace(b"\r\n", b"\n")
        before = contents(path)
        if before == after:
            continue
        if before is not None and not replace:
            raise SetupError(f"Different content already exists at {path}. Review it, then use --replace to back it up and update it.")
        changes.append({"path": path, "before": before, "after": after,
                        "mode": path.stat().st_mode & 0o777 if before is not None else 0o600})
    for path, known_hashes in legacy_definitions(home, directory).items():
        before = contents(path)
        if before is None:
            continue
        if not migrate_legacy:
            raise SetupError("Obsolete GLM orchestration or a shared Astra skill is installed. Review --migrate-legacy to back up and retire it before installing Codex-only Astra coordination.")
        if digest(before.replace(b"\r\n", b"\n")) not in known_hashes:
            raise SetupError(f"Obsolete definition has unknown or customized content: {path}. Preserve/reconcile it manually; --replace does not authorize deleting it.")
        changes.append({"path": path, "before": before, "after": None,
                        "mode": path.stat().st_mode & 0o777})
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="user home for legacy lookup and default HOME/.codex skill location; CODEX_HOME overrides the Codex directory")
    parser.add_argument("--zcode-home", help="explicit user configuration directory; default ~/.zcode")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="write changes; otherwise preview only")
    action.add_argument("--check", action="store_true", help="verify installed file contents offline")
    parser.add_argument("--replace", action="store_true", help="back up and update conflicting package files")
    parser.add_argument("--migrate-legacy", action="store_true", help="back up and retire known old GLM coordinator definitions")
    parser.add_argument("--undo", type=Path, metavar="RECEIPT", help="preview undo; combine with --apply to restore")
    args = parser.parse_args()
    if args.check and (args.undo or args.replace or args.migrate_legacy):
        parser.error("--check cannot be combined with --undo, --replace or --migrate-legacy")
    if args.undo and (args.replace or args.migrate_legacy):
        parser.error("--undo cannot be combined with --replace or --migrate-legacy")
    try:
        home = user_home(args.home)
        directory = config_directory(args.zcode_home)
        if args.undo:
            undo_files(args.undo.expanduser().absolute(), directory, args.apply,
                       tree=None, files=target_files(home, directory) | legacy_files(home, directory))
            return 0
        changes = plan_changes(home, directory, args.replace, args.migrate_legacy)
        if args.check:
            if changes:
                raise SetupError("Handoff adapter is missing or differs from this bundle. Preview installation first.")
            print("Installed skills and helper match this bundle; obsolete GLM and shared coordinator definitions are absent.")
            print("This check verifies installed file contents only; runtime dispatch and selected models remain unverified.")
            return 0
        print(f"Codex coordinator: {coordinator_file(home)}")
        print(f"Codex worker helper: {helper_file(home)}")
        print(f"Codex notify helper: {notify_file(home)}")
        print(f"ZCode home: {directory}")
        print(f"Orchestrator: Astra in Codex (${ORCHESTRATOR})")
        print(f"Worker: GLM-5.3-Flash in ZCode (${WORKER})")
        print("Keep Astra selected in Codex; the runtime helper explicitly selects GLM-5.3-Flash. This installer does not switch or authenticate models.")
        print("Automatic runtime dispatch becomes available when both apps are configured; this installer does not run doctor or inference and creates no configuration or credentials.")
        for change in changes:
            operation = "RETIRE" if change["after"] is None else ("UPDATE" if change["before"] is not None else "CREATE")
            print(f"{operation} {change['path']}")
        if not args.apply:
            print("Preview only. No files changed. Add --apply to install.")
            return 0
        receipt = apply_changes(changes, directory, {})
        print(f"Installed. Undo receipt: {receipt}" if receipt else "Already installed; no changes needed.")
        print("Settings, credentials, AGENTS.md and unrelated skills/agents were not changed.")
        print("Start a fresh Codex chat with Astra and invoke $astra-glm-orchestrator. The bridge starts the ZCode worker automatically.")
        return 0
    except (SetupError, OSError, ValueError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else f"Local installation error ({type(exc).__name__}); inspect locally."
        print(f"INSTALL FAILED: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
