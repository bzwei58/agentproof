import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agentproof.cli import main
from helpers import cleanup_temp


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.name', 'AgentProof Test'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.email', 'test@example.invalid'], check=True)
        (self.root / 'app.py').write_text('old\n')
        self.cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        cleanup_temp(self.temp)

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

    def test_acceptance_items_link_checks_and_surface_human_questions(self):
        self.config([self.check(name='tests')])
        config = json.loads((self.root / 'agentproof.json').read_text(encoding='utf-8'))
        config['acceptance'] = [{"id": "retry-limit", "description": "Retries stop at the configured limit",
                                 "checks": ["tests"], "questions": ["Confirm the timeout scenario is covered."]}]
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'NEEDS_HUMAN_REVIEW')
        self.assertIn('Confirm the timeout scenario is covered.', data['acceptance'][0]['next_steps'])
        report = self.cli('status', '--brief')[1]
        self.assertIn('Acceptance NEEDS_HUMAN_REVIEW', report)

    def test_acceptance_files_are_preserved_in_json_and_report(self):
        self.config([self.check(name='tests')])
        config = json.loads((self.root / 'agentproof.json').read_text(encoding='utf-8'))
        config['acceptance'] = [{'id': 'api', 'description': 'API remains compatible', 'checks': ['tests'],
                                 'files': ['app.py', 'tests/test_api.py']}]
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['files'], ['app.py', 'tests/test_api.py'])
        self.assertIn('`app.py`', self.cli('status')[1])

    def test_acceptance_missing_check_is_actionable_and_config_errors_are_rejected(self):
        self.config([self.check(name='tests')])
        config = json.loads((self.root / 'agentproof.json').read_text(encoding='utf-8'))
        config['acceptance'] = [{"id": "api", "description": "API remains compatible", "checks": ["contract"]}]
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.assertEqual(self.cli('check', '--list')[0], 2)
        config['acceptance'] = [{"id": "api", "description": "API remains compatible", "checks": ["tests"]}]
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['acceptance'][0]['status'], 'NEEDS_HUMAN_REVIEW')

    def acceptance_config(self, acceptance=None):
        config = {'version': 1, 'checks': [self.check(name='tests')]}
        if acceptance is not None:
            config['acceptance'] = acceptance
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')

    def test_confirm_acceptance_item_records_decision(self):
        self.acceptance_config([{'id': 'evidence-integrity', 'description': 'Evidence is coherent',
                                 'checks': ['tests'], 'questions': ['Review the captured output.']}])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        code, output = self.cli('confirm', 'evidence-integrity', '--note', 'I reviewed the output.')
        self.assertEqual(code, 0, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'CONFIRMED')
        self.assertEqual(data['acceptance'][0]['decision']['note'], 'I reviewed the output.')
        self.assertIn('I reviewed the output.', self.cli('status')[1])

    def test_reject_acceptance_item_records_reason(self):
        self.acceptance_config([{'id': 'actionability', 'description': 'Review is actionable',
                                 'checks': ['tests']}])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        code, output = self.cli('reject', 'actionability', '--reason', 'Missing timeout coverage')
        self.assertEqual(code, 0, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'REJECTED')
        self.assertEqual(data['acceptance'][0]['decision']['reason'], 'Missing timeout coverage')

    def test_confirmation_becomes_stale_after_file_change(self):
        self.acceptance_config([{'id': 'evidence', 'description': 'Evidence is current', 'checks': ['tests']}])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)
        (self.root / 'app.py').write_text('changed\n')
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'STALE_CONFIRMATION')

    def test_confirmation_becomes_stale_after_log_tampering(self):
        self.acceptance_config([{'id': 'evidence', 'description': 'Evidence is intact', 'checks': ['tests']}])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)
        state = json.loads((self.root / '.agentproof/task.json').read_text(encoding='utf-8'))
        log = self.root / state['checks'][-1]['log']
        log.write_text(log.read_text(encoding='utf-8') + 'tampered\n', encoding='utf-8')
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'STALE_CONFIRMATION')

    def test_confirmation_becomes_stale_after_rerun(self):
        self.acceptance_config([{'id': 'evidence', 'description': 'Evidence is current', 'checks': ['tests']}])
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)
        self.assertEqual(self.cli('check')[0], 0)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['acceptance'][0]['status'], 'STALE_CONFIRMATION')

    def test_unknown_acceptance_id_is_rejected(self):
        self.acceptance_config([{'id': 'known', 'description': 'Known item', 'checks': ['tests']}])
        self.init()
        code, output = self.cli('confirm', 'missing')
        self.assertEqual(code, 2)
        self.assertIn('Unknown acceptance id', output)
        state = json.loads((self.root / '.agentproof/task.json').read_text(encoding='utf-8'))
        self.assertEqual(state['decisions'], [])

    def test_legacy_task_without_decisions_remains_usable(self):
        self.init()
        path = self.root / '.agentproof/task.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state.pop('decisions', None)
        path.write_text(json.dumps(state), encoding='utf-8')
        self.assertEqual(self.cli('status', '--json')[0], 0)

    def test_init_from_commit_records_after_the_fact_baseline(self):
        (self.root / 'app.py').write_text('committed\n')
        subprocess.run(['git', 'add', 'app.py'], check=True)
        subprocess.run(['git', 'commit', '-qm', 'baseline'], check=True)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        (self.root / 'app.py').write_text('current\n')
        code, output = self.cli('init', '--goal', 'Historical review', '--from-commit', commit)
        self.assertEqual(code, 0, output)
        self.assertIn('after-the-fact baseline', output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['baseline_mode'], 'historical_commit')
        self.assertEqual(data['baseline_commit'], commit)
        self.assertIn('committed', self.cli('diff')[1])
        self.assertEqual(self.cli('report')[0], 0)
        self.assertIn('after-the-fact baseline', (self.root / '.agentproof/report.md').read_text(encoding='utf-8'))

    def test_history_show_and_resume_keeps_archive_immutable(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok")')[0], 0)
        self.assertEqual(self.cli('finish')[0], 0)
        archive = next((self.root / '.agentproof/history').iterdir())
        task_id = archive.name
        code, output = self.cli('history', 'show', task_id)
        self.assertEqual(code, 0, output)
        self.assertIn('Review workflow', output)
        self.assertEqual(self.cli('history', 'resume', task_id)[0], 0)
        self.assertTrue((self.root / '.agentproof/task.json').exists())
        resumed = json.loads((self.root / '.agentproof/task.json').read_text(encoding='utf-8'))
        self.assertEqual(resumed['resumed_from'], task_id)
        self.assertTrue((archive / 'task.json').exists())

    def decision_setup(self):
        self.acceptance_config([{'id': 'evidence', 'description': 'Current evidence', 'checks': ['tests']}])
        self.init()

    def test_confirm_requires_current_successful_intact_evidence(self):
        self.decision_setup()
        self.assertEqual(self.cli('confirm', 'evidence')[0], 2)
        self.assertEqual(self.cli('check')[0], 0)
        state = json.loads((self.root / '.agentproof/task.json').read_text())
        log = self.root / state['checks'][-1]['log']
        original = log.read_bytes()
        log.unlink()
        self.assertEqual(self.cli('confirm', 'evidence')[0], 2)
        log.write_bytes(original)
        (self.root / 'app.py').write_text('changed')
        self.assertEqual(self.cli('confirm', 'evidence')[0], 2)
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)

    def test_failed_check_cannot_be_confirmed_but_can_be_rejected(self):
        self.decision_setup()
        self.acceptance_config([{'id': 'evidence', 'description': 'Current evidence', 'checks': ['tests']}])
        config = json.loads((self.root / 'agentproof.json').read_text())
        config['checks'][0]['command'] = [sys.executable, '-c', 'raise SystemExit(1)']
        (self.root / 'agentproof.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('check')[0], 1)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 2)
        self.assertEqual(self.cli('reject', 'evidence', '--reason', 'Test failed')[0], 0)
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['acceptance'][0]['status'], 'REJECTED')
        self.assertEqual(self.cli('reject', 'evidence', '--reason', '  ')[0], 2)

    def test_confirmation_invalidates_on_xml_tampering_and_config_changes(self):
        self.decision_setup()
        config = json.loads((self.root / 'agentproof.json').read_text())
        config['checks'] = [self.check(script='from pathlib import Path; import sys; '
                            "Path(sys.argv[1]).write_text('<testsuite><testcase name=\"ok\"/></testsuite>')",
                            result_format='junit')]
        config['checks'][0]['command'].append('{junit}')
        (self.root / 'agentproof.json').write_text(json.dumps(config))
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)
        state = json.loads((self.root / '.agentproof/task.json').read_text())
        xml = self.root / state['checks'][-1]['test_results']['path']
        xml.write_text('tampered')
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['acceptance'][0]['status'], 'STALE_CONFIRMATION')
        self.assertEqual(self.cli('check')[0], 0)
        self.assertEqual(self.cli('confirm', 'evidence')[0], 0)
        config['acceptance'][0]['description'] = 'Updated requirement'
        (self.root / 'agentproof.json').write_text(json.dumps(config))
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['acceptance'][0]['status'], 'STALE_CONFIRMATION')

    def test_invalid_config_status_is_actionable(self):
        self.init()
        (self.root / 'agentproof.json').write_text('{}')
        code, output = self.cli('status', '--json')
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)['acceptance'][0]['status'], 'CONFIG_ERROR')

    def test_resume_copy_failure_leaves_no_active_evidence_and_allows_retry(self):
        self.init()
        self.assertEqual(self.cli('verify', '--', sys.executable, '-c', 'print("ok")')[0], 0)
        self.assertEqual(self.cli('finish')[0], 0)
        archive = next((self.root / '.agentproof/history').iterdir())
        before = {str(p.relative_to(archive)): p.read_bytes() for p in archive.rglob('*') if p.is_file()}
        import shutil
        real_copytree = shutil.copytree
        def fail_copy(source, destination, *args, **kwargs):
            if Path(source).name == 'checks':
                raise OSError('disk full')
            return real_copytree(source, destination, *args, **kwargs)
        with patch('agentproof.cli.shutil.copytree', side_effect=fail_copy):
            self.assertEqual(self.cli('history', 'resume', archive.name)[0], 2)
        self.assertFalse((self.root / '.agentproof/task.json').exists())
        self.assertFalse((self.root / '.agentproof/snapshots').exists())
        self.assertFalse(list((self.root / '.agentproof').glob('.resume-*')))
        self.assertEqual(before, {str(p.relative_to(archive)): p.read_bytes() for p in archive.rglob('*') if p.is_file()})
        self.assertEqual(self.cli('history', 'resume', archive.name)[0], 0)

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink regression')
    def test_resume_refuses_symlink_evidence(self):
        self.init()
        self.assertEqual(self.cli('finish')[0], 0)
        archive = next((self.root / '.agentproof/history').iterdir())
        (archive / 'escape').symlink_to(self.root / 'app.py')
        self.assertEqual(self.cli('history', 'resume', archive.name)[0], 2)
        self.assertFalse((self.root / '.agentproof/task.json').exists())

    def test_outcome_rejects_nonfinite_time_and_keeps_unknown_issue_distinct(self):
        self.init()
        for value in ('nan', 'inf', '-1'):
            self.assertEqual(self.cli('outcome', '--decision', 'deferred', '--review-seconds', value)[0], 2)
        self.assertEqual(self.cli('outcome', '--decision', 'deferred')[0], 0)
        self.assertIsNone(json.loads(self.cli('status', '--json')[1])['outcomes'][-1]['found_issue'])

    def test_report_links_changed_files_and_lists_scenarios_once(self):
        self.acceptance_config([{'id': 'api', 'description': 'API remains compatible',
                                 'checks': ['tests'], 'files': ['app.py'],
                                 'questions': ['Does the timeout scenario work?']}])
        self.init()
        (self.root / 'app.py').write_text('updated API')
        self.assertEqual(self.cli('check')[0], 0)
        content = (self.root / '.agentproof/report.md').read_text()
        self.assertEqual(content.count('Does the timeout scenario work?'), 1)
        self.assertIn('Check `tests`: PASS; CURRENT', content)
        import re
        target = re.search(r'\[diff\]\((diffs/[^)]+)\)', content).group(1)
        self.assertTrue((self.root / '.agentproof' / target).exists())
        self.assertIn('updated API', (self.root / '.agentproof' / target).read_text())
        self.assertEqual(self.cli('finish')[0], 0)
        archive = next((self.root / '.agentproof/history').iterdir())
        self.assertTrue((archive / target).exists())
        self.assertIn('[checks/', (archive / 'report.md').read_text())

    def test_acceptance_paths_reject_unsafe_or_nonportable_names(self):
        for name in ('../outside', '/absolute', 'C:/absolute', '.agentproof/task.json', 'a\\b'):
            self.acceptance_config([{'id': 'api', 'description': 'Test paths', 'files': [name]}])
            self.assertEqual(self.cli('check', '--list')[0], 2, name)

    def test_outcome_records_real_review_signal(self):
        self.init()
        code, output = self.cli('outcome', '--review-seconds', '42', '--found-issue',
                                '--decision', 'rejected', '--note', 'Found a missing edge case')
        self.assertEqual(code, 0, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['outcomes'][0]['review_seconds'], 42.0)
        self.assertTrue(data['outcomes'][0]['found_issue'])
        self.assertIn('Found a missing edge case', self.cli('status')[1])

    def test_interrupted_check_stops_remaining_commands_and_keeps_report(self):
        self.config([self.check(name='interrupted', script='raise SystemExit(130)'),
                     self.check(name='never-run', script='from pathlib import Path; Path("marker").touch()')])
        self.init()
        code, output = self.cli('check')
        self.assertEqual(code, 1, output)
        self.assertIn('remaining commands were not run', output)
        self.assertFalse((self.root / 'marker').exists())
        self.assertTrue((self.root / '.agentproof/report.md').is_file())
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual([x['status'] for x in data['planned_checks']], ['INTERRUPTED', 'NOT_RUN'])
        self.assertIn('never-run', (self.root / '.agentproof/report.md').read_text(encoding='utf-8'))

    def test_post_command_capture_failure_keeps_execution_and_report(self):
        self.config([self.check(name='oversize', script='from pathlib import Path; '
                                'Path("large.bin").write_bytes(b"x" * (11 * 1024 * 1024)); print("completed")'),
                     self.check(name='not-executed')])
        self.init()
        code, output = self.cli('check')
        self.assertEqual(code, 2, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['checks'][0]['exit_code'], 0)
        self.assertEqual(data['checks'][0]['workspace'], 'UNKNOWN')
        self.assertFalse(data['checks'][0]['usable'])
        self.assertEqual([x['status'] for x in data['planned_checks']], ['ERROR', 'NOT_RUN'])
        self.assertIn('completed', (self.root / data['checks'][0]['log']).read_text(encoding='utf-8'))
        self.assertIn('Comparison unavailable', (self.root / '.agentproof/changes.patch').read_text(encoding='utf-8'))
        self.assertTrue((self.root / '.agentproof/report.md').is_file())
        (self.root / 'large.bin').unlink()
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['checks'][0]['workspace'], 'UNKNOWN')

    def test_pending_execution_never_becomes_pass_evidence(self):
        self.init()
        self.cli('verify', '--kind', 'test', '--', sys.executable, '-c', 'print("ok")')
        path = self.root / '.agentproof/task.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state['checks'][-1].update(exit_code=None, after=None)
        path.write_text(json.dumps(state), encoding='utf-8')
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['checks'][0]['execution'], 'UNKNOWN')
        self.assertFalse(data['checks'][0]['usable'])

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree regression')
    def test_windows_timeout_stops_child_writes(self):
        self.init()
        child = ('import os,time; from pathlib import Path; '
                 'Path(".agentproof/child-pid").write_text(str(os.getpid())); '
                 '\nwhile True:\n Path(".agentproof/heartbeat").write_text(str(time.monotonic())); time.sleep(0.05)')
        parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(60)'
        try:
            code, output = self.cli('verify', '--timeout', '3', '--', sys.executable, '-c', parent)
            self.assertEqual(code, 1, output)
            heartbeat = self.root / '.agentproof/heartbeat'
            self.assertTrue(heartbeat.is_file(), 'Child must have started for this regression to be meaningful')
            before = heartbeat.read_bytes()
            time.sleep(0.4)
            self.assertEqual(heartbeat.read_bytes(), before)
        finally:
            pid_path = self.root / '.agentproof/child-pid'
            if pid_path.exists():
                subprocess.run(['taskkill', '/PID', pid_path.read_text(), '/T', '/F'], capture_output=True)

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

    def test_report_folds_historical_checks_but_json_keeps_them(self):
        self.config([self.check(name='tests', script='raise SystemExit(4)')])
        self.init()
        self.assertEqual(self.cli('check')[0], 1)
        self.config([self.check(name='tests', script='print("recovered")')])
        self.assertEqual(self.cli('check')[0], 0)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(len(data['checks']), 2)
        self.assertEqual(len(data['latest_checks']), 1)
        self.assertEqual(len(data['historical_checks']), 1)
        report = self.cli('status')[1]
        self.assertIn('Historical checks (collapsed)', report)
        self.assertEqual(report.count('## Verification'), 1)

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
