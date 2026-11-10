"""Freeze anonymous historical cases. Private labels never enter model prompts."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_TESTS = '12069c84ccbf44bcacc016a8d7ee971f70ba4967'
CASES = [
    ('K4', 'c2279458a5ba91ebc6c4eefd54c99e2c3d433021', 'correct_for_scoped_contract'),
    ('M7', 'f502b06ddb12e20ee2680c2c07ce56bc71172ba6', 'ambiguous_human_comprehension'),
    ('R2', HISTORICAL_TESTS, 'historical_defect'),
]
CONTRACT = (
    'When restoring a snapshot, validate snapshot content and filesystem path conflicts '
    'before changing any project file. A refused restore must leave project files unchanged. '
    'Missing directories may be recreated. Do not delete files created after the snapshot '
    'or modify the Git index. This concerns validation conflicts, not atomicity under arbitrary '
    'write failures or concurrent edits.'
)


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def source(ref, name):
    return git('show', f'{ref}:{name}').decode('utf-8')


def freeze(output):
    output.mkdir(parents=True, exist_ok=False)
    labels = []
    for ident, revision, kind in CASES:
        case = output / ident
        snapshot = case / 'snapshot'
        snapshot.mkdir(parents=True)
        names = git('ls-tree', '-r', '--name-only', revision).decode().splitlines()
        for name in names:
            if name.startswith(('agentproof/', 'tests/')) and name.endswith('.py'):
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                ref = HISTORICAL_TESTS if kind != 'ambiguous_human_comprehension' and name.startswith('tests/') else revision
                try:
                    target.write_text(source(ref, name), encoding='utf-8')
                except subprocess.CalledProcessError:
                    raise ValueError(f'Cannot freeze {ref}:{name}')
        # No history or fix descriptions are exposed to either model arm.
        production = {p.relative_to(snapshot).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (snapshot / 'agentproof').rglob('*.py')}
        if kind == 'ambiguous_human_comprehension':
            requirement = '让读报告的人先看懂：这次改了什么、检查发现了什么、还需要自己判断什么；降低审查门槛。'
            context = {'agentproof/presentation.py': source(revision, 'agentproof/presentation.py')}
            cli = source(revision, 'agentproof/cli.py')
            tree = ast.parse(cli)
            context['agentproof/cli.py (excerpt)'] = '\n\n'.join(ast.get_source_segment(cli, node)
                for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'brief_report', 'report'})
            tests = source(revision, 'tests/test_workflow.py')
            tree = ast.parse(tests)
            context['available_tests (excerpt)'] = '\n\n'.join(ast.get_source_segment(tests, node)
                for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in {
                    'test_plain_report_puts_review_before_folded_evidence',
                    'test_chinese_report_language_persists_and_display_override_does_not_mutate'})
        else:
            requirement = CONTRACT
            context = {'agentproof/cli.py': source(revision, 'agentproof/cli.py'),
                       'available_tests': source(HISTORICAL_TESTS, 'tests/test_cli.py')}
        packet = {'case_id': ident, 'task_requirement': requirement, 'context': context}
        text = json.dumps(packet, ensure_ascii=False, indent=2) + '\n'
        (case / 'packet.json').write_text(text, encoding='utf-8')
        labels.append({'case_id': ident, 'revision': revision, 'label': kind,
                       'baseline_tests_revision': revision if kind == 'ambiguous_human_comprehension' else HISTORICAL_TESTS,
                       'packet_sha256': hashlib.sha256(text.encode()).hexdigest(),
                       'production_sha256': production,
                       'contract_origin': ('Retrospectively scoped from c227945 README restore guarantee; not a prospectively frozen original requirement.'
                                           if kind != 'ambiguous_human_comprehension' else
                                           'Historical 0.8.1 task goal; understanding and review effort lack an operational oracle.')})
    (output / 'private-labels.json').write_text(json.dumps(labels, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    freeze(Path(sys.argv[1]).resolve())
