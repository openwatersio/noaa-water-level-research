import tempfile
from pathlib import Path
import unittest

from pipeline.evaluate_request import arguments
from pipeline.archive import sha


class Backend:
    def api(self, path): return {'object':{'sha':'a'*40}}
    def download(self, asset): return b'{"frozen":true}'
    def all_releases(self): return [{'tag_name':'report-test','assets':[{'id':7}]}]


class RequestTests(unittest.TestCase):
    def test_resolves_snapshot_before_start_and_preserves_literal_input(self):
        args = arguments({'first':'$(false)'}, Backend(), Path('/tmp/unused'))
        self.assertIn('a'*40, args)
        self.assertEqual(args[-1], '$(false)')

    def test_source_assets_require_matching_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            request = {'widths_asset':7, 'widths_sha256':'0'*64}
            with self.assertRaisesRegex(ValueError,'differs'):
                arguments(request, Backend(), Path(temp))
            request['widths_sha256'] = sha(b'{"frozen":true}')
            args = arguments(request, Backend(), Path(temp))
            self.assertEqual(Path(args[args.index('--widths')+1]).read_bytes(), b'{"frozen":true}')

    def test_unknown_inputs_fail_closed(self):
        with self.assertRaises(ValueError): arguments({'shell':'evil'}, Backend(), Path('/tmp/unused'))
