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
