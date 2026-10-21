"""Verify the retained development report against restored original receipts."""

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/etss'))
import report

NUMERIC_ABSOLUTE_TOLERANCE = 1e-14


def compare(expected, actual, path):
    if type(expected) is not type(actual):
        raise ValueError(f'development replay differs: {path} (type)')
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            raise ValueError(f'development replay differs: {path} (keys)')
        return [item for key in sorted(expected) for item in compare(expected[key], actual[key], path + '/' + key)]
    if isinstance(expected, list):
        if len(expected) != len(actual):
            raise ValueError(f'development replay differs: {path} (length)')
        return [item for i, (a, b) in enumerate(zip(expected, actual)) for item in compare(a, b, path + '/' + str(i))]
    if isinstance(expected, float) and not (math.isfinite(expected) and math.isfinite(actual)):
        raise ValueError(f'development replay differs: {path} (nonfinite)')
    if expected == actual:
        return []
    leaf = path.split('/')[-1]
    metric = path.split('/')[-2] if leaf.isdecimal() else leaf
    score_output = path.startswith('scores/') and metric in {
        'mae', 'rmse', 'p95', 'max', 'maeDifference', 'rmseDifference',
        'maeDifferenceRange', 'rmseDifferenceRange'}
    numeric_output = score_output or (path.startswith('cases/') and
                      '/methods/' in path and path.endswith('/heightMeters'))
    if numeric_output and isinstance(expected, float) and abs(expected - actual) <= NUMERIC_ABSOLUTE_TOLERANCE:
        return [{'path':path, 'expected':expected, 'actual':actual, 'absoluteDifference':abs(expected - actual)}]
    raise ValueError(f'development replay differs: {path}')


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


def input_digest(inputs, cases):
    canonical = copy.deepcopy(inputs)
    differences = []
    for position, prepared in enumerate(canonical):
        for station, data in prepared['stations'].items():
            for target_index, target in enumerate(data['targets']):
                horizon = str(target['horizonHours'])
                matches = [row for row in cases[station][horizon]
                           if row['decisionAt'] == prepared['decisionAt'] and row['validAt'] == target['validAt']]
                if len(matches) != 1:
                    raise ValueError('input prediction lacks a unique reference case')
                expected = matches[0]['methods']
                path = f'cases/{station}/{horizon}/{prepared["decisionAt"]}/methods'
                for difference in compare(expected, target['methods'], path):
                    difference['path'] = difference['path'].replace(path, f'inputs/{position}/stations/{station}/targets/{target_index}/methods', 1)
                    differences.append(difference)
                # The historical input digest includes these derived outputs as well as receipts.
                for method, value in expected.items():
                    target['methods'][method]['heightMeters'] = value['heightMeters']
    return digest(canonical), differences


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
    input_hash, input_differences = input_digest(result['inputs'], reference['cases'])
    actual.update(selectedInputSha256=input_hash, labelsSha256=digest(result['labels']))
    differences = [item for key, value in actual.items() for item in compare(reference[key], value, key)]
    return {'state':'equal_with_numeric_roundoff' if differences or input_differences else 'equal',
            'numericAbsoluteTolerance':NUMERIC_ABSOLUTE_TOLERANCE, 'numericDifferences':differences,
            'inputPredictionDifferences':input_differences, 'unmodifiedInputSha256':digest(result['inputs']),
            'referenceReportSha256':reference['referenceReportSha256'],
            'selectedInputSha256':actual['selectedInputSha256'], 'labelsSha256':actual['labelsSha256'],
            'normalization':'Absolute evidence-directory prefixes; derived input prediction heights use reference values for the legacy checksum only after bounded comparison. Receipt IDs, times, hashes, truth, labels and verdicts remain exact. Every finite prediction/score difference is reported.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    print(json.dumps(verify(parser.parse_args().root), indent=2))
