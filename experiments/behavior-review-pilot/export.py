"""Export auditable case results, excluding CLI session metadata and credentials."""
import argparse
import hashlib
import json
from pathlib import Path


def export(source, destination):
    labels = json.loads((source / 'private-labels.json').read_text())
    destination.mkdir(parents=True, exist_ok=True)
    for label in labels:
        case = source / label['case_id']
        assert hashlib.sha256((case / 'packet.json').read_bytes()).hexdigest() == label['packet_sha256']
        for name, digest in label['production_sha256'].items():
            assert hashlib.sha256((case / 'snapshot' / name).read_bytes()).hexdigest() == digest
        data = {'case': label, 'model_arms': {}}
        for name in ('baseline.receipt.json', 'known-replay.json', 'probe_execution.receipt.json'):
            if (case / name).exists():
                data[name] = json.loads((case / name).read_text())
        for name in ('baseline.stderr.txt', 'probe_execution.stderr.txt'):
            if (case / name).exists():
                data[name] = (case / name).read_text().replace(str(source.resolve()), '<evidence-root>')
        for path in sorted(case.glob('cross_on_*')):
            if path.name.endswith('.json'):
                data[path.name] = json.loads(path.read_text())
            elif path.name.endswith('.stderr.txt'):
                data[path.name] = path.read_text().replace(str(source.resolve()), '<evidence-root>')
        data['export_note'] = 'Local evidence-root paths in test logs are redacted; receipt hashes identify original local logs.'
        for arm in ('review', 'probe'):
            record = {}
            for suffix in ('json', 'receipt.json', 'invalid.json', 'unavailable.json'):
                path = case / f'{arm}.{suffix}'
                if path.exists():
                    record[suffix] = json.loads(path.read_text())
            path = case / f'{arm}.attempts.jsonl'
            if path.exists():
                record['attempts'] = [json.loads(line) for line in path.read_text().splitlines()]
            record['status'] = ('valid_response' if 'json' in record else
                                'invalid_response' if 'invalid.json' in record else
                                'unavailable' if 'unavailable.json' in record else 'not_attempted')
            data['model_arms'][arm] = record
        path = case / 'generated_probe.py'
        if path.exists():
            data['generated_probe_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (destination / f'{case.name}_probe.py').write_bytes(path.read_bytes())
        (destination / f'{case.name}.json').write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Verified frozen inputs and exported case evidence.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    export(args.source, args.destination)
