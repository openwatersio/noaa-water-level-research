"""Recover independent archives without finalizing an active collector or import."""

from datetime import timedelta
import json
from pipeline.archive import encoded, now, reconcile
from pipeline.health import resolved_runs, utc
from pipeline.hosted import recover_terminals


def recover(store, backend, stopped_import_tag=None):
    known = {json.loads(p.read_text())['tag'] for p in (store.path/'catalog').glob('*.json')}
    releases = {r['tag_name']:r for r in backend.all_releases()}
    result = {'recovered':[], 'deferred':[], 'errors':[]}
    for tag, release in releases.items():
        if not tag.startswith('receipts-') or tag.startswith('receipts-test-') or tag in known: continue
        try:
            if release['draft']:
                batch = tag.removeprefix('receipts-')
                start = store.path / f'runs/{batch}-start.json'
                if start.exists():
                    context = json.loads(start.read_text())
                    if not context.get('runId'): raise ValueError('cannot verify acquisition job is stopped')
                    job = backend.api(f'actions/runs/{context["runId"]}')
                    if job['status'] != 'completed':
                        result['deferred'].append(tag); continue
                elif tag != stopped_import_tag:
                    result['deferred'].append(tag); continue
            record = reconcile(tag, store.path, backend)
            store.commit(f'Reconcile original evidence {tag}')
            result['recovered'].append({'tag':tag,'completion':record['completion']})
        except Exception as error:
            result['errors'].append({'tag':tag,'error':str(error)})
    result['terminalRecords'] = recover_terminals(store)
    existing = {r['context']['batchId']:r for r in resolved_runs(store.path)}
    for path in (store.path/'runs').glob('*-start.json'):
        start = json.loads(path.read_text()); batch = start['batchId']
        if 'receipts-' + batch in releases or existing.get(batch, {}).get('archive') or existing.get(batch, {}).get('unrecoverableAcknowledged'): continue
        if utc(now()) - utc(start['startedAt']) <= timedelta(minutes=90): continue
        try:
            if not start.get('runId') or backend.api(f'actions/runs/{start["runId"]}')['status'] != 'completed': continue
            at = now()
            record = {**existing.get(batch, {}), 'context':{k:v for k,v in start.items() if k not in ('startedAt','state')},
                      'startedAt':start['startedAt'], 'finishedAt':existing.get(batch, {}).get('finishedAt', at),
                      'recoveredAt':at, 'unrecoverableAcknowledged':True, 'state':'interrupted', 'exitCode':1,
                      'archive':None, 'recoveryNote':'No Release evidence found after job completion. Missing original receipts were not reconstructed.'}
            store.write(f'recoveries/{at}-{batch}.json', encoded(record))
            store.commit(f'Record unrecoverable acquisition gap {batch}')
        except Exception as error:
            result['errors'].append({'batchId':batch,'error':str(error)})
    result['exitCode'] = 1 if result['errors'] else 0
    return result
