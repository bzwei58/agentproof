import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentproof.cli import main
from agentproof.junit import parse_junit
from helpers import cleanup_temp


PASS = '<testsuite tests="1"><testcase classname="PaymentTest" name="test_retry"/></testsuite>'
MIXED = ('<testsuites tests="4" failures="1" errors="1" skipped="1"><testsuite tests="4" failures="1" errors="1" skipped="1">'
         '<testcase name="pass"/><testcase name="fail" file="payment.py" line="12"><failure message="retry overflow"/></testcase>'
         '<testcase name="error"><error>connection unavailable</error></testcase>'
         '<testcase name="skip"><skipped/></testcase></testsuite></testsuites>')


class JUnitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'app.py').write_text('pass\n', encoding='utf-8')
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
        self.assertEqual(self.cli('init', '--goal', 'Review test evidence')[0], 0)

    def command(self, xml):
        return [sys.executable, '-c', 'import sys; from pathlib import Path; '
                f'Path(sys.argv[1]).write_text({xml!r}, encoding="utf-8")', '{junit}']

    def run_xml(self, xml, expected=0):
        code, output = self.cli('verify', '--kind', 'test', '--junit', '--', *self.command(xml))
        self.assertEqual(code, expected, output)
        return json.loads(self.cli('status', '--json')[1])

    def test_nested_suite_counts_and_failure_locations(self):
        source = self.root / 'input.xml'
        source.write_text(MIXED, encoding='utf-8')
        result = parse_junit(source)
        self.assertEqual(result['counts'], {'tests': 4, 'passed': 1, 'failures': 1, 'errors': 1, 'skipped': 1})
        self.assertEqual(result['problems'][0]['file'], 'payment.py')
        self.assertEqual(result['problems'][0]['line'], '12')
        self.assertEqual(result['problems'][1]['message'], 'connection unavailable')

    def test_invalid_reports_are_rejected(self):
        source = self.root / 'input.xml'
        invalid = ['<bad/>', '<testsuite>',
                   '<testsuite tests="-1"/>', '<testsuite tests="nan"/>',
                   '<testsuite><testcase><failure/><skipped/></testcase></testsuite>',
                   '<testsuite><testcase status="unknown"/></testsuite>',
                   '<testsuite><testcase status="passed"><failure/></testcase></testsuite>',
                   '<!DOCTYPE testsuite [<!ENTITY x "value">]><testsuite/>',
                   '<testsuite>' + '<testsuite>' * 34 + '</testsuite>' * 34 + '</testsuite>']
        for xml in invalid:
            with self.subTest(xml=xml[:80]):
                source.write_text(xml, encoding='utf-8')
                with self.assertRaises(ValueError):
                    parse_junit(source)
        source.write_bytes(b'x' * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '8 MiB'):
            parse_junit(source)

    def test_notrun_metadata_is_never_counted_as_passed(self):
        source = self.root / 'input.xml'
        source.write_text('<testsuite><testcase status="notrun" result="suppressed"/></testsuite>', encoding='utf-8')
        result = parse_junit(source)
        self.assertEqual(result['counts']['passed'], 0)
        self.assertEqual(result['counts']['skipped'], 1)

    def test_declared_and_record_counts_are_preserved_when_inconsistent(self):
        self.init()
        data = self.run_xml('<testsuite name="subtests" tests="9"><testcase name="one"/></testsuite>', expected=1)
        result = data['checks'][0]['test_results']
        self.assertEqual(result['status'], 'INCONSISTENT')
        self.assertEqual(result['counts']['tests'], 1)
        self.assertEqual(result['count_mismatches'][0]['declared'], 9)
        self.assertFalse(data['checks'][0]['usable'])
        self.assertTrue(any('declared tests=9, testcase records=1' in item for item in data['attention']))

    def test_namespaced_report_and_failure_detail_limit(self):
        source = self.root / 'input.xml'
        source.write_text('<testsuite xmlns="urn:junit" tests="25" failures="25">'
                          + '<testcase name="case"><failure message="bad"/></testcase>' * 25
                          + '</testsuite>', encoding='utf-8')
        result = parse_junit(source)
        self.assertEqual(result['counts']['failures'], 25)
        self.assertEqual(len(result['problems']), 20)
        self.assertTrue(result['problems_truncated'])

    def test_passing_report_remains_scoped_evidence(self):
        self.init()
        data = self.run_xml(PASS)
        check = data['checks'][0]
        self.assertEqual(check['test_results']['counts']['passed'], 1)
        self.assertTrue(check['usable'])
        output = self.cli('review', '--claim', 'All tests passed')[1]
        self.assertIn('UNVERIFIED: All tests passed', output)
        self.assertIn('Receipt:', output)
        self.assertIn('1 tests', self.cli('status', '--brief')[1])

    def test_reported_failure_overrides_zero_command_exit(self):
        self.init()
        data = self.run_xml(MIXED, expected=1)
        self.assertEqual(data['checks'][0]['execution'], 'PASS')
        self.assertFalse(data['checks'][0]['usable'])
        self.assertTrue(any('payment.py::fail' in item for item in data['attention']))

    def test_zero_and_skipped_only_results_require_review(self):
        self.init()
        for xml in ['<testsuite tests="0"/>',
                    '<testsuite tests="1" skipped="1"><testcase name="skip"><skipped/></testcase></testsuite>']:
            data = self.run_xml(xml, expected=1)
            self.assertFalse(data['checks'][-1]['usable'])
            self.assertTrue(any('No executed test cases' in item for item in data['attention']))

    def test_old_report_cannot_fill_missing_current_output(self):
        (self.root / 'old.xml').write_text(PASS, encoding='utf-8')
        self.init()
        code, output = self.cli('verify', '--kind', 'test', '--junit', '--', sys.executable,
                                '-c', 'import sys; print(sys.argv[1])', '{junit}')
        self.assertEqual(code, 1, output)
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['checks'][0]['test_results']['status'], 'MISSING')
        self.assertFalse(data['checks'][0]['usable'])

    def test_modified_or_deleted_xml_revokes_usable_evidence(self):
        self.init()
        data = self.run_xml(PASS)
        result = self.root / data['checks'][0]['test_results']['path']
        result.write_text('<testsuite tests="0"/>', encoding='utf-8')
        data = json.loads(self.cli('status', '--json')[1])
        self.assertEqual(data['checks'][0]['test_results']['status'], 'MODIFIED')
        self.assertFalse(data['checks'][0]['usable'])
        result.unlink()
        self.assertEqual(json.loads(self.cli('status', '--json')[1])['checks'][0]['test_results']['status'], 'MISSING')

    def test_repeated_direct_commands_have_unique_outputs_but_one_latest_result(self):
        self.init()
        self.run_xml(PASS)
        data = self.run_xml(PASS)
        paths = [check['test_results']['path'] for check in data['checks']]
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(len(data['latest_checks']), 1)

    def test_configured_junit_and_archive_preserve_source_xml(self):
        config = {'version': 1, 'checks': [{'name': 'tests', 'kind': 'test', 'result_format': 'junit',
                                          'command': self.command(PASS)}]}
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.init()
        self.assertEqual(self.cli('check')[0], 0)
        command = json.loads(self.cli('status', '--json')[1])['checks'][0]['command']
        self.assertEqual(self.cli('finish')[0], 0)
        archive = next((self.root / '.agentproof/history').iterdir())
        self.assertEqual(len(list((archive / 'results').glob('*.xml'))), 1)
        report = (archive / 'report.md').read_text(encoding='utf-8')
        self.assertIn('tests: 1', report)
        self.assertIn('source: `results/', report)
        self.assertIn(command, report)

    def test_placeholder_and_format_must_agree_before_execution(self):
        self.init()
        code, output = self.cli('verify', '--junit', '--', sys.executable, '-c', 'print("not run")')
        self.assertEqual(code, 2, output)
        code, output = self.cli('verify', '--', *self.command(PASS))
        self.assertEqual(code, 2, output)
        config = {'version': 1, 'checks': [{'name': 'tests', 'kind': 'test', 'result_format': 'junit',
                                          'command': [sys.executable, '-c', 'print("not run")']}]}
        (self.root / 'agentproof.json').write_text(json.dumps(config), encoding='utf-8')
        self.assertEqual(self.cli('check')[0], 2)


if __name__ == '__main__':
    unittest.main()
