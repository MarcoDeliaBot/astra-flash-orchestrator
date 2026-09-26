from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from symlink_support import create_symlink

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install
import install_zcode as zcode


class ZCodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict('os.environ', {'CODEX_HOME': str(self.root / '.codex')})
        self.env.start()
        self.config = self.root / '.zcode'
        self.config.mkdir()
        self.orchestrator = self.root / '.codex' / 'skills' / zcode.ORCHESTRATOR / 'SKILL.md'
        self.skill = self.config / 'skills' / zcode.WORKER / 'SKILL.md'
        self.settings = self.config / 'v2' / 'config.json'
        self.settings.parent.mkdir()
        self.settings.write_text('{"apiKey":"TEST_SECRET","model":"existing-model"}')
        (self.config / 'AGENTS.md').write_text('Preserve project continuity rules')
        (self.root / '.codex').mkdir()
        (self.root / '.codex' / 'config.toml').write_text('model = "existing-astra"\n')

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'install_zcode.py'),
                               '--home', str(self.root), '--zcode-home', str(self.config), *args], capture_output=True, text=True)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def apply(self):
        return install.apply_changes(zcode.plan_changes(self.root, self.config), self.config, {})

    def legacy_fixture(self):
        known = {}
        for name in zcode.LEGACY_HASHES:
            path = self.config / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ('Synthetic old definition: ' + name).encode()
            path.write_bytes(data)
            known[name] = {install.digest(data)}
        return known

    def migration(self):
        known = self.legacy_fixture()
        with patch.object(zcode, 'LEGACY_HASHES', known):
            changes = zcode.plan_changes(self.root, self.config, migrate_legacy=True)
        return install.apply_changes(changes, self.config, {})

    def test_preview_changes_nothing_and_needs_no_router_or_credentials(self):
        before = self.snapshot()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('TEST_SECRET', result.stdout + result.stderr)
        self.assertIn('Manual file handoff', result.stdout)

    def test_install_separates_host_roles_and_preserves_settings(self):
        before = self.snapshot()
        result = self.cli('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.snapshot()
        for path, data in before.items():
            self.assertEqual(data, after[path])
        self.assertEqual(self.orchestrator.read_bytes(),
                         (ROOT / 'handoff/skills/astra-glm-orchestrator/SKILL.md').read_bytes().replace(b'\r\n', b'\n'))
        self.assertEqual(self.skill.read_bytes(),
                         (ROOT / 'zcode/skills/glm-worker/SKILL.md').read_bytes().replace(b'\r\n', b'\n'))
        self.assertFalse((self.config / 'agents').exists())
        self.assertFalse((self.config / 'skills' / zcode.ORCHESTRATOR).exists())
        self.assertNotIn('TEST_SECRET', result.stdout + result.stderr)
        self.assertEqual(self.cli('--check').returncode, 0)

    def test_check_is_read_only_and_rejects_incomplete_or_edited_install(self):
        before = self.snapshot()
        self.assertEqual(self.cli('--check').returncode, 2)
        self.assertEqual(before, self.snapshot())
        self.apply()
        self.skill.write_text(self.skill.read_text() + '\nPersonal instructions\n')
        before = self.snapshot()
        self.assertEqual(self.cli('--check').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_repeat_install_is_idempotent(self):
        self.apply()
        before = self.snapshot()
        result = self.cli('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('no changes needed', result.stdout)
        self.assertEqual(before, self.snapshot())

    def test_conflict_requires_replace_and_undo_restores_original(self):
        self.orchestrator.parent.mkdir(parents=True)
        self.orchestrator.write_text('Personal agent instructions')
        before = self.snapshot()
        self.assertEqual(self.cli('--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())
        receipt = install.apply_changes(zcode.plan_changes(self.root, self.config, True), self.config, {})
        result = self.cli('--undo', str(receipt), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual('Personal agent instructions', self.orchestrator.read_text())
        self.assertFalse(self.skill.exists())

    def test_undo_preview_and_apply_preserve_unrelated_files(self):
        receipt = self.apply()
        unrelated = self.skill.parent / 'notes.md'
        unrelated.write_text('Personal notes')
        before = self.snapshot()
        self.assertEqual(self.cli('--undo', str(receipt)).returncode, 0)
        self.assertEqual(before, self.snapshot())
        result = self.cli('--undo', str(receipt), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.orchestrator.exists())
        self.assertEqual('Personal notes', unrelated.read_text())
        self.assertEqual(before['.zcode/v2/config.json'], self.settings.read_bytes())

    def test_undo_rejects_out_of_scope_target_without_partial_restore(self):
        receipt = self.apply()
        record = json.loads(receipt.read_text())
        record['files'][-1]['path'] = str(self.settings)
        record['files'][-1]['after_hash'] = install.digest(self.settings.read_bytes())
        receipt.write_text(json.dumps(record))
        before = self.snapshot()
        self.assertEqual(self.cli('--undo', str(receipt), '--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_undo_rejects_later_edits_without_partial_restore(self):
        receipt = self.apply()
        self.skill.write_text(self.skill.read_text() + '\nUser change\n')
        before = self.snapshot()
        self.assertEqual(self.cli('--undo', str(receipt), '--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_partial_install_rolls_back(self):
        changes = zcode.plan_changes(self.root, self.config)
        original_write = install.atomic_write

        def failing_write(path, data, mode=0o600):
            if path == self.skill:
                raise OSError('synthetic write failure')
            return original_write(path, data, mode)

        with patch.object(install, 'atomic_write', side_effect=failing_write), self.assertRaises(OSError):
            install.apply_changes(changes, self.config, {})
        self.assertFalse(self.orchestrator.exists())
        self.assertFalse(self.skill.exists())
        receipt, = (self.config / 'astra-flash-install-backups').glob('*/receipt.json')
        self.assertEqual(json.loads(receipt.read_text())['status'], 'rolled-back')

    def test_symlinked_skill_directory_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        create_symlink(self.config / 'skills', outside, directory=True)
        with self.assertRaises(install.SetupError):
            zcode.plan_changes(self.root, self.config)
        self.assertEqual([], list(outside.iterdir()))

    def test_default_destination_and_explicit_override(self):
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(zcode.user_home(None), self.root)
            self.assertEqual(zcode.config_directory(None), self.root / '.zcode')
            self.assertEqual(zcode.config_directory(str(self.root / 'custom')), self.root / 'custom')

    def test_legacy_install_requires_explicit_migration(self):
        self.legacy_fixture()
        before = self.snapshot()
        result = self.cli('--apply')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--migrate-legacy', result.stderr)
        self.assertEqual(self.cli('--check').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_known_legacy_files_are_backed_up_removed_and_undoable(self):
        receipt = self.migration()
        self.assertTrue(self.orchestrator.exists())
        self.assertTrue(self.skill.exists())
        for path in zcode.legacy_files(self.root, self.config):
            self.assertFalse(path.exists())
        self.assertEqual(self.cli('--check').returncode, 0)
        result = self.cli('--undo', str(receipt), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.orchestrator.exists())
        self.assertFalse(self.skill.exists())
        for name in zcode.LEGACY_HASHES:
            self.assertEqual((self.config / name).read_text(), 'Synthetic old definition: ' + name)

    def test_customized_legacy_file_blocks_entire_migration_even_with_replace(self):
        self.legacy_fixture()
        before = self.snapshot()
        result = self.cli('--apply', '--migrate-legacy', '--replace')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown or customized', result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_failed_second_removal_restores_first_and_new_skills(self):
        known = self.legacy_fixture()
        before = self.snapshot()
        with patch.object(zcode, 'LEGACY_HASHES', known):
            changes = zcode.plan_changes(self.root, self.config, migrate_legacy=True)
        original_unlink = Path.unlink
        failing_path = self.config / 'agents' / 'glm-orchestrator-builder.md'

        def fail_one(path, *args, **kwargs):
            if path == failing_path:
                raise OSError('synthetic removal failure')
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, 'unlink', fail_one), self.assertRaises(OSError):
            install.apply_changes(changes, self.config, {})
        after = self.snapshot()
        for path, data in before.items():
            self.assertEqual(data, after[path])
        self.assertFalse(self.skill.exists())
        self.assertFalse(self.orchestrator.exists())
        receipt, = (self.config / 'astra-flash-install-backups').glob('*/receipt.json')
        self.assertEqual(json.loads(receipt.read_text())['status'], 'rolled-back')

    def test_legacy_change_after_planning_blocks_all_writes(self):
        known = self.legacy_fixture()
        with patch.object(zcode, 'LEGACY_HASHES', known):
            changes = zcode.plan_changes(self.root, self.config, migrate_legacy=True)
        old_skill = self.config / 'skills' / 'glm-orchestrator' / 'SKILL.md'
        old_skill.write_text('Concurrent user edit')
        before = self.snapshot()
        with self.assertRaises(install.SetupError):
            install.apply_changes(changes, self.config, {})
        self.assertEqual(before, self.snapshot())

    def test_undo_migration_refuses_recreated_legacy_file(self):
        receipt = self.migration()
        old_skill = self.config / 'skills' / 'glm-orchestrator' / 'SKILL.md'
        old_skill.write_text('New personal skill')
        before = self.snapshot()
        self.assertEqual(self.cli('--undo', str(receipt), '--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_migration_preserves_other_files_in_legacy_directory(self):
        known = self.legacy_fixture()
        notes = self.config / 'skills' / 'glm-orchestrator' / 'notes.md'
        notes.write_text('My notes')
        with patch.object(zcode, 'LEGACY_HASHES', known):
            changes = zcode.plan_changes(self.root, self.config, migrate_legacy=True)
        install.apply_changes(changes, self.config, {})
        self.assertEqual(notes.read_text(), 'My notes')

    def test_conflicting_codex_skill_is_preserved(self):
        duplicate = self.root / '.codex' / 'skills' / zcode.ORCHESTRATOR
        duplicate.mkdir(parents=True)
        (duplicate / 'SKILL.md').write_text('Personal skill')
        before = self.snapshot()
        with patch.dict('os.environ', {'CODEX_HOME': str(self.root / '.codex')}):
            with self.assertRaises(install.SetupError):
                zcode.plan_changes(self.root, self.config)
        self.assertEqual(before, self.snapshot())

    def test_shared_coordinator_migrates_to_codex_only_and_undo_restores_it(self):
        shared = self.root / '.agents' / 'skills' / zcode.ORCHESTRATOR / 'SKILL.md'
        shared.parent.mkdir(parents=True)
        original = (ROOT / 'handoff/skills/astra-glm-orchestrator/SKILL.md').read_bytes().replace(b'\r\n', b'\n')
        shared.write_bytes(original)
        before = self.snapshot()
        self.assertEqual(self.cli('--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())
        result = self.cli('--migrate-legacy', '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.orchestrator.read_bytes(), original)
        self.assertFalse(shared.exists())
        self.assertEqual(self.cli('--check').returncode, 0)
        receipt, = (self.config / 'astra-flash-install-backups').glob('*/receipt.json')
        self.assertEqual(self.cli('--undo', str(receipt), '--apply').returncode, 0)
        self.assertEqual(shared.read_bytes(), original)
        self.assertFalse(self.orchestrator.exists())

    def test_customized_shared_coordinator_is_preserved(self):
        shared = self.root / '.agents' / 'skills' / zcode.ORCHESTRATOR / 'SKILL.md'
        shared.parent.mkdir(parents=True)
        shared.write_text('Personal coordinator skill')
        before = self.snapshot()
        self.assertEqual(self.cli('--migrate-legacy', '--replace', '--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())

    def test_custom_codex_home_selects_only_that_destination(self):
        custom = self.root / 'custom-codex'
        with patch.dict('os.environ', {'CODEX_HOME': str(custom)}):
            changes = zcode.plan_changes(self.root, self.config)
        paths = {c['path'] for c in changes}
        self.assertIn(custom / 'skills' / zcode.ORCHESTRATOR / 'SKILL.md', paths)
        self.assertNotIn(self.orchestrator, paths)


if __name__ == '__main__':
    unittest.main()
