from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import subprocess
import sys
import time
from typing import Callable


APP_TYPES = {
    "wechat": 2,
    "qq": 3,
    "facebook": 4,
    "skype": 5,
    "twitter": 6,
    "whatsapp": 7,
    "line": 8,
    "linkedin": 9,
    "instagram": 10,
    "messenger": 12,
    "vk": 13,
    "viber": 14,
    "telegram": 16,
    "kakaotalk": 18,
    "douyin": 32,
    "kuaishou": 33,
    "douyin_lite": 34,
    "maimai": 52,
    "pinduoduo": 53,
    "work_wechat": 54,
    "tantan": 56,
    "taobao": 57,
}

BLUEZ_SVC = "org.bluez"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE = "org.bluez.GattCharacteristic1"
OM_IFACE = "org.freedesktop.DBus.ObjectManager"
ADAPTER_IFACE = "org.bluez.Adapter1"

FF01_UUID_PREFIX = "0000ff01"
FF02_UUID_PREFIX = "0000ff02"
DEVICE_NAME_UUID_PREFIX = "00002a00"

CMD_GET_DEVICE_STATE = [0x02, 0x00, 0x00, 0x06]
CMD_SET_NOTICE_ALL = [0x09, 0x04, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0x60]
CMD_GET_CURRENT_POWER = [0x27, 0x00, 0x00, 0x74]
END_MESSAGE = [0x0A, 0x01, 0x00, 0x03, 0x0E]

APP_TYPE_CALL = 0x00
APP_TYPE_SMS = 0x01

FIND_DEVICE_CMD = [
    0x10, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
    0x00, 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]
CAMERA_MODE_ENTER_CMD = [0x10, 0x08, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0xC0]
CAMERA_MODE_EXIT_CMD = [0x10, 0x08, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x6C]
CAMERA_BUTTON_EVENT = [0x90, 0x08, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x16]
CAMERA_BUTTON_DEDUP_SECONDS = 0.05


class DeviceError(RuntimeError):
    pass


def _is_in_progress_error(exc: Exception) -> bool:
    return "org.bluez.Error.InProgress" in str(exc)


def _load_bluez_modules():
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
    except ImportError as exc:
        raise DeviceError(
            "Missing BlueZ Python dependencies. Install python3-dbus and python3-gi."
        ) from exc
    return dbus, GLib


def _start_discovery(adapter, dbus_module, log_fn) -> bool:
    """
    Start BLE discovery the way bluetoothctl does:
    SetDiscoveryFilter first, then StartDiscovery.
    Tries progressively looser filters so it always has the best chance.
    Returns True if discovery was started successfully.
    """
    attempts = [
        {"Transport": dbus_module.String("le")},   # BLE only — ideal for wristbands
        {"Transport": dbus_module.String("auto")},  # BLE + classic auto-select
        {},                                          # no filter — widest net
    ]
    for flt in attempts:
        try:
            adapter.SetDiscoveryFilter(flt)
            adapter.StartDiscovery()
            transport = flt.get("Transport", "all")
            log_fn(f"discovery started (transport={transport})")
            return True
        except Exception as exc:
            log_fn(f"discovery attempt failed ({exc}), trying next filter")
    return False


def _hci_adapters_in_os() -> list[str]:
    """Return hci adapter names visible to the kernel (UP or DOWN)."""
    import re
    try:
        r = subprocess.run(["hciconfig", "-a"], timeout=5, capture_output=True, text=True)
        return re.findall(r"^(hci\d+):", r.stdout, re.MULTILINE)
    except Exception:
        return []


def _shell_enable_bluetooth(hci: str, log_fn) -> None:
    """Escalating best-effort recovery: rfkill → hciconfig → bluetoothctl → systemctl."""

    def _run(cmd: list[str], t: int = 5) -> bool:
        try:
            log_fn(f"shell: {' '.join(cmd)}")
            r = subprocess.run(cmd, timeout=t, capture_output=True)
            return r.returncode == 0
        except Exception:
            return False

    # 1. rfkill unblock (soft/hard block)
    _run(["rfkill", "unblock", "bluetooth"]) or _run(["rfkill", "unblock", "all"])
    time.sleep(0.5)

    # 2. hciconfig <hci> up — try direct then non-interactive sudo
    if not _run(["hciconfig", hci, "up"]):
        _run(["sudo", "-n", "hciconfig", hci, "up"])
    time.sleep(0.5)

    # 3. bluetoothctl power on
    try:
        log_fn("shell: bluetoothctl power on")
        r = subprocess.run(
            ["bluetoothctl", "power", "on"],
            timeout=10,
            capture_output=True,
            text=True,
        )
        out = (r.stdout + r.stderr).strip()
        if out:
            log_fn(f"bluetoothctl: {out}")
    except Exception:
        pass
    time.sleep(1.0)

    # 4. Brief bluetoothctl scan le warm-up — wakes up the BLE stack
    try:
        log_fn("shell: bluetoothctl scan le (warm-up)")
        subprocess.run(
            ["timeout", "3", "bluetoothctl", "scan", "le"],
            timeout=5,
            capture_output=True,
        )
    except Exception:
        pass
    time.sleep(1.0)

    # 5. Restart bluetooth service (non-interactive sudo, then plain)
    if not _run(["sudo", "-n", "systemctl", "restart", "bluetooth.service"], 15):
        _run(["systemctl", "restart", "bluetooth.service"], 15)
    time.sleep(3.0)


def checksum(frame: list[int]) -> int:
    total = 0
    for byte in frame:
        total = (total + byte) & 0xFF
    return ((total * 0x56) + 0x5A) & 0xFF


