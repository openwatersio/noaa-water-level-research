import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_hosted import Store
from test_archive import MemoryGitHub
from pipeline.archive import encoded


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data'); self.store.path.mkdir()
        self.context = {'batchId':'new', 'origin':'github', 'codeCommit':'a'*40,
                        'runId':'42', 'runAttempt':'1', 'event':'schedule', 'scheduledAt':None}
        self.run = {'created_at':'2026-11-01T12:51:00Z', 'run_started_at':'2026-11-01T12:52:00Z'}
        self.inputs = {'slot':'2026-11-01T12:50:00Z', 'dispatch_id':'5a858471-9466-4e66-b76c-dea8d0485550',
                       'dispatched_at':'2026-11-01T12:50:01Z', 'cron_scheduled_at':'2026-11-01T12:50:00Z',
                       'expected_commit':'a'*40}

    def admit(self, at='2026-11-01T12:52:01Z', inputs=None, enabled=True):
        from pipeline.scheduling import admit
        return admit(self.context, self.store.path, self.run, inputs or {}, enabled, at)

    def test_slot_boundaries_and_native_unknown_schedule_clock(self):
        from pipeline.scheduling import slot_at
        self.assertEqual(slot_at('2026-11-01T12:49:59Z'), '2026-11-01T11:50:00Z')
        self.assertEqual(slot_at('2026-11-01T12:50:00Z'), '2026-11-01T12:50:00Z')
        result = self.admit()
        self.assertEqual(result['scheduling']['slot'], '2026-11-01T12:50:00Z')
        self.assertIsNone(result['scheduledAt'])
        self.assertEqual(result['scheduling']['actionsStartedAt'], '2026-11-01T12:52:00Z')

    def test_pause_expired_queue_and_incomplete_external_inputs_skip_or_reject(self):
        self.assertEqual(self.admit(enabled=False)['skip'], 'collection_paused')
        self.assertEqual(self.admit(at='2026-11-01T13:50:00Z')['skip'], 'expired_slot')
        self.context['event'] = 'workflow_dispatch'
        for field in self.inputs:
            invalid = {**self.inputs}; invalid.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError): self.admit(inputs=invalid)
        with self.assertRaises(ValueError): self.admit(inputs={**self.inputs, 'expected_commit':'b'*40})
        with self.assertRaises(ValueError): self.admit(inputs={**self.inputs, 'slot':'2026-11-01T12:49:00Z'})
        with self.assertRaises(ValueError): self.admit(inputs={**self.inputs, 'dispatched_at':'2026-11-01T12:55:00Z'})

    def test_existing_claim_blocks_native_external_and_rerun(self):
        self.store.write('scheduling/slots/20261101T125000Z.json', encoded({'batchId':'original'}))
        self.assertEqual(self.admit()['skip'], 'claimed_slot')
        self.context['event'] = 'workflow_dispatch'
        self.assertEqual(self.admit(inputs=self.inputs)['skip'], 'claimed_slot')

    def test_historical_start_blocks_refetch_and_rerun_across_hours(self):
        self.store.write('runs/old-start.json', encoded({'runId':'42', 'startedAt':'2026-11-01T11:51:00Z'}))
        self.assertEqual(self.admit()['skip'], 'run_already_started')
        self.context['runId'] = '43'
        self.store.write('runs/other-start.json', encoded({'runId':'41', 'startedAt':'2026-11-01T12:51:00Z'}))
        self.assertEqual(self.admit()['skip'], 'existing_start_in_window')

    def test_external_identity_and_slot_are_in_durable_start_before_noaa(self):
        from pipeline.hosted import run_hosted_tick
        self.context['event'] = 'workflow_dispatch'
        admitted = self.admit(inputs=self.inputs)
        commits = []
        self.store.commit = lambda message: commits.append(message)
        def collector(root, **kwargs):
            claim = json.loads((self.store.path/'scheduling/slots/20261101T125000Z.json').read_text())
            start = json.loads((self.store.path/'runs/new-start.json').read_text())
            self.assertEqual(claim['batchId'], 'new')
            self.assertEqual(claim['scheduling'], start['scheduling'])
            self.assertEqual(start['event'], 'workflow_dispatch')
            self.assertEqual(start['scheduling']['dispatchId'], self.inputs['dispatch_id'])
            self.assertEqual(len(commits), 1)
            raise RuntimeError('stop synthetic acquisition')
        with patch('pipeline.hosted.now', return_value='2026-11-01T12:52:02Z'):
            run_hosted_tick(admitted, Path(self.temp.name)/'receipts', self.store, MemoryGitHub(), collector)
        self.assertEqual(self.admit(inputs=self.inputs)['skip'], 'run_already_started')

    def test_live_control_credential_is_required_and_separate_from_writer(self):
        from pipeline.scheduling import collection_enabled
        with patch.dict('os.environ', {}, clear=True), self.assertRaisesRegex(ValueError, 'COLLECTION_CONTROL_TOKEN'):
            collection_enabled('openwatersio/noaa-water-level-research')
        class Response:
            returncode = 0
            stdout = b'{"value":"false"}'
        with patch.dict('os.environ', {'COLLECTION_CONTROL_TOKEN':'control-secret', 'GH_TOKEN':'writer-secret'}):
            with patch('subprocess.run', return_value=Response()) as command:
                self.assertFalse(collection_enabled('openwatersio/noaa-water-level-research'))
                self.assertEqual(command.call_args.kwargs['env']['GH_TOKEN'], 'control-secret')
            Response.returncode = 1
            with patch('subprocess.run', return_value=Response()), self.assertRaisesRegex(RuntimeError, 'live collection pause'):
                collection_enabled('openwatersio/noaa-water-level-research')

    def test_delayed_cron_keeps_its_clock_while_actual_poll_selects_current_slot(self):
        self.context['event'] = 'workflow_dispatch'
        result = self.admit(inputs={**self.inputs, 'cron_scheduled_at':'2026-11-01T12:45:00Z'})
        self.assertEqual(result['scheduling']['slot'], '2026-11-01T12:50:00Z')
        self.assertEqual(result['scheduling']['cronScheduledAt'], '2026-11-01T12:45:00Z')

    def test_window_expiry_between_admission_and_start_makes_no_claim_or_request(self):
        from pipeline.hosted import run_hosted_tick
        admitted = self.admit()
        with patch('pipeline.hosted.now', return_value='2026-11-01T13:50:00Z'):
            result = run_hosted_tick(admitted, Path(self.temp.name)/'receipts', self.store, MemoryGitHub(),
                                     lambda *a, **kw: self.fail('expired attempt must not fetch NOAA'))
        self.assertEqual(result['skip'], 'expired_slot')
        self.assertFalse(list(self.store.path.rglob('*.json')))
