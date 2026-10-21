"""Verify the retained development report against restored original receipts."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/etss'))
import report


def normalize(value):
    if isinstance(value, dict):
        return {k:normalize(v) for k,v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str) and value.startswith('/'):
        for kind in ('observations', 'forecasts', 'ticks'):
            if '/' + kind + '/' in value:
                return kind + '/' + value.split('/' + kind + '/', 1)[1]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(',',':')).encode()).hexdigest()


def verify(root):
    reference = json.loads((ROOT / 'fixtures/development-reference.json').read_text())
    root = Path(root)
    for name, expected in reference['manifestInventory'].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'reference manifest differs: {name}')
    with tempfile.TemporaryDirectory(dir=root.parent) as temp:
        view = Path(temp)
        for p in root.rglob('*'):
            if not p.is_file(): continue
            relative = p.relative_to(root)
            if (len(relative.parts) == 2 and relative.parts[0] in ('observations','forecasts')
                    and relative.as_posix() not in reference['manifestInventory']): continue
            target = view / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(p, target)
        result = report.build(view, ROOT / 'inputs/tides.json', 'development', reference['truthAsOf'],
                              '2026-10-14', '2026-10-16')
    expected_fields = set(reference) - {'manifestInventory','referenceReportSha256','selectedInputSha256','labelsSha256'}
    if expected_fields - result.keys(): raise ValueError('replay is missing required scientific fields')
    actual = {key:result[key] for key in expected_fields}
    actual.update(selectedInputSha256=digest(result['inputs']), labelsSha256=digest(result['labels']))
    for key, value in actual.items():
        if value != reference[key]:
            raise ValueError(f'development replay differs: {key}')
    return {'state':'equal', 'referenceReportSha256':reference['referenceReportSha256'],
            'selectedInputSha256':actual['selectedInputSha256'], 'labelsSha256':actual['labelsSha256'],
            'normalization':'Only absolute evidence-directory prefixes; all receipt IDs, times and hashes remain compared.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    print(json.dumps(verify(parser.parse_args().root), indent=2))
