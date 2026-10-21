"""Translate a manual JSON request or scheduled development refresh into pinned CLI inputs."""

from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys

from pipeline.archive import immutable, sha
from pipeline.github import GitHub


def arguments(request, backend, work):
    allowed = {'data_sha','phase','first','last','truth_as_of','widths_asset','widths_sha256',
               'calibration_asset','calibration_sha256','freeze_widths','auto'}
    if set(request) - allowed:
        raise ValueError('unknown evaluation request fields')
    if request.get('phase', 'development') != 'development':
        if request.get('auto') or not request.get('data_sha') or not request.get('truth_as_of'):
            raise ValueError('formal reports require explicit data and truth pins, without auto')
    data_sha = request.get('data_sha') or backend.api('git/ref/heads/data')['object']['sha']
    args = ['--work', str(work), 'evaluate', '--data-sha', data_sha]
    for key in ('phase','first','last','truth_as_of'):
        if key in request:
            if not isinstance(request[key], str): raise ValueError('evaluation values must be strings')
            args += ['--' + key.replace('_','-'), request[key]]
    for key in ('freeze_widths','auto'):
        if request.get(key) is True: args.append('--' + key.replace('_','-'))
    for kind, option in (('widths','widths'), ('calibration','calibration-report')):
        if kind + '_asset' not in request: continue
        digest = request.get(kind + '_sha256')
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError('a source asset requires its original SHA-256')
        body = backend.download({'id':request[kind + '_asset']})
        if kind == 'calibration': body = gzip.decompress(body)
        if sha(body) != digest: raise ValueError('evaluation source asset differs')
        target = work / (kind + '-' + digest + '.json')
        immutable(target, body)
        args += ['--' + option, str(target)]
        if kind == 'widths': args += ['--widths-sha256', digest]
        if kind == 'widths':
            release = next((r for r in backend.all_releases()
                            if any(a['id'] == request['widths_asset'] for a in r.get('assets', []))), None)
            if release is None: raise ValueError('widths release not found')
            args += ['--widths-release', release['tag_name']]
    return args


def main():
    if os.environ.get('GITHUB_EVENT_NAME') == 'schedule':
        if datetime.now(timezone.utc).date().isoformat() > '2026-10-31':
            print('Automatic development refresh ended; formal phases require explicit pinned requests.')
            return 0
        request = {'auto':True}
    else:
        request = json.loads(os.environ['EVALUATION_REQUEST'])
    work = Path(os.environ.get('RUNNER_TEMP', '.runtime')) / 'evaluation'
    args = arguments(request, GitHub('openwatersio/noaa-water-level-research'), work)
    return subprocess.call([sys.executable, '-B', '-m', 'pipeline', *args])


if __name__ == '__main__':
    raise SystemExit(main())
