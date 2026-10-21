"""Commands used by both hosted workflows and operators recovering saved evidence."""

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from pipeline.archive import Archive, encoded, immutable, inventory, materialize, now, reconcile, sha, verified_download
from pipeline.github import GitHub
from pipeline.gitstore import GitStore
from pipeline.health import audit, full_integrity
from pipeline.hosted import run_hosted_tick, recover_terminals
from pipeline.selection import primary_view, validate_transition_publication

ROOT = Path(__file__).resolve().parents[1]


def context(origin):
    if origin == 'github' and os.environ.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('hosted collection must run in GitHub Actions; dispatch collect.yml')
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
    return {'batchId':datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex,
            'origin':origin, 'codeCommit':commit, 'createdAt':now(), 'python':sys.version,
            'runId':os.environ.get('GITHUB_RUN_ID'), 'runAttempt':os.environ.get('GITHUB_RUN_ATTEMPT'),
            'event':os.environ.get('GITHUB_EVENT_NAME', 'operator'), 'scheduledAt':None}


def store_for(args, branch):
    store = GitStore(Path(args.work) / branch, f'https://github.com/{args.repo}.git', branch)
    store.open()
    return store


def pinned_data(args):
    store = store_for(args, 'data')
    if getattr(args, 'data_sha', None):
        if not re.fullmatch('[0-9a-f]{40}', args.data_sha):
            raise ValueError('data snapshot must be a full Git commit')
        if args.data_sha != store.git('rev-parse', 'HEAD').stdout.strip():
            if store.git('rev-parse', '--is-shallow-repository').stdout.strip() == 'true':
                store.git('fetch', '--unshallow', 'origin', 'data')
            store.git('merge-base', '--is-ancestor', args.data_sha, 'origin/data')
        store.git('fetch', 'origin', args.data_sha)
        store.git('checkout', '--detach', args.data_sha)
    if not list((store.path/'catalog').glob('*.json')):
        raise ValueError('snapshot has no evidence catalog')
    return store, store.git('rev-parse', 'HEAD').stdout.strip()


def update_health_issue(backend, result):
    title = 'Collection health'
    issues = []
    for page in range(1,10001):
        batch = backend.api(f'issues?state=open&per_page=100&page={page}')
        issues.extend(batch)
        if len(batch) < 100: break
    else: raise ValueError('issue pagination bound exceeded')
    current = next((i for i in issues if i['title'] == title and 'pull_request' not in i
                    and (i.get('body') or '').startswith('<!-- health:')
                    and (i.get('user', {}).get('login') == 'github-actions[bot]'
                         or i.get('author_association') in ('OWNER','MEMBER','COLLABORATOR'))), None)
    problems = result['problems']
    marker = '<!-- health:' + sha(encoded(problems)) + ' -->'
    body = f'{marker}\n\nChecked at {result["asOf"]}.\n\n' + '\n'.join('- ' + p for p in problems)
    body += '\n\nSee the reports branch for immutable audit snapshots. GitHub-wide outages can delay this monitor too.'
    if problems and current is None:
        backend.api('issues', 'POST', {'title':title, 'body':body})
    elif problems and marker not in (current.get('body') or ''):
        backend.api(f'issues/{current["number"]}', 'PATCH', {'body':body})
    elif not problems and current:
        backend.api(f'issues/{current["number"]}/comments', 'POST', {'body':f'Collection recovered at audit {result["asOf"]}; prior failures remain in the archive.'})
        backend.api(f'issues/{current["number"]}', 'PATCH', {'state':'closed'})


