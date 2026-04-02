from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wrish._sentinel import maybe_cycle_sentinel_adapter, sentinel_state


class TestSentinelState(unittest.TestCase):

    def test_reports_connected_only_when_everything_is_ready(self):
        status = {
            "adapter_present": True,
            "adapter_powered": True,
            "present": True,
            "connected": True,
        }
        self.assertEqual(sentinel_state(status), "connected")

    def test_reports_adapter_off_before_device_state(self):
        status = {
            "adapter_present": True,
            "adapter_powered": False,
            "present": True,
            "connected": True,
        }
        self.assertEqual(sentinel_state(status), "adapter-off")


class TestMaybeCycleSentinelAdapter(unittest.TestCase):

    def test_skips_cycle_before_threshold(self):
        device = MagicMock()
        log = MagicMock()

        cycled = maybe_cycle_sentinel_adapter(device, 3, log_fn=log, threshold=4)

        self.assertFalse(cycled)
        device.cycle_bluetooth.assert_not_called()
        log.assert_not_called()

    def test_cycles_adapter_at_threshold(self):
        device = MagicMock()
        log = MagicMock()

        cycled = maybe_cycle_sentinel_adapter(device, 4, log_fn=log, threshold=4)

        self.assertTrue(cycled)
        device.cycle_bluetooth.assert_called_once_with()
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
