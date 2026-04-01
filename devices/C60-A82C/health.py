#!/usr/bin/env python3
"""Read health data from C60-A82C bracelet via BlueZ D-Bus.

Snapshot (current values):
  CMD_GET_CURRENT_STEP  FF02 ← 20 01 00 00 70  → a0 0d 00 00 [steps u32 LE] [cals u32 LE] [dist u32 LE] [chk]
  CMD_GET_HART          FF02 ← 21 01 00 00 c6  → a1 05 00 00 [HR] [diastolic] [systolic] [SpO2] [chk]

Historical (minute-by-minute, one day):
  CMD_GET_STEP_HIST     FF02 ← 20 05 00 01 [year_lo year_hi mm dd] [chk]
  CMD_GET_HART_HIST     FF02 ← 21 05 00 01 [year_lo year_hi mm dd] [chk]
  Response header: a0/a1 [len_lo len_hi] 01 [year_lo year_hi mm dd] 0f [records...] [chk]
  HR/BP/SpO2 record: 4 bytes [HR diastolic systolic SpO2] per minute (00 00 00 00 = no data)

Checksum: ((sum_of_all_bytes_before_chk * 0x56) + 0x5A) & 0xFF

Usage:
  python3 health.py [--mac AA:BB:CC:DD:EE:FF] [--hci hci0] [--date YYYY-MM-DD]
"""

import argparse
import datetime
import struct
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def _checksum(frame_bytes):
    s = 0
    for b in frame_bytes:
        s = (s + b) & 0xFF
    return ((s * 0x56) + 0x5A) & 0xFF


def _build_cmd(cmd, payload=()):
    payload = list(payload)
    n = len(payload)
    frame = [cmd, n & 0xFF, (n >> 8) & 0xFF] + payload
    frame.append(_checksum(frame))
    return frame


def _date_payload(d: datetime.date):
    """Historical query prefix: [0x01, year_lo, year_hi, month, day]."""
    return [0x01, d.year & 0xFF, (d.year >> 8) & 0xFF, d.month, d.day]


# Snapshot commands (validated from proxy log)
CMD_GET_CURRENT_STEP = _build_cmd(0x20, [0x00])   # 20 01 00 00 70
CMD_GET_HART         = _build_cmd(0x21, [0x00])   # 21 01 00 00 c6


# ---------------------------------------------------------------------------
# Response decoders
# ---------------------------------------------------------------------------

def _decode_steps_snapshot(data):
    """a0 0d 00 00 [steps u32 LE] [cals u32 LE] [dist u32 LE] [chk]"""
    if len(data) < 14 or data[0] != 0xa0 or data[3] != 0x00:
        return None
    steps = struct.unpack_from("<I", bytes(data), 4)[0]
    cals  = struct.unpack_from("<I", bytes(data), 8)[0]
    dist  = struct.unpack_from("<I", bytes(data), 12)[0]
    return {"steps": steps, "calories_kcal": cals, "distance_m": dist}


def _decode_hart_snapshot(data):
    """a1 05 00 00 [HR] [diastolic] [systolic] [SpO2] [chk]"""
    if len(data) < 9 or data[0] != 0xa1 or data[3] != 0x00:
        return None
    return {
        "hr_bpm":              data[4],
        "bp_diastolic_mmhg":  data[5],
        "bp_systolic_mmhg":   data[6],
        "spo2_pct":            data[7],
    }


def _decode_hart_history(data, date: datetime.date):
    """
    a1 [len_lo] [len_hi] 01 [year_lo year_hi mm dd] 0f [records...] [chk]
    Records start at byte 9; each is 4 bytes: [HR diastolic systolic SpO2].
    One record per minute; all-zero = no measurement.
    """
    if len(data) < 10 or data[0] != 0xa1 or data[3] != 0x01:
        return None
    records_data = data[9:-1]  # strip header (9 bytes) and checksum
    out = []
    for i in range(0, len(records_data) - 3, 4):
        hr, dia, sys_, spo2 = records_data[i], records_data[i+1], records_data[i+2], records_data[i+3]
        if hr == 0 and dia == 0 and sys_ == 0 and spo2 == 0:
            continue
        minute = i // 4
        ts = datetime.datetime(date.year, date.month, date.day) + datetime.timedelta(minutes=minute)
        out.append({
            "time":               ts.strftime("%H:%M"),
            "hr_bpm":             hr,
            "bp_diastolic_mmhg":  dia,
            "bp_systolic_mmhg":   sys_,
            "spo2_pct":           spo2,
        })
    return out


# ---------------------------------------------------------------------------
# BlueZ helpers
# ---------------------------------------------------------------------------

BLUEZ_SVC    = "org.bluez"
PROPS_IFACE  = "org.freedesktop.DBus.Properties"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE   = "org.bluez.GattCharacteristic1"
OM_IFACE     = "org.freedesktop.DBus.ObjectManager"

FF01_UUID_PREFIX = "0000ff01"
FF02_UUID_PREFIX = "0000ff02"


def _find_char(bus, dev_path, uuid_prefix):
    mgr = dbus.Interface(bus.get_object(BLUEZ_SVC, "/"), OM_IFACE)
    for path, ifaces in mgr.GetManagedObjects().items():
        if GATT_IFACE not in ifaces:
            continue
        if dev_path not in str(path):
            continue
        if uuid_prefix in str(ifaces[GATT_IFACE].get("UUID", "")):
            return str(path)
    return None


