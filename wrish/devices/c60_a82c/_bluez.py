from __future__ import annotations

import os
import re
import subprocess
import time


class DeviceError(RuntimeError):
    pass


def _is_in_progress_error(exc: Exception) -> bool:
    return "org.bluez.Error.InProgress" in str(exc)


def _is_not_connected_error(exc: Exception) -> bool:
    return "org.bluez.Error.Failed: Not connected" in str(exc)


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
        {"Transport": dbus_module.String("le")},    # BLE only — ideal for wristbands
        {"Transport": dbus_module.String("auto")},   # BLE + classic auto-select
        {},                                           # no filter — widest net
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
    try:
        r = subprocess.run(["hciconfig", "-a"], timeout=5, capture_output=True, text=True)
        return re.findall(r"^(hci\d+):", r.stdout, re.MULTILINE)
    except Exception:
        return []


def _allow_system_service_restart() -> bool:
    return os.environ.get("WRISH_ALLOW_SYSTEM_BT_RESTART", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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

    # 2. hciconfig <hci> up
    _run(["hciconfig", hci, "up"], t=10)
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

    # 5. Optional system bluetooth.service restart for explicitly enabled hosts.
    if _allow_system_service_restart():
        _run(["systemctl", "restart", "bluetooth.service"], t=15)
        time.sleep(3.0)
    else:
        log_fn(
            "shell: skipping system bluetooth.service restart "
            "(set WRISH_ALLOW_SYSTEM_BT_RESTART=1 to enable)"
        )


def _shell_cycle_bluetooth(hci: str, log_fn) -> None:
    """Best-effort adapter power cycle before the usual enable/recovery path."""

    def _run(cmd: list[str], t: int = 10) -> bool:
        try:
            log_fn(f"shell: {' '.join(cmd)}")
            r = subprocess.run(cmd, timeout=t, capture_output=True)
            return r.returncode == 0
        except Exception:
            return False

    log_fn(f"cycling bluetooth adapter {hci}")

    try:
        log_fn("shell: bluetoothctl power off")
        subprocess.run(
            ["bluetoothctl", "power", "off"],
            timeout=10,
            capture_output=True,
            text=True,
        )
    except Exception:
        pass

    _run(["hciconfig", hci, "down"], t=10)
    _run(["rfkill", "block", "bluetooth"]) or _run(["rfkill", "block", "all"])
    time.sleep(1.0)
    _run(["rfkill", "unblock", "bluetooth"]) or _run(["rfkill", "unblock", "all"])
    time.sleep(0.5)

    _shell_enable_bluetooth(hci, log_fn)
