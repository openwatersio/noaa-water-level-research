import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline.health'), 'health audit missing')
        from pipeline.health import audit
        self.audit = audit
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def put(self, name, value):
        p = self.data / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))

    def good(self):
        self.put('runs/run-start.json', {'batchId':'run','startedAt':'2026-10-21T12:50:00Z'})
        self.put('runs/run.json', {'context':{'batchId':'run'},'startedAt':'2026-10-21T12:50:00Z',
                 'finishedAt':'2026-10-21T12:51:00Z','state':'complete','archive':{'tag':'receipts-run'},
                 'tick':{'state':'complete_with_unpublished','observations':[
                     {'station':s,'state':'complete','manifest':f'observations/{s}.json'}
                     for s in ('9449880','9444900','9447130')],
                     'forecasts':[{'state':'expected_unpublished','contractStatus':None}]}})
        self.put('catalog/run.json', {'context':{'batchId':'run','origin':'github'},'tag':'receipts-run',
                                    'completion':'complete','files':{},'bundles':[]})
        for s in ('9449880','9444900','9447130'):
            self.put(f'evidence/observations/{s}.json', {'latestSampleAt':'2026-10-21T12:48:00Z'})

    def test_no_runs_is_not_healthy(self):
        self.assertEqual(self.audit(self.data, '2026-10-21T13:25:00Z')['state'], 'action_required')

    def test_expected_unpublished_is_healthy_with_fresh_observations(self):
        self.good()
        self.assertEqual(self.audit(self.data, '2026-10-21T13:25:00Z')['state'], 'healthy')

    def test_absent_scheduled_run_and_stale_sample_are_visible(self):
        self.good()
        result = self.audit(self.data, '2026-10-21T15:25:00Z')
        self.assertEqual(result['state'], 'action_required')
        self.assertIn('stale_collection', result['problems'])
        self.assertEqual(result['missedSlots'], ['2026-10-21T13:50:00Z','2026-10-21T14:50:00Z'])

    def test_green_run_with_stale_samples_is_unhealthy(self):
        self.good()
        self.put('evidence/observations/9449880.json', {'latestSampleAt':'2026-10-21T10:00:00Z'})
        self.assertIn('stale_observation:9449880', self.audit(self.data,'2026-10-21T13:25:00Z')['problems'])

    def test_interrupted_start_requires_reconciliation(self):
        self.good()
        self.put('runs/lost-start.json', {'batchId':'lost','startedAt':'2026-10-21T10:50:00Z'})
        result = self.audit(self.data, '2026-10-21T13:25:00Z')
        self.assertIn('unarchived_run:lost', result['problems'])

    def test_archived_start_without_terminal_record_is_not_silently_healthy(self):
        self.good()
        (self.data / 'runs/run.json').unlink()
        result = self.audit(self.data, '2026-10-21T15:25:00Z')
        self.assertIn('missing_terminal:run', result['problems'])

    def test_contract_diagnostic_does_not_double_count_forecast_pair(self):
        from pipeline.health import decision_acquisition, utc
        products={k:{'state':'complete','receivedAt':'2026-10-21T12:00:00Z'} for k in ('shef','csv')}
        files={'forecasts/pair.json':{},'forecasts/pair-contract.json':{}}
        for name in files: self.put('evidence/'+name,{'products':products})
        rows=decision_acquisition(self.data,utc('2026-10-21T14:01:00Z'),[{'context':{'origin':'github'},'files':files}])
        row=next(r for r in rows if r['origin']=='github' and r['decisionAt']=='2026-10-21T14:00:00Z')
        self.assertEqual(row['completePairReceiptsInPrior24h'],1)