def _ensure_connected(bus, dev_path):
    dev   = bus.get_object(BLUEZ_SVC, dev_path)
    props = dbus.Interface(dev, PROPS_IFACE)
    if props.Get(DEVICE_IFACE, "Connected"):
        print("[health] already connected", file=sys.stderr)
        return
    print("[health] connecting...", file=sys.stderr)
    dbus.Interface(dev, DEVICE_IFACE).Connect()
    for _ in range(30):
        time.sleep(0.5)
        if props.Get(DEVICE_IFACE, "Connected"):
            print("[health] connected", file=sys.stderr)
            return
    raise RuntimeError("Could not connect to device")


# ---------------------------------------------------------------------------
# Main read function
# ---------------------------------------------------------------------------

def read_health(mac, hci="hci0", date: datetime.date = None):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.upper().replace(":", "_")
    dev_path = f"/org/bluez/{hci}/dev_{mac_path}"

    _ensure_connected(bus, dev_path)

    print("[health] waiting for GATT services...", file=sys.stderr)
    ff01_path = ff02_path = None
    for _ in range(20):
        ff01_path = _find_char(bus, dev_path, FF01_UUID_PREFIX)
        ff02_path = _find_char(bus, dev_path, FF02_UUID_PREFIX)
        if ff01_path and ff02_path:
            break
        time.sleep(0.5)

    if not ff01_path or not ff02_path:
        raise RuntimeError("FF01/FF02 characteristics not found")

    print(f"[health] FF01={ff01_path}", file=sys.stderr)
    print(f"[health] FF02={ff02_path}", file=sys.stderr)

    ff01 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
    ff02 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)

    result = {}
    loop   = GLib.MainLoop()

    # Accumulation buffer for multi-chunk BLE notifications
    buf          = []
    expected_len = [None]

    # State machine: step → hart → (hart_hist if date) → done
    state = {"next": "step"}

    def _write(cmd):
        h = " ".join(f"{b:02x}" for b in cmd)
        print(f"[health] → FF02: {h}", file=sys.stderr)
        ff02.WriteValue(dbus.Array([dbus.Byte(b) for b in cmd], signature="y"), {})

    def _send(cmd):
        """Schedule a write on the next GLib idle cycle."""
        def _do():
            _write(cmd)
            return False
        GLib.idle_add(_do)

    def _process_frame(frame):
        buf.clear()
        expected_len[0] = None
        h = " ".join(f"{b:02x}" for b in frame)
        print(f"[health] ← FF01: {h}", file=sys.stderr)

        if state["next"] == "step":
            snap = _decode_steps_snapshot(frame)
            if snap:
                result["snapshot_steps"] = snap
                state["next"] = "hart"
                _send(CMD_GET_HART)

        elif state["next"] == "hart":
            snap = _decode_hart_snapshot(frame)
            if snap:
                result["snapshot_hart"] = snap
                if date:
                    state["next"] = "hart_hist"
                    _send(_build_cmd(0x21, _date_payload(date)))
                else:
                    loop.quit()

        elif state["next"] == "hart_hist":
            hist = _decode_hart_history(frame, date)
            if hist is not None:
                result["history_hart"] = hist
                loop.quit()

    def on_ff01_changed(iface, changed, _invalidated, path=None):
        if "Value" not in changed:
            return
        buf.extend(list(changed["Value"]))

        # Determine total expected frame length from the header
        if expected_len[0] is None and len(buf) >= 3:
            payload_len     = buf[1] | (buf[2] << 8)
            expected_len[0] = 3 + payload_len + 1  # header + payload + checksum

        if expected_len[0] is not None and len(buf) >= expected_len[0]:
            _process_frame(buf[:expected_len[0]])

    bus.add_signal_receiver(
        on_ff01_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        path=ff01_path,
        path_keyword="path",
    )

    timeout_ms = 30_000 if date else 10_000

    def start():
        ff01.StartNotify()
        time.sleep(0.3)
        _write(CMD_GET_CURRENT_STEP)
        GLib.timeout_add(timeout_ms, loop.quit)

    GLib.timeout_add(200, start)
    loop.run()

    try:
        ff01.StopNotify()
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Read health data from C60-A82C bracelet")
    parser.add_argument("--mac",  default="A4:C1:38:9A:A8:2C", help="Device MAC address")
    parser.add_argument("--hci",  default="hci0",               help="HCI adapter (default: hci0)")
    parser.add_argument("--date", metavar="YYYY-MM-DD",         help="Fetch historical HR/BP/SpO2 for this date")
    args = parser.parse_args()

    date = None
    if args.date:
        try:
            date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date '{args.date}', expected YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)

    try:
        data = read_health(args.mac, args.hci, date)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not data:
        print("No data received (timeout)", file=sys.stderr)
        sys.exit(1)

    if "snapshot_steps" in data:
        s = data["snapshot_steps"]
        print(f"Steps:    {s['steps']}")
        print(f"Calories: {s['calories_kcal']} kcal")
        print(f"Distance: {s['distance_m']} m")

    if "snapshot_hart" in data:
        h = data["snapshot_hart"]
        print(f"HR:       {h['hr_bpm']} bpm")
        print(f"BP:       {h['bp_systolic_mmhg']}/{h['bp_diastolic_mmhg']} mmHg")
        print(f"SpO2:     {h['spo2_pct']}%")

    if "history_hart" in data:
        records = data["history_hart"]
        label   = args.date if args.date else str(date)
        print(f"\nHistorical HR/BP/SpO2 — {label}  ({len(records)} measurements with data)")
        print(f"{'Time':>5}  {'HR':>6}  {'BP':>9}  {'SpO2':>4}")
        print(f"{'-----':>5}  {'------':>6}  {'---------':>9}  {'----':>4}")
        for r in records:
            bp = f"{r['bp_systolic_mmhg']}/{r['bp_diastolic_mmhg']}"
            print(f"{r['time']:>5}  {r['hr_bpm']:>4} bpm  {bp:>9}  {r['spo2_pct']:>3}%")


if __name__ == "__main__":
    main()