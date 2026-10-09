from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from agents.linux.src.hardware_controls import LinuxHardwareControls

class HardwareControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name); self.device = self.root / 'intel'; self.device.mkdir(); (self.device / 'max_brightness').write_text('200'); (self.device / 'brightness').write_text('100'); self.control = LinuxHardwareControls(self.root)
    def tearDown(self): self.tmp.cleanup()
    def test_discovers_only_writable_brightness(self): self.assertTrue(self.control.capabilities()['BRIGHTNESS']); self.assertFalse(self.control.capabilities()['VOLUME'])
    def test_zero_and_one_hundred_map_to_native_boundaries(self): self.assertEqual(self.control.execute('BRIGHTNESS', 0)['status'], 'SUCCEEDED'); self.assertEqual((self.device / 'brightness').read_text(), '0'); self.assertEqual(self.control.execute('BRIGHTNESS', 100)['status'], 'SUCCEEDED'); self.assertEqual((self.device / 'brightness').read_text(), '200')
    def test_invalid_and_unrelated_controls_fail_closed(self): self.assertEqual(self.control.execute('BRIGHTNESS', 101)['status'], 'FAILED'); self.assertEqual(self.control.execute('WIFI', True)['status'], 'UNSUPPORTED')
    def test_missing_backlight_is_unsupported(self): self.assertEqual(LinuxHardwareControls(self.root / 'missing').execute('BRIGHTNESS', 50)['status'], 'UNSUPPORTED')
