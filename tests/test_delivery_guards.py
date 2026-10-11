"""Regression checks for lost take files, stale preview parts and broken preview cards.

Runs without large media or network; safe_commit is exercised inside a throwaway repo.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import split_preview
from transcribe_takes import source_matches
from guard_homonyms import norm


class SplitPreviewTests(unittest.TestCase):
    def test_source_change_requires_resplit_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'film.mp4'
            file.write_bytes(b'old content' * 2048)
            split_preview.split(file, 1)
            self.assertEqual(split_preview.check(file), 0)
            file.write_bytes(b'new content' * 2048)
            self.assertEqual(split_preview.check(file), 2, 'stale parts must not pass')
            self.assertEqual(split_preview.restore(file), 0)
            self.assertEqual(file.read_bytes(), b'old content' * 2048)

    def test_corrupt_part_rejected_even_without_source(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'film.mp4'
            file.write_bytes(b'original film')
            manifest = split_preview.split(file, 1)
            file.unlink()
            part = split_preview.parts_dir(file) / manifest['parts'][0]['file']
            part.write_bytes(b'changed file!')
            self.assertEqual(split_preview.check(file), 2)
            self.assertEqual(split_preview.restore(file), 2)
            self.assertFalse(file.exists())

    def test_manifest_cannot_traverse_outside_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / 'film.mp4'
            file.write_bytes(b'original film')
            split_preview.split(file, 1)
            manifest_file = split_preview.parts_dir(file) / 'manifest.json'
            manifest = json.loads(manifest_file.read_text())
            manifest['parts'][0]['file'] = '../film.mp4'
            manifest_file.write_text(json.dumps(manifest))
            self.assertEqual(split_preview.check(file), 2)


class SafeCommitTests(unittest.TestCase):
    def test_new_ignored_take_and_large_preview_are_safely_staged(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            for name in ('safe_commit.sh', 'split_preview.py', 'archive_build_evidence.py'):
                source = ROOT / 'scripts' / name
                target = repo / 'scripts' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            subprocess.run(['git', 'init', '-q', '-b', 'arena/01a09fa1-dub'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=repo, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=repo, check=True)
            (repo / '.gitignore').write_text('*.mp3\n*.mp4\n', encoding='utf-8')
            take = repo / 'work/demo/takes-voice25/g000.mp3'
            take.parent.mkdir(parents=True)
            take.write_bytes(b'ignored mp3')
            groups = repo / 'work/demo/groups.json'
            groups.write_text('{"groups": []}', encoding='utf-8')
            preview = repo / 'previews/new.mp4'
            preview.parent.mkdir()
            preview.write_bytes(b'new' * 400000)  # > SPLIT_MB=1
            env = dict(os.environ, DRY_RUN='1', SPLIT_MB='1')
            def stage():
                proc = subprocess.run(['bash', 'scripts/safe_commit.sh', 'test'], cwd=repo,
                                      env=env, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            stage()
            def tracked(path):
                return subprocess.run(['git', 'ls-files', '--error-unmatch', '--', str(path)],
                                      cwd=repo, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL).returncode == 0
            self.assertTrue(tracked('work/demo/takes-voice25/g000.mp3'))
            self.assertFalse(tracked('previews/new.mp4'))
            self.assertTrue(tracked('previews/new.mp4.parts/manifest.json'))
            old_sha = json.loads((repo / 'previews/new.mp4.parts/manifest.json').read_text())['sha256']
            preview.write_bytes(b'changed' * 200000)
            stage()
            new_sha = json.loads((repo / 'previews/new.mp4.parts/manifest.json').read_text())['sha256']
            self.assertNotEqual(old_sha, new_sha, 'stale manifest must be rebuilt')
            self.assertFalse(tracked('previews/new.mp4'))
            # A workspace wipe removes the untracked playable file. Parts must restore it.
            preview.unlink()
            self.assertEqual(split_preview.restore(preview), 0)
            self.assertEqual(hashlib.sha256(preview.read_bytes()).hexdigest(), new_sha)


class PronunciationCacheTests(unittest.TestCase):
    def test_old_report_or_changed_take_is_not_valid_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            take = Path(temp) / 'g000.mp3'
            report = Path(temp) / 'g000.json'
            take.write_bytes(b'old voice')
            report.write_text(json.dumps({'transcription': [{'text': 'الرزق'}]}))
            self.assertFalse(source_matches(report, take))
            digest = hashlib.sha256(take.read_bytes()).hexdigest()
            report.write_text(json.dumps({'transcription': [{'text': 'الخير'}],
                                          '_provenance': {'take_sha256': digest}}))
            self.assertTrue(source_matches(report, take))
            take.write_bytes(b'new voice')
            self.assertFalse(source_matches(report, take))


class HomonymTests(unittest.TestCase):
    def test_guard_is_advisory_and_does_not_rewrite_approved_text(self):
        with tempfile.TemporaryDirectory() as temp:
            groups = Path(temp) / 'groups.json'
            original = '{"groups": [{"i": 0, "text": "بلغ قوته"}]}'
            groups.write_text(original, encoding='utf-8')
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/guard_homonyms.py'),
                                     str(groups)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn('قوته', result.stdout)
            self.assertEqual(groups.read_text(encoding='utf-8'), original)
            strict = subprocess.run([sys.executable, str(ROOT / 'scripts/guard_homonyms.py'),
                                     str(groups), '--strict'], capture_output=True)
            self.assertEqual(strict.returncode, 1)


class PreviewCardsTests(unittest.TestCase):
    def test_cards_render_without_runtime_unpacking_errors(self):
        spec = importlib.util.spec_from_file_location('upload_server', ROOT / 'scripts/upload_server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(all(isinstance(v, tuple) and len(v) == 2
                            for v in module.LABELS.values()))
        self.assertIn('voice25-run-0-50', module.render_previews())
        self.assertIn('ست أخذات', module.LABELS['voice25-run-0-50'][1])


if __name__ == '__main__':
    unittest.main()
