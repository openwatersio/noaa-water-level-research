import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_archive import MemoryGitHub
from pipeline.archive import immutable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/etss'))
import capture


class Store:
    def __init__(self, path, fail=False): self.path, self.fail = path, fail
    def write(self, name, body): immutable(self.path / name, body)
    def commit(self, message):
        if self.fail: raise OSError('push unavailable')
        return 'b' * 40


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline.hosted'), 'hosted orchestration missing')
        from pipeline.hosted import run_hosted_tick
        self.run = run_hosted_tick
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.p = Path(self.temp.name); self.root = self.p / 'receipts'; self.root.mkdir()
        self.store = Store(self.p / 'data'); self.store.path.mkdir()
        self.gh = MemoryGitHub()
        self.context = {'batchId':'run-1', 'origin':'github', 'codeCommit':'a'*40, 'runId':'1', 'runAttempt':'1'}

    def test_start_must_be_durable_before_any_acquisition(self):
        self.store.fail = True
        called = []
        def collector(*args, **kwargs): called.append(1)
        with self.assertRaises(OSError): self.run(self.context, self.root, self.store, self.gh, collector)
        self.assertEqual(called, [])

    def test_interruption_preserves_checkpoint_and_failed_terminal_status(self):
        def collector(root, **kwargs):
            immutable(root / 'ticks/attempt-start.json', b'{"startedAt":"2026-10-21T12:00:00Z"}')
            raise RuntimeError('interrupted test')
        result = self.run(self.context, self.root, self.store, self.gh, collector)
        self.assertEqual(result['state'], 'interrupted')
        self.assertTrue((self.store.path / 'evidence/ticks/attempt-start.json').exists())
        self.assertEqual(result['exitCode'], 1)

    def test_real_collector_checkpoints_products_and_keeps_native_disagreement_diagnostic(self):
        from test_collect import forecast, observation
        from test_capture import Response
        from urllib.parse import parse_qs, urlparse
        import collect
        seen = []
        def transport(url, timeout):
            if seen:
                self.assertTrue(any(a['name'].startswith('bundle-') for r in self.gh.releases.values() for a in r['assets']))
            seen.append(url)
            station = parse_qs(urlparse(url).query).get('station')
            return Response(observation(station[0]) if station else forecast(url, disagreement=True))
        with patch.object(capture, 'open_url', side_effect=transport), patch.object(capture, 'utc_now', return_value='2026-10-13T00:11:12Z'):
            result = self.run(self.context, self.root, self.store, self.gh, collect.collect_tick)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(len(seen), 9)
        self.assertTrue(all(x['contractStatus'] == 'disagreement' for x in result['tick']['forecasts']))

    def test_upload_failure_is_not_a_successful_collection(self):
        self.gh.corrupt = True
        def collector(root, **kwargs): return {'state':'complete', 'exitCode':0}
        result = self.run(self.context, self.root, self.store, self.gh, collector)
        self.assertEqual(result['state'], 'publication_failed')
        self.assertEqual(result['exitCode'], 1)
        self.assertFalse(list((self.store.path / 'catalog').glob('*.json')))
