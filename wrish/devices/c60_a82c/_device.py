from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import sys
import threading
import time
from typing import Callable

from ._bluez import (
    DeviceError,
    _is_in_progress_error,
    _is_not_connected_error,
    _load_bluez_modules,
    _shell_cycle_bluetooth,
    _start_discovery,
    _hci_adapters_in_os,
    _shell_enable_bluetooth,
)
from ._constants import (
    APP_TYPE_CALL,
    APP_TYPE_SMS,
    APP_TYPES,
    ADAPTER_IFACE,
    BLUEZ_SVC,
    CAMERA_BUTTON_DEDUP_SECONDS,
    CAMERA_BUTTON_EVENT,
    CAMERA_MODE_ENTER_CMD,
    CAMERA_MODE_EXIT_CMD,
    CMD_GET_CURRENT_POWER,
    CMD_GET_CURRENT_STEP,
    CMD_GET_DEVICE_STATE,
    CMD_GET_HART_SNAPSHOT,
    CMD_SET_NOTICE_ALL,
    DEVICE_IFACE,
    DEVICE_NAME_UUID_PREFIX,
    END_MESSAGE,
    FF01_UUID_PREFIX,
    FF02_UUID_PREFIX,
    FIND_DEVICE_CMD,
    FIND_PHONE_EVENT,
    GATT_IFACE,
    OM_IFACE,
    PROPS_IFACE,
)
from ._dialer import decode_dialer_symbols, format_calibration_report
from ._health import (
    decode_hart_history,
    decode_hart_snapshot,
    decode_steps_snapshot,
    frame_health_hist_query,
)
from ._protocol import (
    frame_message_part,
    frame_message_type,
    frame_set_device_state,
    frame_set_time,
)


def _recover_after_not_connected(device: "C60A82CDevice", exc: Exception) -> None:
    device._log(f"BLE session lost ({exc}); running preflight recovery")
    bus, dbus_module = device._bus()
    device._ensure_adapter_powered(bus, dbus_module)
    found_any = device._preflight_scan(bus, dbus_module, scan_timeout=3.0)
    if not found_any:
        device._log("recovery preflight saw no devices, triggering deep recovery")
        _shell_enable_bluetooth(device.hci, device._log)
        device._ensure_adapter_powered(bus, dbus_module)
        device._preflight_scan(bus, dbus_module, scan_timeout=8.0)
    device._ensure_connected(bus, dbus_module)


def _run_with_notify_retries(
    device: "C60A82CDevice",
    operation: Callable[[], object],
    *,
    context: str,
):
    last_exc: DeviceError | None = None
    for attempt in range(3):
        try:
            return operation()
        except DeviceError as exc:
            last_exc = exc
            cause = exc.__cause__
            if attempt >= 2 or cause is None or not _is_not_connected_error(cause):
                raise
            device._log(
                f"{context}: notify session attempt {attempt + 1} failed with disconnected BLE link; retrying"
            )
            _recover_after_not_connected(device, cause)
            time.sleep(1.0)
    assert last_exc is not None
    raise last_exc


