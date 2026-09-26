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
        self.config = self.root / '.zcode'
        self.config.mkdir()
        self.builder = self.config / 'agents' / f'{zcode.BUILDER}.md'
        self.skill = self.config / 'skills' / zcode.SKILL / 'SKILL.md'
        self.settings = self.config / 'v2' / 'config.json'
        self.settings.parent.mkdir()
        self.settings.write_text('{"apiKey":"TEST_SECRET","model":"existing-model"}')
        (self.config / 'AGENTS.md').write_text('Preserve project continuity rules')

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'install_zcode.py'),
                               '--zcode-home', str(self.config), *args], capture_output=True, text=True)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def apply(self):
        return install.apply_changes(zcode.plan_changes(self.config), self.config, {})

    def test_preview_changes_nothing_and_needs_no_router_or_credentials(self):
        before = self.snapshot()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertNotIn('TEST_SECRET', result.stdout + result.stderr)
        self.assertIn('unverified', result.stdout)

    def test_install_creates_native_skill_and_inherited_worker_preserving_settings(self):
        before = self.snapshot()
        result = self.cli('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        after = self.snapshot()
        for path, data in before.items():
            self.assertEqual(data, after[path])
        worker = self.builder.read_text()
        self.assertIn('model: inherit\n', worker)
        self.assertIn('injectAgentsMd: true\n', worker)
        self.assertNotIn('thoughtLevel:', worker)
        self.assertNotIn('{{', worker)
        self.assertNotIn('Astra', worker)
        self.assertTrue(self.skill.read_text().startswith('---\nname: glm-orchestrator\n'))
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
        self.builder.parent.mkdir()
        self.builder.write_text('Personal agent instructions')
        before = self.snapshot()
        self.assertEqual(self.cli('--apply').returncode, 2)
        self.assertEqual(before, self.snapshot())
        receipt = install.apply_changes(zcode.plan_changes(self.config, True), self.config, {})
        result = self.cli('--undo', str(receipt), '--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual('Personal agent instructions', self.builder.read_text())
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
        self.assertFalse(self.builder.exists())
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
        changes = zcode.plan_changes(self.config)
        original_write = install.atomic_write

        def failing_write(path, data, mode=0o600):
            if path == self.skill:
                raise OSError('synthetic write failure')
            return original_write(path, data, mode)

        with patch.object(install, 'atomic_write', side_effect=failing_write), self.assertRaises(OSError):
            install.apply_changes(changes, self.config, {})
        self.assertFalse(self.builder.exists())
        self.assertFalse(self.skill.exists())
        receipt, = (self.config / 'astra-flash-install-backups').glob('*/receipt.json')
        self.assertEqual(json.loads(receipt.read_text())['status'], 'rolled-back')

    def test_symlinked_skill_directory_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        create_symlink(self.config / 'skills', outside, directory=True)
        with self.assertRaises(install.SetupError):
            zcode.plan_changes(self.config)
        self.assertEqual([], list(outside.iterdir()))

    def test_default_destination_and_explicit_override(self):
        with patch.object(Path, 'home', return_value=self.root):
            self.assertEqual(zcode.config_directory(None), self.root / '.zcode')
            self.assertEqual(zcode.config_directory(str(self.root / 'custom')), self.root / 'custom')


if __name__ == '__main__':
    unittest.main()
