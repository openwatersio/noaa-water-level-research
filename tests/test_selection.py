import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('pipeline.selection'), 'source-boundary selection missing')
        from pipeline.selection import eligible_source
        self.select = eligible_source

    def test_overlap_never_uses_hosted_shadow_receipts(self):
        for at in ('2026-10-21T12:00:00Z','2026-10-25T12:00:00Z'):
            self.assertTrue(self.select('legacy', at, None))
            self.assertFalse(self.select('github', at, None))

    def test_exact_boundary_uses_hosted_and_old_local_receipts_remain_eligible(self):
        boundary = '2026-10-25T12:00:00Z'
        self.assertTrue(self.select('legacy', '2026-10-25T11:59:59Z', boundary))
        self.assertFalse(self.select('legacy', boundary, boundary))
        self.assertTrue(self.select('github', boundary, boundary))
        self.assertFalse(self.select('github', '2026-10-25T11:59:59Z', boundary))

    def test_unknown_source_never_enters_primary(self):
        with self.assertRaises(ValueError): self.select('unknown', '2026-10-25T12:00:00Z', None)

    def test_primary_view_retains_originals_and_excludes_boundary_spanning_pairs(self):
        from pipeline.archive import encoded, immutable, inventory
        from pipeline.selection import primary_view
        boundary = '2026-11-04T03:00:00Z'
        before = '2026-11-04T02:59:59Z'
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); raw = base/'raw'; data = base/'data'
            for origin, times in [('legacy', (before, before)), ('github', (boundary, boundary))]:
                files = {}
                for label, clocks in [('eligible', times), ('spanning', (before, boundary))]:
                    name = f'forecasts/{origin}-{label}.json'
                    immutable(raw/name, encoded({'products': {
                        product: {'receivedAt': at, 'state': 'complete'}
                        for product, at in zip(('shef', 'csv'), clocks)}}))
                    files[name] = {}
                immutable(data/f'catalog/{origin}.json', encoded({'context': {'origin': origin}, 'files': files}))
            original = inventory(raw)
            result = primary_view(raw, data, base/'primary', {'boundaryAt': boundary})
            self.assertEqual(sorted(result['excludedManifests']),
                             ['forecasts/github-spanning.json', 'forecasts/legacy-spanning.json'])
            self.assertEqual(sorted(inventory(base/'primary')),
                             ['forecasts/github-eligible.json', 'forecasts/legacy-eligible.json'])
            self.assertEqual(inventory(raw), original)
