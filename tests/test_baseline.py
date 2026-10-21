import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BaselineTests(unittest.TestCase):
    def test_standalone_inputs_and_source_pin(self):
        self.assertTrue((ROOT / "tools/etss/evaluation-pin.json").exists(), "standalone science baseline is missing")
        pin = json.loads((ROOT / "tools/etss/evaluation-pin.json").read_text())
        for name, expected in pin["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected, name)
        self.assertEqual(hashlib.sha256((ROOT / "inputs/tides.json").read_bytes()).hexdigest(), pin["tideBundleSha256"])
