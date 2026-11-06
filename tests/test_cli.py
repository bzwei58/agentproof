import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentproof.cli import main, safe_path
from agentproof import __version__


class CLITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.file = self.root / 'app.py'
        self.file.write_text('print("start")\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'app.py'], check=True)
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
        self.assertEqual(self.cli('init', '--goal', 'Fix payments')[0], 0)
        return self.state()['checkpoints'][0]['id']

    def state(self):
        return json.loads((self.root / '.agentproof/task.json').read_text())

    def test_baseline_preserves_initial_uncommitted_edits(self):
        self.file.write_text('existing user edit')
        ident = self.init()
        self.assertEqual((self.root / f'.agentproof/snapshots/{ident}/files/app.py').read_text(),
                         'existing user edit')
        self.assertEqual(self.cli('init', '--goal', 'again')[0], 2)

    def test_version_matches_package_version(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as exc:
            with contextlib.redirect_stdout(out):
                main(['--version'])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f'AgentProof {__version__}')

    def test_evidence_is_git_ignored(self):
        self.init()
        status = subprocess.check_output(['git', 'status', '--porcelain']).decode()
        self.assertNotIn('.agentproof', status)

    def test_restore_preview_and_backup_preserve_index_and_extra_files(self):
        ident = self.init()
        self.file.write_text('changed')
        (self.root / 'new.py').write_text('keep me')
        index_before = subprocess.check_output(['git', 'diff', '--cached'])
        self.assertEqual(self.cli('restore', ident)[0], 0)
        self.assertEqual(self.file.read_text(), 'changed')
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 0)
        self.assertEqual(self.file.read_text(), 'print("start")\n')
        self.assertEqual((self.root / 'new.py').read_text(), 'keep me')
        self.assertEqual(subprocess.check_output(['git', 'diff', '--cached']), index_before)
        backup = self.state()['checkpoints'][-1]['id']
        self.assertEqual((self.root / f'.agentproof/snapshots/{backup}/files/app.py').read_text(), 'changed')

    def test_restore_deleted_file(self):
        ident = self.init()
        self.file.unlink()
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 0)
        self.assertTrue(self.file.exists())

    def test_restore_parent_file_conflict_leaves_workspace_unchanged(self):
        folder = self.root / 'z-module'
        folder.mkdir()
        nested = folder / 'worker.py'
        nested.write_text('original module')
        ident = self.init()
        self.file.write_text('keep unrelated local changes')
        nested.unlink()
        folder.rmdir()
        folder.write_text('directory replaced by a file')
        before = self.state()['checkpoints']

        code, output = self.cli('restore', ident, '--apply')

        self.assertEqual(code, 2)
        self.assertEqual(self.file.read_text(), 'keep unrelated local changes')
        self.assertEqual(folder.read_text(), 'directory replaced by a file')
        self.assertIn('z-module', output)
        self.assertEqual(self.state()['checkpoints'], before)
        self.assertEqual(self.cli('restore', ident)[0], 2)

    def test_restore_ancestor_file_conflict_leaves_workspace_unchanged(self):
        folder = self.root / 'z-module'
        nested = folder / 'submodule' / 'worker.py'
        nested.parent.mkdir(parents=True)
        nested.write_text('original module')
        ident = self.init()
        self.file.write_text('keep unrelated local changes')
        nested.unlink()
        nested.parent.rmdir()
        folder.rmdir()
        folder.write_text('directory replaced by a file')
        before = self.state()['checkpoints']

        code, output = self.cli('restore', ident, '--apply')

        self.assertEqual(code, 2)
        self.assertEqual(self.file.read_text(), 'keep unrelated local changes')
        self.assertEqual(folder.read_text(), 'directory replaced by a file')
        self.assertIn('z-module', output)
        self.assertEqual(self.state()['checkpoints'], before)
        self.assertEqual(self.cli('restore', ident)[0], 2)

    def test_restore_recreates_missing_parent_directories(self):
        nested = self.root / 'module' / 'submodule' / 'worker.py'
        nested.parent.mkdir(parents=True)
        nested.write_text('original module')
        ident = self.init()
        nested.unlink()
        nested.parent.rmdir()
        nested.parent.parent.rmdir()

        self.assertEqual(self.cli('restore', ident)[0], 0)
        self.assertFalse(nested.parent.exists())
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 0)
        self.assertEqual(nested.read_text(), 'original module')

    def test_restore_refuses_to_overwrite_file_not_in_backup_scope(self):
        extra = self.root / 'initial.txt'
        extra.write_text('initial')
        ident = self.init()
        extra.write_text('important later work')
        (self.root / '.gitignore').write_text('initial.txt\n')
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 2)
        self.assertEqual(extra.read_text(), 'important later work')

    def test_pass_failure_and_stale_evidence(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok")')[0], 0)
        self.assertIn('matches current captured files', self.cli('status')[1])
        self.file.write_text('new code')
        self.assertIn('STALE', self.cli('status')[1])
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'raise SystemExit(3)')[0], 1)
        self.assertEqual(self.state()['checks'][-1]['exit_code'], 3)
        self.assertIn('**FAIL**', self.cli('status')[1])

    def test_check_that_changes_code_is_not_fresh(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c',
                                 'from pathlib import Path; Path("app.py").write_text("changed")')[0], 0)
        self.assertIn('STALE', self.cli('status')[1])

    def test_timeout_and_missing_executable_recorded(self):
        self.init()
        self.assertEqual(self.cli('verify', '--timeout', '0.05', '--', sys.executable,
                                 '-c', 'import time; time.sleep(10)')[0], 1)
        self.assertTrue(self.state()['checks'][-1]['timed_out'])
        self.assertEqual(self.cli('verify', '--', 'agentproof-nonexistent-command')[0], 1)
        self.assertEqual(self.state()['checks'][-1]['exit_code'], 127)

    def test_failed_command_shows_bounded_tail_and_retains_full_log(self):
        self.init()
        code, output = self.cli('verify', '--', sys.executable, '-c',
                                'print("START_OF_LOG"); print("x" * 20000); '
                                'print("actionable failure detail"); raise SystemExit(3)')
        self.assertEqual(code, 1)
        self.assertIn('actionable failure detail', output)
        self.assertNotIn('START_OF_LOG', output)
        self.assertLess(len(output), 4500)
        full_log = (self.root / self.state()['checks'][-1]['log']).read_text()
        self.assertIn('START_OF_LOG', full_log)
        self.assertGreater(len(full_log), 20000)

    def test_successful_command_stays_quiet(self):
        self.init()
        code, output = self.cli('verify', '--', sys.executable, '-c', 'print("QUIET_SUCCESS")')
        self.assertEqual(code, 0)
        self.assertNotIn('QUIET_SUCCESS', output)
        self.assertIn('QUIET_SUCCESS', (self.root / self.state()['checks'][-1]['log']).read_text())

    def test_timeout_is_explained_in_terminal(self):
        self.init()
        code, output = self.cli('verify', '--timeout', '0.05', '--', sys.executable,
                                '-c', 'import time; time.sleep(10)')
        self.assertEqual(code, 1)
        self.assertIn('Timed out after 0.05s', output)

    def test_missing_executable_error_is_visible(self):
        self.init()
        code, output = self.cli('verify', '--', 'agentproof-nonexistent-command')
        self.assertEqual(code, 1)
        self.assertIn('agentproof-nonexistent-command', output)

    def test_import_partial_jsonl_without_storing_messages(self):
        self.init()
        fixture = self.root / '.agentproof/session.jsonl'
        rows = [
            {'type': 'system', 'subtype': 'compact_boundary'},
            {'message': {'content': [{'type': 'tool_use', 'input': {'secret': 'DO_NOT_COPY'}}]}},
            {'message': {'content': [{'type': 'tool_result', 'is_error': True,
                                      'content': 'output truncated'}]}},
        ]
        fixture.write_text('\n'.join(json.dumps(r) for r in rows) + '\n{broken\n[]\n')
        self.assertEqual(self.cli('import-session', str(fixture))[0], 0)
        state = self.state()
        self.assertEqual(state['sessions'][0]['metrics'], {
            'records': 3, 'malformed_lines': 2, 'tool_calls': 1, 'tool_errors': 1,
            'compactions': 1, 'truncation_markers': 1})
        self.assertNotIn('DO_NOT_COPY', json.dumps(state))

    def test_sensitive_ignored_and_symlink_files_excluded(self):
        (self.root / '.env').write_text('secret')
        (self.root / '.gitignore').write_text('ignored.txt\n')
        (self.root / 'ignored.txt').write_text('ignored')
        (self.root / 'link.py').symlink_to(self.file)
        ident = self.init()
        manifest = json.loads((self.root / f'.agentproof/snapshots/{ident}/manifest.json').read_text())
        self.assertNotIn('.env', manifest['files'])
        self.assertNotIn('link.py', manifest['files'])
        self.assertNotIn('ignored.txt', manifest['files'])
        self.assertEqual(len(manifest['omitted']), 2)

    def test_restore_rejects_symlink_and_corrupted_snapshot(self):
        ident = self.init()
        self.file.unlink()
        self.file.symlink_to(self.root / 'outside.py')
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 2)
        self.file.unlink()
        self.file.write_text('current')
        (self.root / f'.agentproof/snapshots/{ident}/files/app.py').write_text('tampered')
        self.assertEqual(self.cli('restore', ident, '--apply')[0], 2)
        self.assertEqual(self.file.read_text(), 'current')

    def test_path_traversal_rejected(self):
        for name in ('../escape', '/tmp/escape', '.git/config', '.agentproof/task.json'):
            with self.assertRaises(ValueError):
                safe_path(self.root, name)

    def test_lock_and_unknown_checkpoint(self):
        self.init()
        lock = self.root / '.agentproof/lock'
        lock.mkdir()
        self.assertEqual(self.cli('checkpoint')[0], 2)
        self.assertTrue(lock.exists())
        lock.rmdir()
        self.assertEqual(self.cli('restore', '../escape')[0], 2)

    def test_report_generation_and_no_checks_claim(self):
        self.init()
        self.assertEqual(self.cli('report')[0], 0)
        content = (self.root / '.agentproof/report.md').read_text()
        self.assertIn('No verification commands recorded', content)
        self.assertIn('does not certify correctness', content)

    def test_review_matches_fresh_test_and_reports_missing_evidence(self):
        self.init()
        self.file.write_text('print("changed")\n')
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok") # test')[0], 0)
        code, output = self.cli('review', '--claim', 'Fixed the bug', '--claim', 'All tests passed')
        self.assertEqual(code, 0)
        self.assertIn('OBSERVED: Fixed the bug', output)
        self.assertIn('VERIFIED: All tests passed', output)
        self.assertEqual(self.cli('report')[0], 0)
        report = (self.root / '.agentproof/report.md').read_text()
        self.assertIn('## Claim review', report)

    def test_review_flags_test_claim_when_check_is_stale(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok")')[0], 0)
        self.file.write_text('changed after test')
        code, output = self.cli('review', '--claim', 'Tests passed')
        self.assertEqual(code, 0)
        self.assertIn('CONFLICT: Tests passed', output)

    def test_review_can_read_summary_file(self):
        self.init()
        summary = self.root / 'summary.txt'
        summary.write_text('Implemented the change\nNo external verification was performed\n')
        code, output = self.cli('review', '--summary', str(summary))
        self.assertEqual(code, 0)
        self.assertIn('OBSERVED: Implemented the change', output)
        self.assertIn('UNVERIFIED: No external verification was performed', output)

    def test_review_does_not_treat_arbitrary_success_as_test_evidence(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok")')[0], 0)
        code, output = self.cli('review', '--claim', 'All tests passed')
        self.assertEqual(code, 0)
        self.assertIn('CONFLICT: All tests passed', output)

    def test_review_counts_deleted_files_as_changes(self):
        self.init()
        self.file.unlink()
        code, output = self.cli('review', '--claim', 'Fixed the bug')
        self.assertEqual(code, 0)
        self.assertIn('OBSERVED: Fixed the bug', output)

    def test_report_compacts_multiline_commands_without_losing_evidence(self):
        self.init()
        script = 'print("ok")\n# ' + ('long command ' * 30) + '```'
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', script)[0], 0)
        self.assertEqual(self.cli('report')[0], 0)
        content = (self.root / '.agentproof/report.md').read_text()
        summary = next(line for line in content.splitlines() if line.startswith('- **PASS**'))
        self.assertLess(len(summary), 300)
        self.assertIn('<summary>Full command</summary>', content)
        self.assertIn(self.state()['checks'][-1]['command'], content)
        self.assertIn('````sh', content)


if __name__ == '__main__':
    unittest.main()
