"""Audit actual hosted starts and archived observations, not scheduler promises."""

from datetime import datetime, timedelta
import json
from pathlib import Path
from pipeline.archive import materialize

STATIONS = ('9449880', '9444900', '9447130')


def full_integrity(result, data, reports, output, backend, requested):
    previous = []
    for path in (Path(reports) / 'health').glob('*.json'):
        item = json.loads(path.read_text()).get('fullVerification')
        if item and 'checkedAt' in item:
            previous.append(item)
    verification = max(previous, key=lambda item:item['checkedAt']) if previous else None
    if requested:
        verification = {'checkedAt':result['asOf'], 'dataCommit':result['dataCommit']}
        try:
            verification.update(materialize(data, output, backend), state='complete')
        except Exception as error:
            verification.update(state='failed', error=f'{type(error).__name__}: {error}')
    if verification:
        result['fullVerification'] = verification
        if verification['state'] != 'complete':
            result['problems'].append('full_archive_verification_failed')
            result['state'] = 'action_required'


def utc(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.utcoffset() != timedelta(0):
        raise ValueError('UTC timestamp required')
    return at


def resolved_runs(data):
    data = Path(data)
    runs = {p.stem:json.loads(p.read_text()) for p in (data / 'runs').glob('*.json') if not p.name.endswith('-start.json')}
    for p in sorted((data / 'recoveries').glob('*.json')):
        result = json.loads(p.read_text())
        batch = result['context']['batchId']
        if batch in runs: result['originalState'] = runs[batch].get('originalState', runs[batch]['state'])
        runs[batch] = result
    return list(runs.values())


def audit(data, as_of):
    data, current = Path(data), utc(as_of)
    starts = [json.loads(p.read_text()) for p in (data / 'runs').glob('*-start.json')]
    finals = resolved_runs(data)
    starts = sorted((r for r in starts if utc(r['startedAt']) <= current), key=lambda r:r['startedAt'])
    finals = sorted((r for r in finals if utc(r['finishedAt']) <= current), key=lambda r:r['startedAt'])
    catalogs = [json.loads(p.read_text()) for p in (data / 'catalog').glob('*.json')]
    archived = {r['context'].get('sourceBatchId', r['context']['batchId']):r for r in catalogs}
    problems = []
    successes = [r for r in finals if r.get('archive') and r['context']['batchId'] in archived
                 and r.get('tick') and {o['station'] for o in r['tick']['observations']} == set(STATIONS)
                 and all(o['state'] == 'complete' for o in r['tick']['observations'])]
    latest = successes[-1] if successes else None
    if latest is None or (current - utc(latest['finishedAt'])).total_seconds() > 5400:
        problems.append('stale_collection')
    if finals and finals[-1]['state'] != 'complete':
        problems.append('latest_run:' + finals[-1]['state'])
    for r in starts:
        if (current - utc(r['startedAt'])).total_seconds() > 5400 and r['batchId'] not in archived and not any(f['context']['batchId'] == r['batchId'] and f.get('unrecoverableAcknowledged') for f in finals):
            problems.append('unarchived_run:' + r['batchId'])
        elif (current - utc(r['startedAt'])).total_seconds() > 5400 and r['batchId'] not in {f['context']['batchId'] for f in finals}:
            problems.append('missing_terminal:' + r['batchId'])
    samples = {}
    if latest:
        for row in latest['tick']['observations']:
            record = json.loads((data / 'evidence' / row['manifest']).read_text())
            samples[row['station']] = record.get('latestSampleAt')
    for station in STATIONS:
        at = samples.get(station)
        if not at or not 0 <= (current - utc(at)).total_seconds() <= 5400:
            problems.append('stale_observation:' + station)
    missing = []
    if starts:
        slot = utc(starts[0]['startedAt']).replace(minute=50, second=0, microsecond=0)
        if slot < utc(starts[0]['startedAt']): slot += timedelta(hours=1)
        times = [utc(r['startedAt']) for r in starts]
        while slot < current - timedelta(minutes=15):
            if not any(slot <= at < slot + timedelta(hours=1) for at in times):
                missing.append(slot.isoformat().replace('+00:00','Z'))
            slot += timedelta(hours=1)
    return {'asOf':as_of, 'state':'action_required' if problems else 'healthy', 'problems':problems,
            'latestArchivedObservationRunAt':latest['finishedAt'] if latest else None,
            'samples':samples, 'hostedStarts':len(starts), 'terminalRuns':len(finals),
            'missedSlots':missing, 'slotDefinition':'Hourly minute-50 windows without an actual start; not proof of provider failure or scheduler delivery time.',
            'latestRelease':latest['archive']['tag'] if latest else None,
            'latestForecastStates':finals[-1].get('tick', {}).get('forecasts', []) if finals and finals[-1].get('tick') else [],
            'catalogCount':len(catalogs), 'originalBytes':sum(v['bytes'] for c in catalogs for v in c['files'].values()),
            'decisionAcquisition':decision_acquisition(data, current, catalogs),
            'acknowledgedGaps':[{'batchId':r['context']['batchId'],'startedAt':r['startedAt'],'note':r['recoveryNote']} for r in finals if r.get('unrecoverableAcknowledged')],
            'monitoringLimit':'This audit shares GitHub availability with collection.'}


def decision_acquisition(data, current, catalogs):
    origins = {name:c['context'].get('origin') for c in catalogs for name in c['files']}
    observations, forecasts = [], []
    for name, origin in origins.items():
        parts = Path(name).parts
        if len(parts) != 2 or not name.endswith('.json') or name.endswith('-contract.json') or parts[0] not in ('observations','forecasts'): continue
        document = json.loads((data / 'evidence' / name).read_text())
        if document.get('record', {}).get('state') == 'complete':
            observations.append((origin, document))
        products = document.get('products', {})
        if set(products) == {'shef','csv'} and all(p.get('state') == 'complete' and p.get('receivedAt') for p in products.values()):
            forecasts.append((origin, document))
    rows = []
    for days in (1, 0):
        for hour in (2, 14):
            decision = (current - timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if decision > current: continue
            for origin in ('legacy','github'):
                pairs = [d for o,d in forecasts if o == origin and all(decision-timedelta(hours=24) <= utc(p['receivedAt']) <= decision for p in d['products'].values())]
                stations = {s:max((d['record']['receivedAt'] for o,d in observations if o == origin and d.get('station') == s
                                  and d['record'].get('receivedAt') and decision-timedelta(hours=24) <= utc(d['record']['receivedAt']) <= decision), default=None) for s in STATIONS}
                rows.append({'decisionAt':decision.isoformat().replace('+00:00','Z'), 'origin':origin,
                             'completePairReceiptsInPrior24h':len(pairs), 'observationReceipts':stations,
                             'meaning':'Acquisition candidates only; scientific eligibility and primary-source filtering are evaluated in the pinned report.'})
    return rows
