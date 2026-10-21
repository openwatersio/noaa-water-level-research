import math
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline.replay import compare, digest, verify


class ReplayRoundoffTests(unittest.TestCase):
    def test_full_verify_accepts_roundoff_but_rejects_changed_input_receipts(self):
        methods = {'surgeChange': {'heightMeters': -0.18345243571250885, 'fallback': False, 'reason': None}}
        cases = {'station': {'6': [{'decisionAt': 'decision', 'validAt': 'target', 'methods': copy.deepcopy(methods)}]}}
        inputs = [{'decisionAt': 'decision', 'stations': {'station': {'receipt': 'original', 'targets': [
            {'horizonHours': 6, 'validAt': 'target', 'methods': copy.deepcopy(methods)}]}}}]
        reference = {'truthAsOf': 'truth', 'cases': cases, 'labelsSha256': digest({}),
                     'selectedInputSha256': digest(inputs), 'referenceReportSha256': 'reference', 'manifestInventory': {}}
        result = {'truthAsOf': 'truth', 'cases': copy.deepcopy(cases), 'inputs': copy.deepcopy(inputs), 'labels': {}}
        result['cases']['station']['6'][0]['methods']['surgeChange']['heightMeters'] = -0.18345243571250888
        result['inputs'][0]['stations']['station']['targets'][0]['methods']['surgeChange']['heightMeters'] = -0.18345243571250888
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'fixtures').mkdir(); (root/'raw').mkdir()
            (root/'fixtures/development-reference.json').write_text(json.dumps(reference))
            with patch('pipeline.replay.ROOT', root), patch('pipeline.replay.report.build', return_value=result):
                checked = verify(root/'raw')
                self.assertEqual(checked['state'], 'equal_with_numeric_roundoff')
                self.assertEqual(checked['selectedInputSha256'], reference['selectedInputSha256'])
                self.assertNotEqual(checked['unmodifiedInputSha256'], reference['selectedInputSha256'])
                self.assertEqual(len(checked['inputPredictionDifferences']), 1)
                self.assertEqual(len(checked['numericDifferences']), 1)
                self.assertTrue(checked['inputPredictionDifferences'][0]['path'].startswith('inputs/0/'))
                self.assertEqual(result['inputs'][0]['stations']['station']['targets'][0]['methods']['surgeChange']['heightMeters'], -0.18345243571250888)
                result['inputs'][0]['stations']['station']['receipt'] = 'changed'
                with self.assertRaisesRegex(ValueError, 'selectedInputSha256'):
                    verify(root/'raw')
                result['inputs'][0]['stations']['station']['receipt'] = 'original'
                result['inputs'][0]['stations']['station']['targets'][0]['methods']['surgeChange']['heightMeters'] = 0.5
                with self.assertRaises(ValueError):
                    verify(root/'raw')
                result['inputs'] = copy.deepcopy(inputs)
                result['cases'] = copy.deepcopy(cases)
                self.assertEqual(verify(root/'raw')['state'], 'equal')

    def test_observed_linux_prediction_roundoff_is_reported(self):
        path = 'cases/9449880/6/0/methods/surgeChange/heightMeters'
        differences = compare(-0.18345243571250885, -0.18345243571250888, path)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]['path'], path)
        self.assertLess(differences[0]['absoluteDifference'], 3e-17)

    def test_scores_allow_only_bounded_finite_float_roundoff(self):
        self.assertEqual(compare({'mae': 0.1}, {'mae': 0.1}, 'scores'), [])
        self.assertEqual(len(compare([0.1], [math.nextafter(0.1, 1)], 'scores/maeDifferenceRange')), 1)
        for actual in (0.100000000001, float('nan'), float('inf'), 1, True, None):
            with self.assertRaises(ValueError):
                compare(0.1, actual, 'scores/mae')
        with self.assertRaises(ValueError):
            compare(float('inf'), float('inf'), 'scores/mae')

    def test_receipts_truth_labels_flags_and_structure_remain_exact(self):
        for path in ('inputs/value', 'labels/value', 'cases/0/truthMeters', 'scores/radius', 'scores/coverage'):
            with self.assertRaises(ValueError):
                compare(0.1, math.nextafter(0.1, 1), path)
        for before, after in (({'count': 1}, {'count': 2}), ({'fallback': False}, {'fallback': True}),
                              ({'mae': 0.1}, {}), ([0.1], [0.1, 0.1]), ({'count': 1}, {'count': 1.0})):
            with self.assertRaises(ValueError):
                compare(before, after, 'scores')


if __name__ == '__main__':
    unittest.main()