@dataclass(slots=True)
class C60A82CDevice:
    mac: str
    hci: str = "hci0"
    debug: bool = False

    def __post_init__(self) -> None:
        self.mac = self.mac.upper()

    @property
    def device_path(self) -> str:
        return f"/org/bluez/{self.hci}/dev_{self.mac.replace(':', '_')}"

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[wrish:{self.mac}] {message}", file=sys.stderr)

    def _bus(self):
        dbus, _glib = _load_bluez_modules()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        return dbus.SystemBus(), dbus

    def is_connected(self) -> bool:
        try:
            bus, dbus_module = self._bus()
            device_path = self._resolve_device_path(bus)
            if not device_path:
                return False
            dev = bus.get_object(BLUEZ_SVC, device_path)
            props = dbus_module.Interface(dev, PROPS_IFACE)
            return bool(props.Get(DEVICE_IFACE, "Connected"))
        except Exception:
            return False

    def status(self) -> dict[str, object]:
        info: dict[str, object] = {
            "mac": self.mac,
            "hci": self.hci,
            "device_path": self.device_path,
            "present": False,
            "connected": False,
            "services_resolved": False,
        }
        try:
            bus, dbus_module = self._bus()
            adapter = bus.get_object(BLUEZ_SVC, self.adapter_path)
            adapter_props = dbus_module.Interface(adapter, PROPS_IFACE)
            info["adapter_present"] = True
            try:
                info["adapter_powered"] = bool(adapter_props.Get(ADAPTER_IFACE, "Powered"))
            except Exception:
                pass

            device_path = self._resolve_device_path(bus)
            if not device_path:
                return info
            info["device_path"] = device_path
            dev = bus.get_object(BLUEZ_SVC, device_path)
            props = dbus_module.Interface(dev, PROPS_IFACE)
            info["present"] = True
            info["connected"] = bool(props.Get(DEVICE_IFACE, "Connected"))
            try:
                info["name"] = str(props.Get(DEVICE_IFACE, "Name"))
            except Exception:
                pass
            try:
                info["alias"] = str(props.Get(DEVICE_IFACE, "Alias"))
            except Exception:
                pass
            try:
                info["paired"] = bool(props.Get(DEVICE_IFACE, "Paired"))
            except Exception:
                pass
            try:
                info["trusted"] = bool(props.Get(DEVICE_IFACE, "Trusted"))
            except Exception:
                pass
            try:
                info["rssi"] = int(props.Get(DEVICE_IFACE, "RSSI"))
            except Exception:
                pass

            ff01_path = self._find_char(bus, FF01_UUID_PREFIX, device_path=device_path)
            ff02_path = self._find_char(bus, FF02_UUID_PREFIX, device_path=device_path)
            info["services_resolved"] = bool(ff01_path and ff02_path)
            if ff01_path:
                info["ff01_path"] = ff01_path
            if ff02_path:
                info["ff02_path"] = ff02_path
        except Exception:
            return info
        return info

    def connect(self) -> None:
        bus, dbus_module = self._bus()
        self._ensure_adapter_powered(bus, dbus_module)
        self._ensure_connected(bus, dbus_module)

    def diagnose_adapter(self) -> dict[str, object]:
        """
        Proactive health check: verifies the BT adapter is on and BT is scannable.
        Returns a dict with keys: powered (bool), recoverable (bool), error (str|None).
        Raises DeviceError only if recovery is impossible.
        """
        info: dict[str, object] = {"powered": False, "recoverable": True, "error": None}
        try:
            bus, dbus_module = self._bus()
            self._ensure_adapter_powered(bus, dbus_module)
            info["powered"] = True
        except DeviceError as exc:
            info["error"] = str(exc)
            info["recoverable"] = False
            self._log(f"diagnose: adapter fault — {exc}")
        return info

    def cycle_bluetooth(self) -> None:
        _shell_cycle_bluetooth(self.hci, self._log)
        bus, dbus_module = self._bus()
        self._ensure_adapter_powered(bus, dbus_module)
        self._preflight_scan(bus, dbus_module, scan_timeout=8.0)

    def _preflight_scan(self, bus, dbus_module, scan_timeout: float = 5.0) -> bool:
        """
        Short discovery pass to check BT can see at least one device.
        Returns True if any device is visible, False if none found.
        Never raises — failures are logged and treated as False.
        """
        try:
            adapter_obj = bus.get_object(BLUEZ_SVC, self.adapter_path)
            adapter = dbus_module.Interface(adapter_obj, ADAPTER_IFACE)
            manager = self._get_manager(bus, dbus_module)
        except Exception as exc:
            self._log(f"preflight: could not reach adapter — {exc}")
            return False

        self._log(f"preflight scan on {self.hci} ({scan_timeout:.0f}s)")
        started = _start_discovery(adapter, dbus_module, self._log)
        try:
            deadline = time.monotonic() + scan_timeout
            while time.monotonic() < deadline:
                time.sleep(1.0)
                visible = [
                    path
                    for path, ifaces in manager.GetManagedObjects().items()
                    if DEVICE_IFACE in ifaces
                ]
                if visible:
                    self._log(f"preflight: {len(visible)} device(s) visible")
                    return True
        except Exception as exc:
            self._log(f"preflight: scan error — {exc}")
        finally:
            if started:
                try:
                    adapter.StopDiscovery()
                except Exception:
                    pass

        self._log("preflight: no devices visible")
        return False

    @property
    def adapter_path(self) -> str:
        return f"/org/bluez/{self.hci}"

    def _get_manager(self, bus, dbus_module):
        return dbus_module.Interface(bus.get_object(BLUEZ_SVC, "/"), OM_IFACE)

    def _resolve_device_path(self, bus) -> str | None:
        _dbus, _glib = _load_bluez_modules()
        manager = self._get_manager(bus, _dbus)
        for path, interfaces in manager.GetManagedObjects().items():
            if DEVICE_IFACE not in interfaces:
                continue
            if str(interfaces[DEVICE_IFACE].get("Address", "")).upper() != self.mac:
                continue
            return str(path)
        return None

    def _ensure_adapter_powered(self, bus, dbus_module) -> None:
        def _get_powered() -> bool | None:
            """True = on, False = off, None = adapter not reachable via D-Bus."""
            try:
                obj = bus.get_object(BLUEZ_SVC, self.adapter_path)
                p = dbus_module.Interface(obj, PROPS_IFACE)
                return bool(p.Get(ADAPTER_IFACE, "Powered"))
            except Exception:
                return None

        def _dbus_set_powered() -> None:
            try:
                obj = bus.get_object(BLUEZ_SVC, self.adapter_path)
                p = dbus_module.Interface(obj, PROPS_IFACE)
                p.Set(ADAPTER_IFACE, "Powered", True)
            except Exception:
                pass

        state = _get_powered()

        if state is True:
            return

        if state is False:
            # Adapter reachable but off — try D-Bus Set first (~5 s)
            self._log(f"powering on adapter {self.hci} via D-Bus")
            _dbus_set_powered()
            for _ in range(10):
                time.sleep(0.5)
                if _get_powered() is True:
                    return

        # Adapter invisible or D-Bus Set failed — shell recovery
        self._log(f"adapter {self.hci} did not power on via D-Bus, trying shell recovery")
        _shell_enable_bluetooth(self.hci, self._log)

        for _ in range(20):
            time.sleep(0.5)
            if _get_powered() is True:
                self._log(f"adapter {self.hci} is now on")
                return

        adapters = _hci_adapters_in_os()
        if adapters and self.hci in adapters:
            hint = (
                f"adapter {self.hci} is visible to the OS but not powered on — "
                "try: sudo systemctl restart bluetooth.service"
            )
        elif adapters:
            hint = f"OS-visible adapters: {', '.join(adapters)} (expected {self.hci})"
        else:
            hint = "no Bluetooth adapters detected by the OS"
        raise DeviceError(
            f"Could not power on Bluetooth adapter {self.hci} "
            f"(tried D-Bus, rfkill, bluetoothctl) — {hint}"
        )

    def _discover_device_path(self, bus, dbus_module, timeout: float = 12.0) -> str | None:
        adapter_obj = bus.get_object(BLUEZ_SVC, self.adapter_path)
        adapter = dbus_module.Interface(adapter_obj, ADAPTER_IFACE)
        start = time.monotonic()
        self._log(f"starting discovery on {self.hci}")
        started = _start_discovery(adapter, dbus_module, self._log)
        try:
            while time.monotonic() - start < timeout:
                path = self._resolve_device_path(bus)
                if path:
                    return path
                time.sleep(1.0)
            return self._resolve_device_path(bus)
        finally:
            if started:
                try:
                    adapter.StopDiscovery()
                except Exception:
                    pass

    def _find_char(self, bus, uuid_prefix: str, *, device_path: str | None = None) -> str | None:
        _dbus, _glib = _load_bluez_modules()
        manager = self._get_manager(bus, _dbus)
        target_device_path = device_path or self._resolve_device_path(bus) or self.device_path
        for path, interfaces in manager.GetManagedObjects().items():
            if GATT_IFACE not in interfaces:
                continue
            if target_device_path not in str(path):
                continue
            uuid = str(interfaces[GATT_IFACE].get("UUID", ""))
            if uuid_prefix in uuid:
                return str(path)
        return None

    def _ensure_connected(self, bus, dbus_module) -> None:
        device_path = self._resolve_device_path(bus)
        if not device_path:
            found_any = self._preflight_scan(bus, dbus_module)
            if not found_any:
                self._log("no devices visible, triggering deep recovery")
                _shell_enable_bluetooth(self.hci, self._log)
                found_any = self._preflight_scan(bus, dbus_module, scan_timeout=8.0)
                if not found_any:
                    self._log(
                        "still no devices visible after recovery — "
                        "proceeding to search for target anyway"
                    )
            device_path = self._discover_device_path(bus, dbus_module)
        if not device_path:
            raise DeviceError(f"Could not find device {self.mac}")

        dev = bus.get_object(BLUEZ_SVC, device_path)
        props = dbus_module.Interface(dev, PROPS_IFACE)
        if props.Get(DEVICE_IFACE, "Connected"):
            self._log("already connected")
            return

        self._log(f"connecting via {self.hci}")
        try:
            dbus_module.Interface(dev, DEVICE_IFACE).Connect()
        except Exception as exc:
            raise DeviceError(f"Connection attempt failed: {exc}") from exc
        for _ in range(30):
            time.sleep(0.5)
            if props.Get(DEVICE_IFACE, "Connected"):
                self._log("connected")
                return
        raise DeviceError("Could not connect to device")

    def _resolve_paths(self, bus) -> tuple[str, str]:
        self._log("waiting for GATT services")
        ff01_path = ff02_path = None
        device_path = self._resolve_device_path(bus)
        if not device_path:
            raise DeviceError(f"Could not resolve device path for {self.mac}")
        for _ in range(20):
            ff01_path = self._find_char(bus, FF01_UUID_PREFIX, device_path=device_path)
            ff02_path = self._find_char(bus, FF02_UUID_PREFIX, device_path=device_path)
            if ff01_path and ff02_path:
                return ff01_path, ff02_path
            time.sleep(0.5)
        raise DeviceError("FF01/FF02 characteristics not found")

    def _write_value(self, ff02, frame: list[int], dbus_module) -> None:
        for index in range(0, len(frame), 20):
            chunk = frame[index:index + 20]
            for attempt in range(8):
                try:
                    ff02.WriteValue(dbus_module.Array([dbus_module.Byte(b) for b in chunk], signature="y"), {})
                    break
                except Exception as exc:
                    if not _is_in_progress_error(exc) or attempt == 7:
                        raise
                    time.sleep(0.15)
            if index + 20 < len(frame):
                time.sleep(0.1)

    def _with_vendor_chars(self):
        for attempt in range(3):
            bus, dbus_module = self._bus()
            try:
                self._ensure_adapter_powered(bus, dbus_module)
                self._ensure_connected(bus, dbus_module)
                ff01_path, ff02_path = self._resolve_paths(bus)
                ff01 = dbus_module.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
                ff02 = dbus_module.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)
                return bus, dbus_module, ff01_path, ff01, ff02
            except DeviceError as exc:
                if attempt < 2 and "not connected" in str(exc).lower():
                    self._log(f"vendor chars attempt {attempt + 1} failed ({exc}), retrying")
                    time.sleep(2.0)
                    continue
                raise

    def _run_ff01_command(
        self,
        command: list[int],
        *,
        matcher: Callable[[list[int]], bool],
        timeout_ms: int = 8000,
    ) -> list[int]:
        def operation() -> list[int]:
            bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
            _dbus, GLib = _load_bluez_modules()
            result: dict[str, list[int]] = {}
            loop = GLib.MainLoop()

            def on_changed(_iface, changed, _invalidated, path=None):
                if "Value" not in changed:
                    return
                data = [int(byte) for byte in changed["Value"]]
                self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
                if matcher(data):
                    result["data"] = data
                    loop.quit()

            bus.add_signal_receiver(
                on_changed,
                signal_name="PropertiesChanged",
                dbus_interface=PROPS_IFACE,
                path=ff01_path,
                path_keyword="path",
            )

            def run():
                try:
                    ff01.StartNotify()
                    time.sleep(0.3)
                    self._log(f"sending {' '.join(f'{b:02x}' for b in command)}")
                    self._write_value(ff02, command, dbus_module)
                    GLib.timeout_add(timeout_ms, loop.quit)
                except Exception as exc:
                    result["error"] = exc
                    loop.quit()

            GLib.timeout_add(200, run)
            loop.run()

            try:
                ff01.StopNotify()
            except Exception:
                pass

            if "error" in result:
                raise DeviceError(f"Could not start BLE notifications: {result['error']}") from result["error"]
            if "data" not in result:
                raise DeviceError("No response received (timeout)")
            return result["data"]

        return _run_with_notify_retries(self, operation, context="ff01-command")

    def _run_ff01_fragmented_command(
        self,
        command: list[int],
        *,
        response_cmd: int,
        timeout_ms: int = 30_000,
    ) -> list[int]:
        """Like _run_ff01_command but reassembles multi-chunk BLE notifications.

        Accumulates incoming 20-byte chunks into a buffer, determines the total
        expected frame length from the 3-byte header (cmd + len_lo + len_hi), and
        only resolves once the full frame has arrived.
        """
        def operation() -> list[int]:
            bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
            _dbus, GLib = _load_bluez_modules()
            result: dict[str, object] = {}
            loop = GLib.MainLoop()
            buf: list[int] = []
            expected_len: list[int | None] = [None]

            def on_changed(_iface, changed, _invalidated, path=None):
                if "Value" not in changed:
                    return
                chunk = [int(b) for b in changed["Value"]]
                self._log(
                    f"FF01 chunk ({len(chunk)}B): {' '.join(f'{b:02x}' for b in chunk)}"
                )
                buf.extend(chunk)

                if expected_len[0] is None and len(buf) >= 3:
                    payload_len = buf[1] | (buf[2] << 8)
                    expected_len[0] = 3 + payload_len + 1  # header + payload + checksum

                if expected_len[0] is not None and len(buf) >= expected_len[0]:
                    frame = buf[:expected_len[0]]
                    if frame[0] == response_cmd:
                        result["data"] = frame
                        loop.quit()

            bus.add_signal_receiver(
                on_changed,
                signal_name="PropertiesChanged",
                dbus_interface=PROPS_IFACE,
                path=ff01_path,
                path_keyword="path",
            )

            def run():
                try:
                    ff01.StartNotify()
                    time.sleep(0.3)
                    self._log(f"sending {' '.join(f'{b:02x}' for b in command)}")
                    self._write_value(ff02, command, dbus_module)
                    GLib.timeout_add(timeout_ms, loop.quit)
                except Exception as exc:
                    result["error"] = exc
                    loop.quit()

            GLib.timeout_add(200, run)
            loop.run()

            try:
                ff01.StopNotify()
            except Exception:
                pass

            if "error" in result:
                raise DeviceError(f"Could not start BLE notifications: {result['error']}") from result["error"]
            if "data" not in result:
                raise DeviceError("No response received (timeout)")
            return result["data"]

        return _run_with_notify_retries(self, operation, context="ff01-fragmented-command")

    def _run_notification_sequence(self, frames: list[list[int]], *, do_init: bool) -> None:
        def operation() -> None:
            bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
            _dbus, GLib = _load_bluez_modules()
            state = {
                "phase": "get_state" if do_init else "notify",
                "notify_stage": 0,
                "device_state_payload": [],
            }
            loop = GLib.MainLoop()

            def send_notification_stage(stage: int) -> None:
                frame = frames[stage]
                self._log(f"sending stage {stage}: {' '.join(f'{b:02x}' for b in frame)}")
                self._write_value(ff02, frame, dbus_module)

            def on_changed(_iface, changed, _invalidated, path=None):
                if "Value" not in changed:
                    return
                data = [int(byte) for byte in changed["Value"]]
                if not data:
                    return

                first = data[0]
                length = (data[2] << 8 | data[1]) if len(data) >= 3 else 0
                self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
                phase = state["phase"]

                if phase == "get_state" and first == 0x82 and length > 1:
                    payload = data[3:-1]
                    state["device_state_payload"] = payload
                    state["phase"] = "set_state"
                    GLib.timeout_add(200, lambda: self._write_value(ff02, frame_set_device_state(payload), dbus_module) or False)
                    return

                if phase == "set_state" and first == 0x82 and length == 1:
                    state["phase"] = "set_time"
                    GLib.timeout_add(200, lambda: self._write_value(ff02, frame_set_time(), dbus_module) or False)
                    return

                if phase == "set_time" and first == 0x84 and length == 1:
                    state["phase"] = "set_notice"
                    GLib.timeout_add(200, lambda: self._write_value(ff02, CMD_SET_NOTICE_ALL, dbus_module) or False)
                    return

                if phase == "set_notice" and first == 0x89 and length == 1:
                    state["phase"] = "notify"
                    GLib.timeout_add(200, lambda: send_notification_stage(0) or False)
                    return

                if phase == "notify" and first == 0x8A and len(data) >= 4:
                    stage = data[3]
                    if stage != state["notify_stage"]:
                        return
                    if stage >= len(frames) - 1:
                        loop.quit()
                        return
                    state["notify_stage"] += 1
                    GLib.timeout_add(200, lambda: send_notification_stage(state["notify_stage"]) or False)

            bus.add_signal_receiver(
                on_changed,
                signal_name="PropertiesChanged",
                dbus_interface=PROPS_IFACE,
                path=ff01_path,
                path_keyword="path",
            )

            def run():
                try:
                    ff01.StartNotify()
                    time.sleep(0.3)
                    if do_init:
                        self._write_value(ff02, CMD_GET_DEVICE_STATE, dbus_module)
                    else:
                        send_notification_stage(0)
                    GLib.timeout_add(12000, loop.quit)
                except Exception as exc:
                    state["error"] = exc
                    loop.quit()

            GLib.timeout_add(200, run)
            loop.run()

            try:
                ff01.StopNotify()
            except Exception:
                pass

            if "error" in state:
                raise DeviceError(f"Could not start BLE notifications: {state['error']}") from state["error"]
            if state["notify_stage"] != len(frames) - 1:
                raise DeviceError("Notification sequence did not complete")

        _run_with_notify_retries(self, operation, context="notify-sequence")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read_info(self) -> dict[str, str]:
        bus, dbus_module = self._bus()
        self._ensure_connected(bus, dbus_module)
        path = self._find_char(bus, DEVICE_NAME_UUID_PREFIX)
        if not path:
            raise DeviceError("Device name characteristic not found")
        char = dbus_module.Interface(bus.get_object(BLUEZ_SVC, path), GATT_IFACE)
        data = [int(byte) for byte in char.ReadValue({})]
        name = bytes(data).split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return {"name": name}

    def read_battery(self) -> int:
        response = self._run_ff01_command(
            CMD_GET_CURRENT_POWER,
            matcher=lambda data: len(data) >= 4 and data[0] == 0xA7,
        )
        return int(response[3])

    def read_health(self, date: dt.date | None = None) -> dict[str, object]:
        """Read current health snapshot and optionally historical data for one day.

        Always fetches today's historical HR data to derive the real device-side
        timestamp of the last measurement (minute precision).  If *date* is today
        those records are reused; otherwise a second historical fetch is issued.

        Returns a dict with up to four keys:
          - "last_measured":  ISO-8601 datetime of the last recorded measurement
                              on the device (YYYY-MM-DDTHH:MM), derived from the
                              most recent non-zero record in today's history.
          - "snapshot_steps": {"steps", "calories_kcal", "distance_m"}
          - "snapshot_hart":  {"hr_bpm", "bp_diastolic_mmhg", "bp_systolic_mmhg", "spo2_pct"}
          - "history_hart":   list of per-minute records for *date* (only if date is given)
        """
        result: dict[str, object] = {}
        today = dt.date.today()

        # Current steps / calories / distance
        step_data = self._run_ff01_command(
            CMD_GET_CURRENT_STEP,
            matcher=lambda data: len(data) >= 4 and data[0] == 0xA0 and data[3] == 0x00,
        )
        snap_steps = decode_steps_snapshot(step_data)
        if snap_steps is not None:
            result["snapshot_steps"] = snap_steps

        # Current HR / BP / SpO2
        hart_data = self._run_ff01_command(
            CMD_GET_HART_SNAPSHOT,
            matcher=lambda data: len(data) >= 4 and data[0] == 0xA1 and data[3] == 0x00,
        )
        snap_hart = decode_hart_snapshot(hart_data)
        if snap_hart is not None:
            result["snapshot_hart"] = snap_hart

        # Fetch today's historical HR to get the real last-measurement timestamp.
        # decode_hart_history skips all-zero records, so the last element is the
        # last minute on the device that had an actual reading.
        today_raw = self._run_ff01_fragmented_command(
            frame_health_hist_query(0x21, today),
            response_cmd=0xA1,
            timeout_ms=30_000,
        )
        hist_today = decode_hart_history(today_raw, today)
        if hist_today:
            last = hist_today[-1]
            result["last_measured"] = f"{today.isoformat()}T{last['time']}"

        # Historical data for the requested date.
        # Reuse today's records if date == today to avoid a second BLE fetch.
        if date is not None:
            if date == today:
                result["history_hart"] = hist_today if hist_today is not None else []
            else:
                hist_raw = self._run_ff01_fragmented_command(
                    frame_health_hist_query(0x21, date),
                    response_cmd=0xA1,
                    timeout_ms=30_000,
                )
                hist = decode_hart_history(hist_raw, date)
                if hist is not None:
                    result["history_hart"] = hist

        return result

    def find_device(self) -> None:
        self._run_ff01_command(
            FIND_DEVICE_CMD,
            matcher=lambda data: len(data) >= 1 and data[0] == 0x90,
        )

    def vibrate(self) -> None:
        self.find_device()

    def send_raw_hex(self, hex_bytes: list[str]) -> list[int] | None:
        try:
            frame = [int(part, 16) for part in hex_bytes]
        except ValueError as exc:
            raise DeviceError("Raw bytes must be valid hex values") from exc

        try:
            return self._run_ff01_command(frame, matcher=lambda data: True, timeout_ms=5000)
        except DeviceError as exc:
            if "timeout" in str(exc).lower():
                return None
            raise

    def send_notification(self, *, app_name: str, title: str, body: str, do_init: bool = True) -> None:
        app_type = APP_TYPES.get(app_name.lower(), APP_TYPES["whatsapp"])
        frames = [
            frame_message_type(app_type),
            frame_message_part(1, title, 32),
            frame_message_part(2, body, 128),
            END_MESSAGE,
        ]
        self._run_notification_sequence(frames, do_init=do_init)

    def send_sms(self, *, sender: str, text: str, do_init: bool = True) -> None:
        frames = [
            frame_message_type(APP_TYPE_SMS),
            frame_message_part(1, sender, 32),
            frame_message_part(2, text, 128),
            END_MESSAGE,
        ]
        self._run_notification_sequence(frames, do_init=do_init)

    def send_call(self, *, caller: str, number: str, do_init: bool = True) -> None:
        title = caller or number or "Unknown"
        body = number if caller and number else ""
        frames = [
            frame_message_type(APP_TYPE_CALL),
            frame_message_part(1, title, 32),
            frame_message_part(2, body, 128),
            END_MESSAGE,
        ]
        self._run_notification_sequence(frames, do_init=do_init)

    def listen_for_button(
        self,
        *,
        timeout: float | None = None,
        max_events: int | None = None,
        on_event: Callable[[], None] | None = None,
        stop: threading.Event | None = None,
    ) -> int:
        bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
        _dbus, GLib = _load_bluez_modules()
        loop = GLib.MainLoop()
        events = {"count": 0}
        last_event_at = {"value": None}

        def on_changed(_iface, changed, _invalidated, path=None):
            if "Value" not in changed:
                return
            data = [int(byte) for byte in changed["Value"]]
            self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
            if data == CAMERA_BUTTON_EVENT:
                now = time.monotonic()
                previous = last_event_at["value"]
                if previous is not None and (now - previous) < CAMERA_BUTTON_DEDUP_SECONDS:
                    return
                last_event_at["value"] = now
                events["count"] += 1
                print(f"Button event #{events['count']}")
                if on_event is not None:
                    on_event()
                if max_events is not None and events["count"] >= max_events:
                    loop.quit()

        bus.add_signal_receiver(
            on_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=ff01_path,
            path_keyword="path",
        )

        def check_stop():
            if stop is not None and stop.is_set():
                loop.quit()
                return False
            return True

        def run():
            try:
                ff01.StartNotify()
                time.sleep(0.3)
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
                self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
                if timeout is not None:
                    GLib.timeout_add(int(timeout * 1000), loop.quit)
                if stop is not None:
                    GLib.timeout_add(100, check_stop)
            except Exception as exc:
                events["error"] = exc
                loop.quit()

        GLib.timeout_add(200, run)
        try:
            loop.run()
        finally:
            try:
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_EXIT_CMD)}")
                self._write_value(ff02, CAMERA_MODE_EXIT_CMD, dbus_module)
                time.sleep(0.3)
            except Exception:
                pass
            try:
                ff01.StopNotify()
            except Exception:
                pass

        if "error" in events:
            raise DeviceError(f"Could not start BLE notifications: {events['error']}") from events["error"]
        return int(events["count"])

    def listen_for_find_phone(
        self,
        *,
        timeout: float | None = None,
        max_events: int | None = None,
        on_event: Callable[[], None] | None = None,
    ) -> int:
        """Listen on FF01 for the bracelet's find-phone trigger.

        The bracelet sends FIND_PHONE_EVENT spontaneously when the user activates
        find-phone on the device.  No camera-mode handshake is needed — only an
        FF01 notification subscription is required.

        Matched as a prefix: the sniffed frame is 12 bytes but the documented
        frame is 20 bytes; both start with the same FIND_PHONE_EVENT prefix.
        """
        bus, dbus_module, ff01_path, ff01, _ff02 = self._with_vendor_chars()
        _dbus, GLib = _load_bluez_modules()
        loop = GLib.MainLoop()
        events: dict[str, object] = {"count": 0}
        prefix = FIND_PHONE_EVENT

        def on_changed(_iface, changed, _invalidated, path=None):
            if "Value" not in changed:
                return
            data = [int(b) for b in changed["Value"]]
            self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
            if data[:len(prefix)] == prefix:
                count = int(events["count"]) + 1
                events["count"] = count
                print(f"Find-phone event #{count}")
                if on_event is not None:
                    on_event()
                if max_events is not None and count >= max_events:
                    loop.quit()

        bus.add_signal_receiver(
            on_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=ff01_path,
            path_keyword="path",
        )

        def run():
            try:
                ff01.StartNotify()
                if timeout is not None:
                    GLib.timeout_add(int(timeout * 1000), loop.quit)
            except Exception as exc:
                events["error"] = exc
                loop.quit()

        GLib.timeout_add(200, run)
        try:
            loop.run()
        finally:
            try:
                ff01.StopNotify()
            except Exception:
                pass

        if "error" in events:
            raise DeviceError(f"Could not start BLE notifications: {events['error']}") from events["error"]
        return int(events["count"])

    def run_dialer(
        self,
        *,
        arm_timeout: float = 5.0,
        cluster_gap: float = 0.9,
        k_min: int = 3,
        k_max: int = 4,
    ) -> str:
        bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
        _dbus, GLib = _load_bluez_modules()
        loop = GLib.MainLoop()
        started_at = time.monotonic()
        last_press_at = {"value": None}
        cluster_count = {"value": 0}
        session = {"open": False, "open_t_count": 0, "exit_k_count": 0}
        result = {"status": "timeout"}
        feedback_messages: list[str] = []
        last_event_at = {"value": None}

        def send_open_feedback() -> None:
            self._log(f"sending {' '.join(f'{b:02x}' for b in FIND_DEVICE_CMD)}")
            self._write_value(ff02, FIND_DEVICE_CMD, dbus_module)
            time.sleep(0.8)
            self._log(f"sending {' '.join(f'{b:02x}' for b in FIND_DEVICE_CMD)}")
            self._write_value(ff02, FIND_DEVICE_CMD, dbus_module)

        def flush_cluster() -> None:
            count = cluster_count["value"]
            if count <= 0:
                return

            symbol = None
            if count == 1:
                symbol = "T"
            elif k_min <= count <= k_max:
                symbol = "K"

            cluster_count["value"] = 0
            last_press_at["value"] = None

            if symbol is None:
                self._log(f"ignoring cluster of {count} button presses")
                if not session["open"]:
                    print(f"Dialer exited: invalid opening cluster ({count} presses)")
                    result["status"] = "invalid-opening-cluster"
                    loop.quit()
                return

            print(symbol)

            if not session["open"]:
                if symbol != "T":
                    print("Dialer exited: expected T T T to open the session")
                    result["status"] = "expected-opening-t"
                    loop.quit()
                    return

                session["open_t_count"] += 1
                print(f"OPEN {session['open_t_count']}/3")
                if session["open_t_count"] >= 3:
                    session["open"] = True
                    session["exit_k_count"] = 0
                    print("SESSION OPEN")
                    send_open_feedback()
                return

            print(f"TRACE {symbol}")
            if symbol == "K":
                session["exit_k_count"] += 1
                if session["exit_k_count"] >= 2:
                    print("SESSION CLOSE")
                    feedback_messages.append("dialer off")
                    result["status"] = "closed"
                    loop.quit()
            else:
                session["exit_k_count"] = 0

        def on_changed(_iface, changed, _invalidated, path=None):
            if "Value" not in changed:
                return
            data = [int(byte) for byte in changed["Value"]]
            self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
            if data != CAMERA_BUTTON_EVENT:
                return

            now = time.monotonic()
            previous_event = last_event_at["value"]
            if previous_event is not None and (now - previous_event) < CAMERA_BUTTON_DEDUP_SECONDS:
                return
            last_event_at["value"] = now
            previous = last_press_at["value"]
            if previous is not None and now - previous > cluster_gap:
                flush_cluster()

            cluster_count["value"] += 1
            last_press_at["value"] = now

        def heartbeat():
            if result["status"] != "timeout":
                return False

            now = time.monotonic()
            last = last_press_at["value"]
            if last is not None and now - last > cluster_gap:
                flush_cluster()
                if result["status"] != "timeout":
                    return False

            if not session["open"] and now - started_at > arm_timeout:
                print("Dialer timeout: T T T not received")
                result["status"] = "open-timeout"
                loop.quit()
                return False

            return True

        bus.add_signal_receiver(
            on_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=ff01_path,
            path_keyword="path",
        )

        def run():
            try:
                ff01.StartNotify()
                time.sleep(0.3)
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
                self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
                GLib.timeout_add(100, heartbeat)
            except Exception as exc:
                result["error"] = exc
                loop.quit()

        GLib.timeout_add(200, run)
        try:
            loop.run()
        finally:
            try:
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_EXIT_CMD)}")
                self._write_value(ff02, CAMERA_MODE_EXIT_CMD, dbus_module)
                time.sleep(0.3)
            except Exception:
                pass
            try:
                ff01.StopNotify()
            except Exception:
                pass

        if "error" in result:
            raise DeviceError(f"Could not start BLE notifications: {result['error']}") from result["error"]
        for message in feedback_messages:
            try:
                self.send_notification(
                    app_name="whatsapp",
                    title="wrish",
                    body=message,
                    do_init=True,
                )
            except Exception as exc:
                self._log(f"feedback message failed: {exc}")

        return str(result["status"])

    def calibrate_button_cluster(
        self,
        *,
        timeout: float = 8.0,
        idle_gap: float = 1.2,
    ) -> str:
        bus, dbus_module, ff01_path, ff01, ff02 = self._with_vendor_chars()
        _dbus, GLib = _load_bluez_modules()
        loop = GLib.MainLoop()
        press_times: list[float] = []
        report = {"text": ""}
        last_event_at = {"value": None}

        def finish() -> None:
            if report["text"]:
                return
            report["text"] = format_calibration_report(press_times)
            loop.quit()

        def on_changed(_iface, changed, _invalidated, path=None):
            if "Value" not in changed:
                return
            data = [int(byte) for byte in changed["Value"]]
            self._log(f"FF01: {' '.join(f'{b:02x}' for b in data)}")
            if data != CAMERA_BUTTON_EVENT:
                return
            now = time.monotonic()
            previous = last_event_at["value"]
            if previous is not None and (now - previous) < CAMERA_BUTTON_DEDUP_SECONDS:
                return
            last_event_at["value"] = now
            press_times.append(now)
            print(f"press #{len(press_times)}")

        def heartbeat():
            if press_times and (time.monotonic() - press_times[-1]) > idle_gap:
                finish()
                return False
            return True

        bus.add_signal_receiver(
            on_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=ff01_path,
            path_keyword="path",
        )

        def run():
            try:
                ff01.StartNotify()
                time.sleep(0.3)
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
                self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
                GLib.timeout_add(100, heartbeat)
                GLib.timeout_add(int(timeout * 1000), lambda: finish() or False)
            except Exception as exc:
                report["error"] = exc
                loop.quit()

        GLib.timeout_add(200, run)
        try:
            loop.run()
        finally:
            try:
                self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_EXIT_CMD)}")
                self._write_value(ff02, CAMERA_MODE_EXIT_CMD, dbus_module)
                time.sleep(0.3)
            except Exception:
                pass
            try:
                ff01.StopNotify()
            except Exception:
                pass

        if "error" in report:
            raise DeviceError(f"Could not start BLE notifications: {report['error']}") from report["error"]
        return report["text"] or format_calibration_report(press_times)
