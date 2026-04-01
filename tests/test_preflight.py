"""Tests for _preflight_scan and _ensure_adapter_powered on C60A82CDevice."""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Stub out dbus / gi so the module loads without the real system libraries
# ---------------------------------------------------------------------------
_dbus_stub = types.ModuleType("dbus")
_dbus_stub.SystemBus = MagicMock
_dbus_stub.Interface = MagicMock
_dbus_stub.Array = list
_dbus_stub.Byte = int
_mainloop_stub = types.ModuleType("dbus.mainloop")
_glib_stub = types.ModuleType("dbus.mainloop.glib")
_glib_stub.DBusGMainLoop = MagicMock
_gi_stub = types.ModuleType("gi")
_gi_repo_stub = types.ModuleType("gi.repository")
_gi_repo_stub.GLib = MagicMock
sys.modules.setdefault("dbus", _dbus_stub)
sys.modules.setdefault("dbus.mainloop", _mainloop_stub)
sys.modules.setdefault("dbus.mainloop.glib", _glib_stub)
sys.modules.setdefault("gi", _gi_stub)
sys.modules.setdefault("gi.repository", _gi_repo_stub)

from wrish.devices.c60_a82c import (  # noqa: E402
    ADAPTER_IFACE,
    DEVICE_IFACE,
    DeviceError,
    C60A82CDevice,
    _recover_after_not_connected,
    _run_with_notify_retries,
)

MODULE = "wrish.devices.c60_a82c"


def _make_device(mac="A4:C1:38:9A:A8:2C", hci="hci0") -> C60A82CDevice:
    return C60A82CDevice(mac=mac, hci=hci, debug=False)


# ---------------------------------------------------------------------------
# _ensure_adapter_powered
# ---------------------------------------------------------------------------

class TestEnsureAdapterPowered(unittest.TestCase):

    def _run(self, device, bus, dbus_module):
        device._ensure_adapter_powered(bus, dbus_module)

    def test_already_powered_returns_immediately(self):
        dev = _make_device()
        props = MagicMock()
        props.Get.return_value = True
        dbus_module = MagicMock()
        dbus_module.Interface.return_value = props
        bus = MagicMock()

        self._run(dev, bus, dbus_module)

        props.Set.assert_not_called()

    def test_not_powered_sets_true_and_waits(self):
        dev = _make_device()
        props = MagicMock()
        props.Get.side_effect = [False, True]
        dbus_module = MagicMock()
        dbus_module.Interface.return_value = props
        bus = MagicMock()

        with patch(f"{MODULE}.time") as mock_time:
            mock_time.sleep = MagicMock()
            mock_time.monotonic = MagicMock(side_effect=[0, 1, 2, 3])
            self._run(dev, bus, dbus_module)

        props.Set.assert_called_once_with(ADAPTER_IFACE, "Powered", True)

    def test_adapter_unavailable_raises(self):
        dev = _make_device()
        props = MagicMock()
        props.Get.side_effect = Exception("no adapter")
        dbus_module = MagicMock()
        dbus_module.Interface.return_value = props
        bus = MagicMock()

        with self.assertRaises(DeviceError) as ctx:
            self._run(dev, bus, dbus_module)
        self.assertIn("Could not power on", str(ctx.exception))

    def test_power_on_never_succeeds_raises(self):
        dev = _make_device()
        props = MagicMock()
        props.Get.side_effect = [False] + [False] * 25
        dbus_module = MagicMock()
        dbus_module.Interface.return_value = props
        bus = MagicMock()

        with patch(f"{MODULE}.time") as mock_time:
            mock_time.sleep = MagicMock()
            with self.assertRaises(DeviceError) as ctx:
                self._run(dev, bus, dbus_module)
        self.assertIn("Could not power on", str(ctx.exception))


# ---------------------------------------------------------------------------
# _preflight_scan
# ---------------------------------------------------------------------------