def frame_set_device_state(state_payload: list[int]) -> list[int]:
    payload = list(state_payload)
    if len(payload) >= 9:
        payload[8] = 0x01
    if len(payload) >= 15:
        payload[14] = 0x02
    frame = [0x02, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return frame + [checksum(frame)]


def frame_set_time(now: dt.datetime | None = None) -> list[int]:
    now = now or dt.datetime.now()
    payload = [
        now.year & 0xFF,
        (now.year >> 8) & 0xFF,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        0x00,
    ]
    frame = [0x04, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return frame + [checksum(frame)]


def frame_message_type(app_type: int) -> list[int]:
    frame = [0x0A, 0x02, 0x00, 0x00, app_type]
    return frame + [checksum(frame)]


def frame_message_part(kind: int, text: str, max_len: int) -> list[int]:
    payload_bytes = list(text.encode("utf-8")[:max_len])
    payload_length = 1 + len(payload_bytes)
    frame = [0x0A, payload_length & 0xFF, (payload_length >> 8) & 0xFF, kind] + payload_bytes
    return frame + [checksum(frame)]


def decode_dialer_symbols(symbols: list[str]) -> str | None:
    armed = False
    digits: list[str] = []
    taps = 0

    for symbol in symbols:
        if not armed:
            if symbol == "K":
                armed = True
            continue

        if symbol == "T":
            taps += 1
            continue

        if symbol != "K":
            continue

        if taps == 0:
            return "".join(digits) if digits else None

        digits.append(str(taps))
        taps = 0

    return None


def format_calibration_report(press_times: list[float]) -> str:
    if not press_times:
        return "CALIBRATION\npresses=0\n"

    base = press_times[0]
    relative = [timestamp - base for timestamp in press_times]
    deltas = [press_times[index] - press_times[index - 1] for index in range(1, len(press_times))]

    lines = [
        "CALIBRATION",
        f"presses={len(press_times)}",
        "relative_seconds=" + ",".join(f"{value:.3f}" for value in relative),
        "delta_seconds=" + ",".join(f"{value:.3f}" for value in deltas) if deltas else "delta_seconds=",
    ]

    if deltas:
        suggested_gap = max(deltas) + 0.25
        lines.append(f"suggested_cluster_gap={suggested_gap:.3f}")

    return "\n".join(lines) + "\n"


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

    def _preflight_scan(self, bus, dbus_module, scan_timeout: float = 5.0) -> None:
        """Short discovery pass to verify BT can see at least one device in range."""
        adapter_obj = bus.get_object(BLUEZ_SVC, self.adapter_path)
        adapter = dbus_module.Interface(adapter_obj, ADAPTER_IFACE)
        manager = self._get_manager(bus, dbus_module)

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
                    return
        finally:
            if started:
                try:
                    adapter.StopDiscovery()
                except Exception:
                    pass

        raise DeviceError(
            f"Bluetooth adapter {self.hci} is on but no devices found — "
            "ensure the target device is powered on and in range"
        )

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
            self._preflight_scan(bus, dbus_module)
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
        bus, dbus_module = self._bus()
        self._ensure_adapter_powered(bus, dbus_module)
        self._ensure_connected(bus, dbus_module)
        ff01_path, ff02_path = self._resolve_paths(bus)
        ff01 = dbus_module.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
        ff02 = dbus_module.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)
        return bus, dbus_module, ff01_path, ff01, ff02

    def _run_ff01_command(
        self,
        command: list[int],
        *,
        matcher: Callable[[list[int]], bool],
        timeout_ms: int = 8000,
    ) -> list[int]:
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
            ff01.StartNotify()
            time.sleep(0.3)
            self._log(f"sending {' '.join(f'{b:02x}' for b in command)}")
            self._write_value(ff02, command, dbus_module)
            GLib.timeout_add(timeout_ms, loop.quit)

        GLib.timeout_add(200, run)
        loop.run()

        try:
            ff01.StopNotify()
        except Exception:
            pass

        if "data" not in result:
            raise DeviceError("No response received (timeout)")
        return result["data"]

    def _run_notification_sequence(self, frames: list[list[int]], *, do_init: bool) -> None:
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
            ff01.StartNotify()
            time.sleep(0.3)
            if do_init:
                self._write_value(ff02, CMD_GET_DEVICE_STATE, dbus_module)
            else:
                send_notification_stage(0)
            GLib.timeout_add(12000, loop.quit)

        GLib.timeout_add(200, run)
        loop.run()

        try:
            ff01.StopNotify()
        except Exception:
            pass

        if state["notify_stage"] != len(frames) - 1:
            raise DeviceError("Notification sequence did not complete")

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

    def listen_for_button(self, *, timeout: float | None = None, max_events: int | None = None) -> int:
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
                if max_events is not None and events["count"] >= max_events:
                    loop.quit()

        bus.add_signal_receiver(
            on_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=ff01_path,
            path_keyword="path",
        )

        def run():
            ff01.StartNotify()
            time.sleep(0.3)
            self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
            self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
            if timeout is not None:
                GLib.timeout_add(int(timeout * 1000), loop.quit)

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
            ff01.StartNotify()
            time.sleep(0.3)
            self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
            self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
            GLib.timeout_add(100, heartbeat)

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
            ff01.StartNotify()
            time.sleep(0.3)
            self._log(f"sending {' '.join(f'{b:02x}' for b in CAMERA_MODE_ENTER_CMD)}")
            self._write_value(ff02, CAMERA_MODE_ENTER_CMD, dbus_module)
            GLib.timeout_add(100, heartbeat)
            GLib.timeout_add(int(timeout * 1000), lambda: finish() or False)

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

        return report["text"] or format_calibration_report(press_times)
