"""Hourly acquisition admission; intended clocks never replace original receipt clocks."""

from datetime import timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from pipeline.archive import now
from pipeline.health import utc


def slot_at(value):
    at = utc(value)
    slot = at.replace(minute=50, second=0, microsecond=0)
    if at < slot: slot -= timedelta(hours=1)
    return slot.isoformat().replace('+00:00', 'Z')


def slot_path(slot):
    if slot_at(slot) != slot:
        raise ValueError('slot must be an exact minute-50 UTC boundary')
    return 'scheduling/slots/' + utc(slot).strftime('%Y%m%dT%H%M%SZ') + '.json'


def collection_enabled(repo):
    token = os.environ.get('COLLECTION_CONTROL_TOKEN')
    if not token: raise ValueError('COLLECTION_CONTROL_TOKEN is required for the live collection pause check')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo): raise ValueError('invalid repository')
    response = subprocess.run(['gh', 'api', f'repos/{repo}/actions/variables/COLLECTION_ENABLED'],
                              env={**os.environ, 'GH_TOKEN':token}, capture_output=True, timeout=30)
    if response.returncode:
        raise RuntimeError('cannot verify live collection pause; acquisition denied')
    return json.loads(response.stdout).get('value') == 'true'


def admit(context, data, actions_run, inputs, enabled, as_of=None):
    if not enabled: return {'skip':'collection_paused', 'exitCode':0}
    at = as_of or now()
    inputs = {k:v for k,v in inputs.items() if v}
    scheduling = {'source':context['event'], 'actionsCreatedAt':actions_run['created_at'],
                  'actionsStartedAt':actions_run['run_started_at'], 'admittedAt':at}
    if inputs:
        required = {'slot','dispatch_id','dispatched_at','cron_scheduled_at','expected_commit'}
        if set(inputs) != required or context['event'] != 'workflow_dispatch':
            raise ValueError('external dispatch requires all watchdog intent fields')
        if (str(uuid.UUID(inputs['dispatch_id'])) != inputs['dispatch_id'] or
                not re.fullmatch('[0-9a-f]{40}', inputs['expected_commit']) or
                inputs['expected_commit'] != context['codeCommit']):
            raise ValueError('external identity or reviewed code commit differs')
        slot = inputs['slot']; slot_path(slot)
        if (not utc(slot) <= utc(inputs['dispatched_at']) <= utc(at) or
                utc(inputs['cron_scheduled_at']) > utc(inputs['dispatched_at'])):
            raise ValueError('invalid external dispatch clock order')
        scheduling.update(source='external-watchdog', dispatchId=inputs['dispatch_id'],
                          dispatchedAt=inputs['dispatched_at'], cronScheduledAt=inputs['cron_scheduled_at'],
                          expectedCommit=inputs['expected_commit'])
    else:
        if context['event'] not in ('schedule','workflow_dispatch'):
            raise ValueError('collection requires schedule or workflow_dispatch')
        slot = slot_at(actions_run['created_at'])
    scheduling['slot'] = slot
    utc(actions_run['created_at']); utc(actions_run['run_started_at'])
    starts = [json.loads(p.read_text()) for p in (Path(data)/'runs').glob('*-start.json')]
    if any(str(s.get('runId')) == str(context['runId']) for s in starts):
        return {'skip':'run_already_started', 'slot':slot, 'exitCode':0}
    if slot != slot_at(at): return {'skip':'expired_slot', 'slot':slot, 'exitCode':0}
    if (Path(data)/slot_path(slot)).exists(): return {'skip':'claimed_slot', 'slot':slot, 'exitCode':0}
    if any(slot_at(s['startedAt']) == slot for s in starts):
        return {'skip':'existing_start_in_window', 'slot':slot, 'exitCode':0}
    return {**context, 'scheduling':scheduling}