def _make_preflight_mocks(objects: dict):
    """Return (bus, dbus_module, adapter_iface, manager) configured for _preflight_scan."""
    bus = MagicMock()
    dbus_module = MagicMock()
    adapter_iface = MagicMock()
    manager = MagicMock()
    manager.GetManagedObjects.return_value = objects

    def _iface(obj, iface):
        if iface == ADAPTER_IFACE:
            return adapter_iface
        return manager

    dbus_module.Interface.side_effect = _iface
    return bus, dbus_module, adapter_iface, manager


class TestPreflightScan(unittest.TestCase):

    def _run(self, device, bus, dbus_module, manager, scan_timeout=0.05):
        with (
            patch.object(C60A82CDevice, "_get_manager", return_value=manager),
            patch(f"{MODULE}.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            # make monotonic advance past scan_timeout immediately on first call
            mock_time.monotonic.side_effect = [0.0, 0.0, scan_timeout + 1]
            return device._preflight_scan(bus, dbus_module, scan_timeout=scan_timeout)

    def test_finds_device_returns_true(self):
        dev = _make_device()
        objects = {
            "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
                DEVICE_IFACE: {"Address": "AA:BB:CC:DD:EE:FF"}
            }
        }
        bus, dbus_module, _, manager = _make_preflight_mocks(objects)
        self.assertTrue(self._run(dev, bus, dbus_module, manager))

    def test_no_devices_returns_false(self):
        dev = _make_device()
        bus, dbus_module, _, manager = _make_preflight_mocks({})

        with (
            patch.object(C60A82CDevice, "_get_manager", return_value=manager),
            patch(f"{MODULE}.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            # monotonic advances past timeout quickly
            mock_time.monotonic.side_effect = [0.0, 0.0, 1.0, 2.0, 10.0]
            self.assertFalse(dev._preflight_scan(bus, dbus_module, scan_timeout=0.05))

    def test_non_device_objects_ignored(self):
        dev = _make_device()
        objects = {
            "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
        }
        bus, dbus_module, _, manager = _make_preflight_mocks(objects)

        with (
            patch.object(C60A82CDevice, "_get_manager", return_value=manager),
            patch(f"{MODULE}.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 0.0, 1.0, 2.0, 10.0]
            self.assertFalse(dev._preflight_scan(bus, dbus_module, scan_timeout=0.05))

    def test_stop_discovery_called_even_on_failure(self):
        dev = _make_device()
        bus, dbus_module, adapter_iface, manager = _make_preflight_mocks({})

        with (
            patch.object(C60A82CDevice, "_get_manager", return_value=manager),
            patch(f"{MODULE}.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            mock_time.monotonic.side_effect = [0.0, 0.0, 1.0, 2.0, 10.0]
            dev._preflight_scan(bus, dbus_module, scan_timeout=0.05)

        adapter_iface.StopDiscovery.assert_called()

    def test_non_target_device_is_enough(self):
        """A device with a different MAC is sufficient to pass preflight."""
        dev = _make_device(mac="A4:C1:38:9A:A8:2C")
        objects = {
            "/org/bluez/hci0/dev_DE_AD_BE_EF_00_01": {
                DEVICE_IFACE: {"Address": "DE:AD:BE:EF:00:01"}
            }
        }
        bus, dbus_module, _, manager = _make_preflight_mocks(objects)
        self.assertTrue(self._run(dev, bus, dbus_module, manager))


# ---------------------------------------------------------------------------
# _ensure_connected: preflight triggered only when device not already known
# ---------------------------------------------------------------------------

class TestEnsureConnectedPreflight(unittest.TestCase):

    def test_preflight_runs_when_device_not_resolved(self):
        """When device path is unknown, preflight must run before discovery."""
        dev = _make_device()
        call_order = []
        bus = MagicMock()
        dbus_module = MagicMock()

        # Device not in cache
        dev_props = MagicMock()
        dev_props.Get.return_value = True  # "Connected" → skip Connect() call
        dbus_module.Interface.return_value = dev_props

        resolved = [None, "/org/bluez/hci0/dev_AA_BB_CC"]

        def _fake_resolve(self, bus):
            return resolved.pop(0)

        def _fake_preflight(self, bus, dbus_module, scan_timeout=5.0):
            call_order.append("preflight")
            return True

        def _fake_discover(self, bus, dbus_module, timeout=12.0):
            call_order.append("discover")
            return "/org/bluez/hci0/dev_AA_BB_CC"

        with (
            patch.object(C60A82CDevice, "_resolve_device_path", _fake_resolve),
            patch.object(C60A82CDevice, "_preflight_scan", _fake_preflight),
            patch.object(C60A82CDevice, "_discover_device_path", _fake_discover),
        ):
            dev._ensure_connected(bus, dbus_module)

        self.assertEqual(call_order, ["preflight", "discover"])

    def test_preflight_skipped_when_device_already_known(self):
        """When the device is already in BlueZ cache, preflight must be skipped."""
        dev = _make_device()
        bus = MagicMock()
        dbus_module = MagicMock()

        dev_props = MagicMock()
        dev_props.Get.return_value = True  # "Connected"
        dbus_module.Interface.return_value = dev_props

        preflight_called = []

        def _fake_resolve(self, bus):
            return "/org/bluez/hci0/dev_AA_BB_CC"

        def _fake_preflight(self, bus, dbus_module, scan_timeout=5.0):
            preflight_called.append(True)

        with (
            patch.object(C60A82CDevice, "_resolve_device_path", _fake_resolve),
            patch.object(C60A82CDevice, "_preflight_scan", _fake_preflight),
        ):
            dev._ensure_connected(bus, dbus_module)

        self.assertEqual(preflight_called, [], "preflight must not run when device is already cached")

    def test_preflight_failure_aborts_connection(self):
        """If preflight raises, the discovery must not proceed."""
        dev = _make_device()
        bus = MagicMock()
        dbus_module = MagicMock()

        discover_called = []

        def _fake_resolve(self, bus):
            return None

        def _bad_preflight(self, bus, dbus_module, scan_timeout=5.0):
            raise DeviceError("no devices in range")

        def _fake_discover(self, bus, dbus_module, timeout=12.0):
            discover_called.append(True)
            return None

        with (
            patch.object(C60A82CDevice, "_resolve_device_path", _fake_resolve),
            patch.object(C60A82CDevice, "_preflight_scan", _bad_preflight),
            patch.object(C60A82CDevice, "_discover_device_path", _fake_discover),
        ):
            with self.assertRaises(DeviceError, msg="no devices in range"):
                dev._ensure_connected(bus, dbus_module)

        self.assertEqual(discover_called, [], "_discover_device_path must not be called after preflight failure")


class TestNotifyRecovery(unittest.TestCase):

    def test_run_with_notify_retries_recovers_after_not_connected(self):
        dev = _make_device()
        attempts = []
        recover_calls = []

        def operation():
            attempts.append("run")
            if len(attempts) == 1:
                cause = RuntimeError("org.bluez.Error.Failed: Not connected")
                raise DeviceError(f"Could not start BLE notifications: {cause}") from cause
            return "ok"

        def fake_recover(self, exc):
            recover_calls.append(str(exc))

        with patch(f"{MODULE}._recover_after_not_connected", fake_recover):
            result = _run_with_notify_retries(dev, operation, context="test")

        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(recover_calls, ["org.bluez.Error.Failed: Not connected"])

    def test_run_with_notify_retries_does_not_retry_other_errors(self):
        dev = _make_device()
        attempts = []

        def operation():
            attempts.append("run")
            cause = RuntimeError("org.bluez.Error.NotPermitted")
            raise DeviceError(f"Could not start BLE notifications: {cause}") from cause

        with self.assertRaises(DeviceError):
            _run_with_notify_retries(dev, operation, context="test")

        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