def evaluate(args, backend):
    from tools.etss import report
    if args.phase != 'development' and (not args.truth_as_of or args.auto):
        raise ValueError('formal reports require an explicit truth snapshot, without auto')
    width_publication = None
    if args.widths:
        from pipeline.publication import validate_widths_publication
        if not args.widths_release or not args.calibration_report: raise ValueError('widths require their immutable release and calibration report')
        width_publication = validate_widths_publication(backend, args.widths_release, Path(args.widths).read_bytes(), Path(args.calibration_report).read_bytes())
    store, data_sha = pinned_data(args)
    run = context('derived')
    directory = Path(args.work) / run['batchId']; directory.mkdir(parents=True)
    raw = directory / 'raw'
    materialize(store.path, raw, backend)
    transition_body = (ROOT / 'protocols/collector-transition.json').read_bytes()
    transition = json.loads(transition_body)
    validate_transition_publication(transition, transition_body, backend)
    selection = primary_view(raw, store.path, directory / 'primary', transition)
    phase, first, last = args.phase, args.first, args.last
    truth = args.truth_as_of or now()
    if args.auto:
        phase, first = 'development', '2026-10-14'
        last = min((datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat(), '2026-10-31')
    result = report.build(directory / 'primary', ROOT / 'inputs/tides.json', phase, truth, first, last,
                          widths_path=args.widths, widths_hash=args.widths_sha256,
                          calibration_path=args.calibration_report)
    provenance = {'codeCommit':run['codeCommit'], 'dataCommit':data_sha, 'truthAsOf':truth,
                  'selection':selection, 'widthPublication':width_publication, 'protocolSha256':sha((ROOT/'protocols/prospective-v1.1.md').read_bytes()),
                  'transitionSha256':sha((ROOT/'protocols/collector-transition.json').read_bytes()),
                  'catalogs':{p.name:sha(p.read_bytes()) for p in sorted((store.path/'catalog').glob('*.json'))}}
    # Scientific fields remain unchanged; host provenance is a separate published document.
    frozen = encoded(report.freeze_widths(result)) if args.freeze_widths else None
    body = encoded(result); compressed = gzip.compress(body, mtime=0)
    tag = 'report-' + run['batchId']
    release = backend.ensure_release(tag, run['codeCommit'])
    asset = backend.upload(release, 'report.json.gz', compressed)
    verified_download(backend, asset, sha(compressed))
    provenance.update(reportSha256=sha(body), storedSha256=sha(compressed), asset=asset, tag=tag)
    if args.freeze_widths:
        width_asset = backend.upload(release, 'widths.json', frozen)
        verified_download(backend, width_asset, sha(frozen))
        provenance.update(widthsSha256=sha(frozen), widthsAsset=width_asset)
    prov_body = encoded(provenance)
    prov_asset = backend.upload(release, 'provenance.json', prov_body)
    verified_download(backend, prov_asset, sha(prov_body))
    published = backend.publish(release)
    if not published.get('immutable'): raise ValueError('report release is not immutable')
    if frozen:
        from pipeline.publication import validate_widths_publication
        validate_widths_publication(backend, tag, frozen, body)
    reports = store_for(args, 'reports')
    reports.write(f'evaluation/{run["batchId"]}/provenance.json', prov_body)
    reports.write(f'evaluation/{run["batchId"]}/report.md', report.markdown(result).encode())
    reports.commit(f'Publish {phase} evaluation {run["batchId"]}')
    return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='openwatersio/noaa-water-level-research')
    parser.add_argument('--work', default='.runtime')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('collect')
    imp = sub.add_parser('import'); imp.add_argument('--source', required=True)
    recover = sub.add_parser('reconcile'); recover.add_argument('--stopped-import-tag')
    artifact = sub.add_parser('recover-artifact'); artifact.add_argument('--source', required=True); artifact.add_argument('--source-batch', required=True)
    restore = sub.add_parser('restore'); restore.add_argument('--output', required=True); restore.add_argument('--data-sha')
    health = sub.add_parser('audit'); health.add_argument('--notify', action='store_true'); health.add_argument('--full', action='store_true')
    ev = sub.add_parser('evaluate')
    ev.add_argument('--data-sha', required=True)
    ev.add_argument('--phase', choices=['development','calibration','confirmation'], default='development')
    for name in ('first','last','truth-as-of','widths','widths-sha256','widths-release','calibration-report'):
        ev.add_argument('--' + name)
    ev.add_argument('--freeze-widths', action='store_true'); ev.add_argument('--auto', action='store_true')
    args = parser.parse_args()
    backend = GitHub(args.repo)
    if args.command == 'evaluate':
        result = evaluate(args, backend)
    elif args.command == 'restore':
        data, revision = pinned_data(args)
        result = {**materialize(data.path, Path(args.output), backend), 'dataCommit':revision}
    elif args.command == 'collect':
        data = store_for(args, 'data')
        run = context('github')
        result = run_hosted_tick(run, Path(args.work)/'receipts', data, backend)
    elif args.command in ('import','recover-artifact'):
        variables = []
        for page in range(1,10001):
            batch = backend.api(f'actions/variables?per_page=100&page={page}')['variables']
            variables.extend(batch)
            if len(batch) < 100: break
        else: raise ValueError('variable pagination bound exceeded')
        if not any(v['name'] == 'COLLECTION_ENABLED' and v['value'] == 'false' for v in variables):
            raise ValueError('set repository COLLECTION_ENABLED=false before imports; use a frozen source directory')
        for workflow in ('collect.yml', 'reconcile.yml'):
            recent = backend.api(f'actions/workflows/{workflow}/runs?per_page=100')['workflow_runs']
            if any(job['status'] != 'completed' for job in recent):
                raise ValueError('wait for queued or active evidence writers before importing')
        data = store_for(args, 'data')
        run = context('legacy')
        if args.command == 'recover-artifact':
            original = json.loads((data.path/'runs'/f'{args.source_batch}-start.json').read_text())
            if original['origin'] != 'github' or not original.get('runId'): raise ValueError('artifact lacks hosted provenance')
            job = backend.api(f'actions/runs/{original["runId"]}')
            if job['status'] != 'completed' or job['head_sha'] != original['codeCommit']: raise ValueError('artifact source run is not verified complete')
            run.update(origin='github', sourceBatchId=original['batchId'], runId=original['runId'],
                       runAttempt=original.get('runAttempt'), sourceCodeCommit=original['codeCommit'])
        before = inventory(Path(args.source))
        run.update(importInventorySha256=sha(encoded(before)), importFileCount=len(before), legacyCollector='6611282c1f22a3304d6138aae1521b58b36960a2')
        if args.command == 'recover-artifact': run.pop('legacyCollector')
        data.write(f'imports/{run["batchId"]}-start.json', encoded(run))
        data.write(f'imports/{run["batchId"]}-inventory.json', encoded(before))
        data.commit(f'Start preserved evidence import {run["batchId"]}')
        # Source should be a frozen inventory snapshot, not the still-running collector directory.
        archive = Archive(Path(args.source), data.path, backend, run, expected_inventory=before)
        result = archive.finish()
        data.commit(f'Import preserved legacy receipts {run["batchId"]}')
        result = {'tag':result['tag'], 'files':len(result['files']), 'dataCommit':data.git('rev-parse','HEAD').stdout.strip()}
    elif args.command == 'reconcile':
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            raise ValueError('dispatch reconcile.yml so recovery shares collection concurrency')
        data = store_for(args, 'data')
        from pipeline.recovery import recover
        result = recover(data, backend, args.stopped_import_tag)
    else:
        data, revision = pinned_data(args)
        result = audit(data.path, now()); result['dataCommit'] = revision
        if result['latestRelease']:
            try:
                release = backend.get_release(result['latestRelease'])
                if not release.get('immutable'): raise ValueError('latest archive is mutable')
                record = next(json.loads(p.read_text()) for p in (data.path/'catalog').glob('*.json')
                              if json.loads(p.read_text())['tag'] == result['latestRelease'])
                from pipeline.archive import record_files
                record_files(record, backend)
            except Exception as error:
                result['problems'].append('archive_integrity:' + type(error).__name__)
                result['state'] = 'action_required'
        run = context('derived')
        reports = store_for(args, 'reports')
        full_integrity(result, data.path, reports.path, Path(args.work)/'full-verification', backend, args.full)
        reports.write(f'health/{run["batchId"]}.json', encoded(result))
        reports.commit(f'Audit collection at {result["asOf"]}')
        if args.notify: update_health_issue(backend, result)
        result['exitCode'] = 0 if result['state'] == 'healthy' else 1
    print(json.dumps(result, indent=2))
    return result.get('exitCode', 0)


if __name__ == '__main__':
    raise SystemExit(main())
