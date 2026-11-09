import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentproof.cli import main


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'app.py').write_text('old\n')
        self.cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.temp.cleanup()

    def cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(args))
        return code, out.getvalue()

    def init(self):
        code, output = self.cli('init', '--goal', 'Review workflow')
        self.assertEqual(code, 0, output)

    def config(self, checks):
        (self.root / 'agentproof.json').write_text(json.dumps({'version': 1, 'checks': checks}))

    def check(self, name='tests', script='print("check ran")', **kwargs):
        return {'name': name, 'kind': 'test', 'command': [sys.executable, '-c', script], **kwargs}

    def test_check_list_is_preview_without_active_task(self):
        self.config([self.check(script='from pathlib import Path; Path("marker").touch()')])
        code, output = self.cli('check', '--list')
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)[0]['name'], 'tests')
        self.assertFalse((self.root / 'marker').exists())
        self.assertFalse((self.root / '.agentproof/task.json').exists())

    def test_invalid_later_configuration_prevents_all_execution(self):
        self.config([self.check(script='from pathlib import Path; Path("marker").touch()'),
                     self.check(name='invalid', timeout=-1)])
        self.init()
        self.assertEqual(self.cli('check')[0], 2)
        self.assertFalse((self.root / 'marker').exists())

    def test_configured_checks_keep_failure_and_generate_report(self):
        self.config([self.check(name='failure', script='raise SystemExit(4)'), self.check(name='success')])
        self.init()
        code, output = self.cli('check')
        self.assertEqual(code, 1, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['schema_version'], 1)
        self.assertEqual([x['exit_code'] for x in data['checks']], [4, 0])
        self.assertTrue(any('FAIL: failure' in x for x in data['attention']))
        self.assertTrue((self.root / '.agentproof/report.md').is_file())
        self.assertTrue((self.root / '.agentproof/changes.patch').is_file())

    def test_interrupted_check_stops_remaining_commands_and_keeps_report(self):
        self.config([self.check(name='interrupted', script='raise SystemExit(130)'),
                     self.check(name='never-run', script='from pathlib import Path; Path("marker").touch()')])
        self.init()
        code, output = self.cli('check')
        self.assertEqual(code, 1, output)
        self.assertIn('remaining commands were not run', output)
        self.assertFalse((self.root / 'marker').exists())
        self.assertTrue((self.root / '.agentproof/report.md').is_file())

    def test_latest_success_replaces_failure_in_review_priorities(self):
        self.config([self.check(script='from pathlib import Path; '
                                'raise SystemExit(0 if Path(".agentproof/ready").exists() else 4)')])
        self.init()
        # Same configured check can fail once and then succeed at unchanged files.
        self.assertEqual(self.cli('check')[0], 1)
        (self.root / '.agentproof/ready').touch()
        self.assertEqual(self.cli('check')[0], 0)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertFalse(any('FAIL:' in x for x in data['attention']))
        self.assertEqual(len(data['checks']), 2)

    def test_latest_failure_removes_earlier_receipt_for_same_command(self):
        self.config([self.check(script='from pathlib import Path; '
                                'raise SystemExit(4 if Path(".agentproof/fail").exists() else 0)')])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        self.cli('review', '--claim', 'Tests passed')
        self.assertIn('Receipt: Recorded exit 0', self.cli('status')[1])
        (self.root / '.agentproof/fail').touch()
        self.assertEqual(self.cli('check')[0], 1)
        self.assertNotIn('Receipt: Recorded exit 0', self.cli('status')[1])

    def test_invalid_config_shapes_and_timeouts(self):
        invalid = [self.check(timeout=float('nan')), self.check(timeout=True), self.check(timeout=10 ** 1000),
                   self.check(command='python -m unittest'), self.check(extra='typo'),
                   self.check(kind='unknown')]
        for entry in invalid:
            with self.subTest(entry=entry):
                self.config([entry])
                self.assertEqual(self.cli('check', '--list')[0], 2)
        self.config([self.check(), self.check()])
        self.assertEqual(self.cli('check', '--list')[0], 2)

    def test_direct_verify_rejects_nonpositive_or_nonfinite_timeout(self):
        self.init()
        for timeout in ['0', '-1', 'nan', 'inf']:
            code, output = self.cli('verify', '--timeout', timeout, '--', sys.executable, '-c', 'print("ok")')
            self.assertEqual(code, 2, output)

    def test_baseline_diff_includes_existing_edits_additions_and_deletions(self):
        subprocess.run(['git', 'add', 'app.py'], check=True)
        (self.root / 'app.py').write_text('preexisting edit\n')
        (self.root / 'deleted.txt').write_text('remove\n')
        self.init()
        (self.root / 'app.py').write_text('AI edit\n')
        (self.root / 'new.txt').write_text('new file without newline')
        (self.root / 'deleted.txt').unlink()
        code, output = self.cli('diff')
        self.assertEqual(code, 0, output)
        self.assertIn('-preexisting edit', output)
        self.assertIn('+AI edit', output)
        self.assertIn('--- /dev/null', output)
        self.assertIn('+++ /dev/null', output)
        self.assertIn('No newline at end of file', output)
        one_file = self.cli('diff', 'app.py')[1]
        self.assertNotIn('deleted.txt', one_file)
        self.assertEqual(self.cli('diff', '../outside')[0], 2)

    def test_binary_diff_and_corrupted_baseline(self):
        self.init()
        (self.root / 'app.py').write_bytes(b'binary\0data')
        self.assertIn('Binary or non-UTF-8', self.cli('diff')[1])
        state = json.loads((self.root / '.agentproof/task.json').read_text(encoding="utf-8"))
        snapshot = self.root / '.agentproof/snapshots' / state['checkpoints'][0]['id'] / 'files/app.py'
        snapshot.write_text('corrupt')
        code, output = self.cli('diff')
        self.assertEqual(code, 2, output)
        self.assertIn('Corrupted baseline file', output)

    def test_brief_and_json_surface_bad_logs_without_claims(self):
        self.init()
        self.cli('verify', '--', sys.executable, '-c', 'print("ok")')
        state = json.loads((self.root / '.agentproof/task.json').read_text(encoding="utf-8"))
        (self.root / state['checks'][-1]['log']).unlink()
        self.assertIn('Log MISSING:', self.cli('status', '--brief')[1])
        data = json.loads(self.cli('status', '--json')[1])
        self.assertFalse(data['checks'][0]['usable'])
        self.assertEqual(data['checks'][0]['execution'], 'PASS')
        self.assertEqual(data['checks'][0]['log_integrity'], 'MISSING')

    def test_only_latest_claim_set_is_in_current_report(self):
        self.init()
        self.cli('review', '--claim', 'Old claim')
        self.cli('review', '--claim', 'New claim')
        output = self.cli('status')[1]
        self.assertNotIn('Old claim', output)
        self.assertIn('New claim', output)
        state = json.loads((self.root / '.agentproof/task.json').read_text(encoding="utf-8"))
        self.assertEqual(len(state['reviews']), 2)

    def test_finish_preserves_evidence_and_allows_next_task(self):
        self.init()
        self.cli('verify', '--kind', 'test', '--', sys.executable, '-c', 'print("ok")')
        (self.root / 'app.py').write_text('new\n')
        staged_before = subprocess.check_output(['git', 'diff', '--cached'])
        code, output = self.cli('finish')
        self.assertEqual(code, 0, output)
        self.assertFalse((self.root / '.agentproof/task.json').exists())
        history = list((self.root / '.agentproof/history').iterdir())
        self.assertEqual(len(history), 1)
        archive = history[0]
        self.assertIn('Review workflow', (archive / 'report.md').read_text(encoding="utf-8"))
        self.assertIn('+new', (archive / 'changes.patch').read_text(encoding="utf-8"))
        self.assertEqual(len(list((archive / 'checks').glob('*.log'))), 1)
        self.assertNotIn('.agentproof/checks/', (archive / 'report.md').read_text(encoding="utf-8"))
        self.assertTrue((archive / 'snapshots').is_dir())
        self.assertEqual(subprocess.check_output(['git', 'diff', '--cached']), staged_before)
        self.assertEqual((self.root / 'app.py').read_text(encoding="utf-8"), 'new\n')
        self.assertIn(archive.name, self.cli('history')[1])
        self.init()
        self.assertEqual(self.cli('finish')[0], 0)
        self.assertEqual(len(list((self.root / '.agentproof/history').iterdir())), 2)

    def test_archive_copy_failure_keeps_active_evidence(self):
        self.init()
        task = self.root / '.agentproof/task.json'
        before = task.read_bytes()
        with patch('agentproof.cli.shutil.copytree', side_effect=OSError('disk full')):
            code, output = self.cli('finish')
        self.assertEqual(code, 2, output)
        self.assertEqual(task.read_bytes(), before)
        self.assertTrue((self.root / '.agentproof/snapshots').is_dir())
        self.assertFalse(list((self.root / '.agentproof/history').iterdir()))


if __name__ == '__main__':
    unittest.main()
