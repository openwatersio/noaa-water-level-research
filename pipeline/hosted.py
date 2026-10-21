"""Finite collection with durable starts and per-product Release checkpoints."""

from pathlib import Path
import json
import sys

from pipeline.archive import Archive, encoded, immutable, now

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/etss'))
import capture
import collect


def run_hosted_tick(context, output, store, backend, collector=collect.collect_tick):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    start = {**context, 'startedAt': now(), 'state': 'started'}
    store.write(f'runs/{context["batchId"]}-start.json', encoded(start))
    store.commit(f'Start acquisition {context["batchId"]}')
    # Previous start records inform the original collector's acquisition-gap diagnostic.
    for catalog in (store.path / 'catalog').glob('*.json'):
        record = json.loads(catalog.read_text())
        if record['context']['origin'] != 'github': continue
        for name in record['files']:
            if name.startswith('ticks/') and name.endswith('-start.json'):
                immutable(output / name, (store.path / 'evidence' / name).read_bytes())
    archive = Archive(output, store.path, backend, context)
    original = capture.capture_product
    result = {'context': context, 'startedAt': start['startedAt'], 'state': 'interrupted',
              'exitCode': 1, 'tick': None, 'archive': None}

    def checkpointed(*args, **kwargs):
        record = original(*args, **kwargs)
        archive.checkpoint()
        return record

    try:
        archive.start()
        capture.capture_product = checkpointed
        result['tick'] = collector(output, timeout=30)
        result['state'] = 'complete' if result['tick']['exitCode'] == 0 else 'acquisition_failed'
        result['exitCode'] = result['tick']['exitCode']
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        capture.capture_product = original
        try:
            record = archive.finish('complete' if result['tick'] is not None else 'interrupted')
            result['archive'] = {'tag': record['tag'], 'releaseId': record['releaseId']}
        except Exception as error:
            result.update(state='publication_failed', exitCode=1,
                          publicationError=f'{type(error).__name__}: {error}')
        result['finishedAt'] = now()
        store.write(f'runs/{context["batchId"]}.json', encoded(result))
        store.commit(f'Preserve acquisition {context["batchId"]}: {result["state"]}')
    return result


def recover_terminals(store):
    from pipeline.health import resolved_runs
    existing = {r['context']['batchId']:r for r in resolved_runs(store.path)}
    recovered = []
    for path in sorted((store.path / 'catalog').glob('*.json')):
        record = json.loads(path.read_text())
        context = record['context']; batch = context.get('sourceBatchId', context['batchId'])
        start = store.path / f'runs/{batch}-start.json'
        if context['origin'] != 'github' or not start.exists(): continue
        previous = existing.get(batch)
        if previous and previous.get('archive') and (not context.get('sourceBatchId') or previous['archive']['tag'] == record['tag']): continue
        ticks = [name for name in record['files'] if name.startswith('ticks/') and name.endswith('.json') and not name.endswith('-start.json')]
        if len(ticks) > 1: raise ValueError('ambiguous recovered terminal tick')
        tick = json.loads((store.path / 'evidence' / ticks[0]).read_text()) if ticks else None
        if previous and previous.get('archive') and not tick: continue
        original = json.loads(start.read_text())
        original_context = {k:v for k,v in original.items() if k not in ('startedAt','state')}
        recovery_at = now()
        result = {'context':original_context, 'startedAt':original['startedAt'],
                  'finishedAt':tick.get('finishedAt', recovery_at) if tick else recovery_at,
                  'recoveredAt':recovery_at, 'terminalRecovered':True,
                  'state':('complete' if tick['exitCode'] == 0 else 'acquisition_failed') if tick else 'interrupted',
                  'exitCode':tick['exitCode'] if tick else 1, 'tick':tick,
                  'archive':{'tag':record['tag'], 'releaseId':record['releaseId']}}
        store.write(f'recoveries/{recovery_at}-{batch}.json', encoded(result))
        store.commit(f'Recover publication status for {batch}')
        existing[batch] = result
        recovered.append(batch)
    return recovered
