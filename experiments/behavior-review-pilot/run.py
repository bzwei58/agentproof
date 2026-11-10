"""Run existing tests, tool-free model inference, then frozen generated probes."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
SYSTEM = ('You are a text-only participant in a code-review evaluation. '
          'You have no tools. Only use the supplied anonymous task, code and tests. '
          'Do not infer hidden labels or claim to have executed code. Return a single JSON object, no Markdown. '
          'Use at most 3000 output tokens. Preserve uncertainty. Do not invent requirements.')
COMMON = '''Evaluate the supplied candidate against the task requirement, only within its stated scope.
No earlier review, commit messages, fix or answer key are provided. Do not treat passing existing
checks as proof of all requirements. If the desired result has no objective oracle, identify the
missing criterion and ask one concrete clarification question rather than inventing a threshold.
'''
ARMS = {
 'review': '''Do a direct static AI review. Do NOT generate or run test code.
Return {"verdict":"issue_found|no_issue_found|needs_clarification", "findings":[
{"title":"...", "source":"...", "expected":"...", "trigger":"...", "actual_prediction":"...",
"impact":"..."}], "question":null, "limits":"..."}. Distinguish a concrete supported issue from speculation.''',
 'probe': '''Propose executable behavior checks rather than a prose code review. Do NOT alter the implementation.
Return {"verdict":"tests_proposed|needs_clarification", "oracle_source":"quote the relevant task requirement",
"scenarios":["..."], "test_code":"Python source or empty string", "question":null, "limits":"..."}.
Use only Python stdlib and this adapter: from probe_support import ProbeCase. Define one or more
unittest.TestCase subclasses of ProbeCase. The runner handles discovery; no __main__ block is needed.
ProbeCase creates an empty temporary Git repository for EACH test and changes into it. It exposes:
self.root (pathlib.Path), self.write(relative_path, text), self.read(relative_path),
self.cli(*args)->(exit_code, stdout_and_stderr), and self.start({relative_path:text,...})->checkpoint_id.
start writes files and calls `init`. CLI calls act on the temporary repo, NOT the source snapshot.
Use self.root for further file setup. Keep all mutations inside self.root, no network, no shell,
no dependencies, no mock of the production behavior under test. Assertions must follow the task
requirement, not the implementation's apparent output. Do not add a new pass criterion for subjective
understanding. Return needs_clarification if the core user outcome cannot be established by code tests.'''
}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def environment():
    env = os.environ.copy()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', PYTHONNOUSERSITE='1')
    # Node CLI does not automatically use macOS system proxy settings.
    for protocol, value in urllib.request.getproxies().items():
        if protocol in {'http', 'https'}:
            env.setdefault(protocol.upper() + '_PROXY', value)
    for name in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'PYTHONPATH'):
        env.pop(name, None)
    return env


def command(argv, cwd, prefix, env=None, timeout=180):
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
        code, output, error = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        code, output, error = 124, str(exc.stdout or ''), str(exc.stderr or '') + '\nExecution timeout.'
    prefix.with_suffix('.stdout.txt').write_text(output, encoding='utf-8')
    prefix.with_suffix('.stderr.txt').write_text(error, encoding='utf-8')
    receipt = {'exit_code': code, 'seconds': round(time.monotonic() - started, 3),
               'stdout_sha256': hashlib.sha256(output.encode()).hexdigest(),
               'stderr_sha256': hashlib.sha256(error.encode()).hexdigest()}
    write(prefix.with_suffix('.receipt.json'), receipt)
    return receipt


def cases(output):
    return sorted(path for path in output.iterdir() if path.is_dir() and (path/'packet.json').is_file())


def baselines(output):
    for case in cases(output):
        path = case/'baseline.receipt.json'
        if path.exists():
            continue
        receipt = command([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                          case/'snapshot', case/'baseline', environment())
        print(case.name, 'existing tests', receipt, flush=True)


def infer(output, case_id=None, timeout=360):
    # Each process is a fresh text inference with NO tools or subagent delegation.
    for case in cases(output):
        if case_id and case.name != case_id:
            continue
        packet = (case/'packet.json').read_text(encoding='utf-8')
        for arm in ('review', 'probe'):
            if (case/f'{arm}.json').exists() or (case/f'{arm}.invalid.json').exists():
                continue
            prompt = COMMON + '\n' + ARMS[arm] + '\nINPUT:\n' + packet
            (case/f'{arm}.prompt.txt').write_text(prompt, encoding='utf-8')
            argv = ['claude', '-p', '--safe-mode', '--tools', '', '--strict-mcp-config',
                    '--no-session-persistence', '--system-prompt', SYSTEM,
                    '--output-format', 'json', '--max-budget-usd', '1.5']
            started = time.monotonic()
            try:
                # A tool-free, history-free prompt; no private label or future source is passed.
                result = subprocess.run(argv, input=prompt, cwd=output, text=True, capture_output=True,
                                        timeout=timeout, env=environment())
            except subprocess.TimeoutExpired as exc:
                for stream in ('stdout', 'stderr'):
                    value = getattr(exc, stream) or b''
                    if isinstance(value, bytes):
                        value = value.decode('utf-8', errors='replace')
                    (case/f'{arm}.timeout.{stream}.txt').write_text(value, encoding='utf-8')
                with (case/f'{arm}.attempts.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'outcome':'timeout','timeout_seconds':timeout,
                              'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})+'\n')
                write(case/f'{arm}.unavailable.json', {'reason':'model inference timeout',
                      'note':'Retained infrastructure failure; any successful retry uses the identical packet.'})
                raise SystemExit('Model timeout; no fabricated replacement review.')
            (case/f'{arm}.raw.json').write_text(result.stdout, encoding='utf-8')
            (case/f'{arm}.stderr.txt').write_text(result.stderr, encoding='utf-8')
            with (case/f'{arm}.attempts.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps({'outcome':'response','exit_code':result.returncode,
                          'seconds':round(time.monotonic()-started,3),
                          'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})+'\n')
            if result.returncode:
                write(case/f'{arm}.unavailable.json', {'reason': 'model call failed', 'exit_code':result.returncode})
                raise SystemExit(f'{case.name} {arm}: model unavailable; inspect the saved stderr.')
            raw = json.loads(result.stdout)
            if raw.get('is_error'):
                raise SystemExit(f'{case.name} {arm}: model returned an error; no substitute review used.')
            answer = raw.get('result', '').strip()
            if answer.startswith('```'):
                answer = answer.split('\n', 1)[1].rsplit('```', 1)[0].strip()
            write(case/f'{arm}.receipt.json', {
                'packet_sha256':hashlib.sha256(packet.encode()).hexdigest(),
                'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'seconds':round(time.monotonic()-started,3),
                'provider':'Claude CLI (tool-free print, configured default model)',
                'model_usage':raw.get('modelUsage'), 'usage':raw.get('usage'),
                'reported_cost_usd':raw.get('total_cost_usd'),
                'tools_available':False, 'history_supplied':False,
                'trials':1, 'same_base_packet_for_both_arms':True})
            try:
                parsed = json.loads(answer)
            except json.JSONDecodeError as exc:
                write(case/f'{arm}.invalid.json', {'reason':'invalid response JSON',
                      'error':str(exc),'original_answer':answer,
                      'note':'Not repaired or resampled; prose may be assessed separately.'})
                print(case.name, arm, 'invalid JSON; original answer preserved', flush=True)
                continue
            write(case/f'{arm}.json', parsed)
            code = parsed.get('test_code', '')
            if arm == 'probe' and code:
                ast.parse(code)
                (case/'generated_probe.py').write_text(code, encoding='utf-8')
            print(case.name, arm, parsed.get('verdict'), flush=True)


def probes(output, case_id=None, target_case=None):
    for case in cases(output):
        if case_id and case.name != case_id:
            continue
        source = case/'generated_probe.py'
        if not source.exists():
            print(case.name,'no executable probe proposed',flush=True)
            continue
        # Run only after the operator has inspected the generated source.
        inspection = case/'probe.inspection.json'
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if not inspection.exists() or json.loads(inspection.read_text()).get('source_sha256') != digest:
            raise SystemExit(f'{case.name}: inspect generated code and record its hash in probe.inspection.json first.')
        target = output/target_case if target_case else case
        env = environment()
        env['PYTHONPATH'] = os.pathsep.join([str((target/'snapshot').resolve()), str(HERE)])
        runner = ('import importlib.util,sys,unittest; '
                  'spec=importlib.util.spec_from_file_location("generated_probe",sys.argv[1]); '
                  'module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module); '
                  'suite=unittest.defaultTestLoader.loadTestsFromModule(module); '
                  'result=unittest.TextTestRunner(verbosity=2).run(suite); '
                  'sys.exit(0 if result.testsRun and result.wasSuccessful() else 1)')
        prefix = case/f'cross_on_{target_case}' if target_case else case/'probe_execution'
        receipt = command([sys.executable, '-c', runner, str(source.resolve())],
                          target/'snapshot', prefix, env)
        receipt['generated_source_sha256'] = digest
        receipt.update(source_case=case.name, target_case=target.name)
        write(prefix.with_suffix('.receipt.json'), receipt)
        print(case.name,'generated probe execution',receipt,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['baseline','models','probes'])
    parser.add_argument('output',type=Path)
    parser.add_argument('--case', choices=['K4','M7','R2'], help='Select one inference case; permits independent runs.')
    parser.add_argument('--model-timeout', type=int, default=360)
    parser.add_argument('--target', choices=['K4','M7','R2'], help='Run inspected probes against a different frozen target.')
    args=parser.parse_args()
    if args.phase == 'models':
        infer(args.output.resolve(), args.case, args.model_timeout)
    elif args.phase == 'probes':
        probes(args.output.resolve(), args.case, args.target)
    else:
        baselines(args.output.resolve())
