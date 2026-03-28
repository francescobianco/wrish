#!/usr/bin/env python3
"""Simulate an incoming call on the C60-A82C bracelet via BlueZ D-Bus.

Protocol (from CmdHelper.smali analysis):
  1. Session init (same as notify.py — optional with --no-init):
       CMD_GET_DEVICE_STATE → CMD_SET_DEVICE_STATE → CMD_SET_TIME → CMD_SET_NOTICE
  2. CMD_SET_PHONE_TYPE  47 01 00 01 e0               ACK: c7 01 00 00 ...
  3. Notification stages (app_type = 0x00 = phone call icon):
       stage 0  setMessageType  0a 02 00 00 00 [chk]   ACK: 8a ... 00 ...
       stage 1  caller name     0a [len] 00 01 [utf8]  ACK: 8a ... 01 ...
       stage 2  body (empty)    0a 01 00 02 [chk]      ACK: 8a ... 02 ...
       stage 3  END_MESSAGE     0a 01 00 03 0e          ACK: 8a ... 03 ...

Call control frames (phone → bracelet, same 20-byte format as FIND_DEVICE):
  Answer: 10 08 00 01 00 00 00 00 00 00 00 c0 00 00 00 00 00 00 00 00
  End:    10 08 00 02 00 00 00 00 00 00 00 16 00 00 00 00 00 00 00 00

Usage:
  python3 call.py [--caller "John Doe"] [--number "+39123456789"] [--mac ...] [--hci hci0] [--no-init]
"""

import argparse
import datetime
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

BLUEZ_SVC    = "org.bluez"
PROPS_IFACE  = "org.freedesktop.DBus.Properties"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE   = "org.bluez.GattCharacteristic1"
OM_IFACE     = "org.freedesktop.DBus.ObjectManager"

FF01_UUID_PREFIX = "0000ff01"
FF02_UUID_PREFIX = "0000ff02"

STAGE_NAMES = ["setMessageType", "callerName", "body", "END_MESSAGE"]

# app_type = 0x00 = phone call (no specific app icon — generic call UI)
APP_TYPE_CALL = 0x00

CMD_GET_DEVICE_STATE = [0x02, 0x00, 0x00, 0x06]
CMD_SET_NOTICE_ALL   = [0x09, 0x04, 0x00, 0xff, 0xff, 0xff, 0xff, 0x60]
CMD_SET_PHONE_TYPE   = [0x47, 0x01, 0x00, 0x01, 0xe0]

END_MESSAGE = [0x0A, 0x01, 0x00, 0x03, 0x0E]


# ─── Frame builders ────────────────────────────────────────────────────────────

def checksum(bs):
    s = 0
    for b in bs:
        s = (s + b) & 0xFF
    return ((s * 0x56) + 0x5A) & 0xFF


def frame_set_device_state(state_payload):
    payload = list(state_payload)
    if len(payload) >= 9:
        payload[8] = 0x01   # notifications enabled
    if len(payload) >= 15:
        payload[14] = 0x02  # session active
    bs = [0x02, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return bs + [checksum(bs)]


def frame_set_time():
    now = datetime.datetime.now()
    year = now.year
    payload = [
        year & 0xFF, (year >> 8) & 0xFF,
        now.month, now.day,
        now.hour, now.minute, now.second,
        0x00,
    ]
    bs = [0x04, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return bs + [checksum(bs)]


def frame_msg_type(app_type):
    bs = [0x0A, 0x02, 0x00, 0x00, app_type]
    return bs + [checksum(bs)]


def frame_msg2(kind, text, max_len):
    tb = list(text.encode("utf-8")[:max_len])
    plen = 1 + len(tb)
    bs = [0x0A, plen & 0xFF, (plen >> 8) & 0xFF, kind] + tb
    return bs + [checksum(bs)]


# ─── BlueZ helpers ─────────────────────────────────────────────────────────────

def find_char(bus, dev_path, uuid_prefix):
    mgr = dbus.Interface(bus.get_object(BLUEZ_SVC, "/"), OM_IFACE)
    for path, ifaces in mgr.GetManagedObjects().items():
        if GATT_IFACE not in ifaces:
            continue
        if dev_path not in str(path):
            continue
        uuid = str(ifaces[GATT_IFACE].get("UUID", ""))
        if uuid_prefix in uuid:
            return str(path)
    return None


def ensure_connected(bus, dev_path, mac):
    dev = bus.get_object(BLUEZ_SVC, dev_path)
    props = dbus.Interface(dev, PROPS_IFACE)
    if props.Get(DEVICE_IFACE, "Connected"):
        print("[call] already connected", file=sys.stderr)
        return
    print(f"[call] connecting to {mac}...", file=sys.stderr)
    dbus.Interface(dev, DEVICE_IFACE).Connect()
    for _ in range(30):
        time.sleep(0.5)
        if props.Get(DEVICE_IFACE, "Connected"):
            print("[call] connected", file=sys.stderr)
            return
    raise RuntimeError("Could not connect to device")


def write_value(ff02, frame):
    for i in range(0, len(frame), 20):
        chunk = frame[i:i + 20]
        ff02.WriteValue(
            dbus.Array([dbus.Byte(b) for b in chunk], signature="y"), {}
        )
        if i + 20 < len(frame):
            time.sleep(0.1)


# ─── Main ──────────────────────────────────────────────────────────────────────

def simulate_call(mac, caller, number, hci="hci0", do_init=True):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.upper().replace(":", "_")
    dev_path = f"/org/bluez/{hci}/dev_{mac_path}"

    ensure_connected(bus, dev_path, mac)

    print("[call] waiting for GATT services...", file=sys.stderr)
    ff01_path = ff02_path = None
    for _ in range(20):
        ff01_path = find_char(bus, dev_path, FF01_UUID_PREFIX)
        ff02_path = find_char(bus, dev_path, FF02_UUID_PREFIX)
        if ff01_path and ff02_path:
            break
        time.sleep(0.5)

    if not ff01_path or not ff02_path:
        raise RuntimeError("FF01/FF02 characteristics not found")

    print(f"[call] FF01={ff01_path}", file=sys.stderr)
    print(f"[call] FF02={ff02_path}", file=sys.stderr)

    ff01 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
    ff02 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)

    # Notification stages: app_type=0x00 (phone call), caller name as title,
    # number (or empty) as body
    title_text = caller or number or "Unknown"
    body_text  = number if (number and caller) else ""
    notif_frames = [
        frame_msg_type(APP_TYPE_CALL),
        frame_msg2(1, title_text, 32),
        frame_msg2(2, body_text, 128),
        END_MESSAGE,
    ]

    # State machine phases (mirrors notify.py):
    #   "get_state"    → waiting for CMD_GET_DEVICE_STATE response
    #   "set_state"    → waiting for CMD_SET_DEVICE_STATE ACK
    #   "set_time"     → waiting for CMD_SET_TIME ACK
    #   "set_notice"   → waiting for CMD_SET_NOTICE ACK
    #   "set_phone"    → waiting for CMD_SET_PHONE_TYPE ACK (0xC7)
    #   "notify"       → notification stages 0..3
    state = {
        "phase": "get_state" if do_init else "set_phone",
        "notify_stage": 0,
        "acks": 0,
        "device_state_payload": None,
    }
    loop = GLib.MainLoop()

    def hex_frame(frame):
        s = " ".join(f"{b:02x}" for b in frame[:16])
        return s + ("..." if len(frame) > 16 else "")

    def send_notify_stage(stage):
        frame = notif_frames[stage]
        print(f"[call] sending stage {stage} ({STAGE_NAMES[stage]}): {hex_frame(frame)}", file=sys.stderr)
        write_value(ff02, frame)

    def on_ff01_changed(iface, changed, _invalidated, path=None):
        if "Value" not in changed:
            return
        data = list(changed["Value"])
        first = data[0] if data else 0
        length = (data[2] << 8 | data[1]) if len(data) >= 3 else 0
        hex_str = " ".join(f"{b:02x}" for b in data)
        print(f"[call] FF01: {hex_str}", file=sys.stderr)

        phase = state["phase"]

        # ── Init: CMD_GET_DEVICE_STATE response ──────────────────────────────
        if phase == "get_state" and first == 0x82 and length > 1:
            payload = data[3:-1]
            print(f"[call] got device state ({len(payload)} bytes)", file=sys.stderr)
            state["device_state_payload"] = payload
            state["phase"] = "set_state"
            frame = frame_set_device_state(payload)
            print(f"[call] sending CMD_SET_DEVICE_STATE: {hex_frame(frame)}", file=sys.stderr)
            GLib.timeout_add(200, lambda: write_value(ff02, frame) or False)
            return

        # ── Init: CMD_SET_DEVICE_STATE ACK ───────────────────────────────────
        if phase == "set_state" and first == 0x82 and length == 1:
            print("[call] CMD_SET_DEVICE_STATE ACK", file=sys.stderr)
            state["phase"] = "set_time"
            frame = frame_set_time()
            print(f"[call] sending CMD_SET_TIME: {hex_frame(frame)}", file=sys.stderr)
            GLib.timeout_add(200, lambda: write_value(ff02, frame) or False)
            return

        # ── Init: CMD_SET_TIME ACK ───────────────────────────────────────────
        if phase == "set_time" and first == 0x84 and length == 1:
            print("[call] CMD_SET_TIME ACK", file=sys.stderr)
            state["phase"] = "set_notice"
            print(f"[call] sending CMD_SET_NOTICE (all enabled): {' '.join(f'{b:02x}' for b in CMD_SET_NOTICE_ALL)}", file=sys.stderr)
            GLib.timeout_add(200, lambda: write_value(ff02, CMD_SET_NOTICE_ALL) or False)
            return

        # ── Init: CMD_SET_NOTICE ACK ─────────────────────────────────────────
        if phase == "set_notice" and first == 0x89 and length == 1:
            print("[call] CMD_SET_NOTICE ACK — init complete", file=sys.stderr)
            state["phase"] = "set_phone"
            print(f"[call] sending CMD_SET_PHONE_TYPE: {' '.join(f'{b:02x}' for b in CMD_SET_PHONE_TYPE)}", file=sys.stderr)
            GLib.timeout_add(300, lambda: write_value(ff02, CMD_SET_PHONE_TYPE) or False)
            return

        # ── CMD_SET_PHONE_TYPE ACK (0x80|0x47 = 0xC7) ───────────────────────
        if phase == "set_phone" and first == 0xC7:
            print("[call] CMD_SET_PHONE_TYPE ACK — sending call notification", file=sys.stderr)
            state["phase"] = "notify"
            GLib.timeout_add(300, lambda: send_notify_stage(0) or False)
            return

        # ── Notification ACKs ────────────────────────────────────────────────
        if phase == "notify" and first == 0x8A and len(data) >= 4:
            ack_stage = int(data[3])
            if ack_stage != state["notify_stage"]:
                return
            print(f"[call] ACK stage {ack_stage} ({STAGE_NAMES[ack_stage]})", file=sys.stderr)
            state["acks"] += 1
            state["notify_stage"] += 1
            if state["notify_stage"] < len(notif_frames):
                ns = state["notify_stage"]
                GLib.timeout_add(200, lambda: send_notify_stage(ns) or False)
            else:
                GLib.timeout_add(200, loop.quit)

    bus.add_signal_receiver(
        on_ff01_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        path=ff01_path,
        path_keyword="path",
    )

    def run():
        ff01.StartNotify()
        time.sleep(0.3)
        if do_init:
            print(f"[call] sending CMD_GET_DEVICE_STATE: {' '.join(f'{b:02x}' for b in CMD_GET_DEVICE_STATE)}", file=sys.stderr)
            write_value(ff02, CMD_GET_DEVICE_STATE)
        else:
            print(f"[call] sending CMD_SET_PHONE_TYPE: {' '.join(f'{b:02x}' for b in CMD_SET_PHONE_TYPE)}", file=sys.stderr)
            write_value(ff02, CMD_SET_PHONE_TYPE)
        GLib.timeout_add(30000, loop.quit)

    GLib.timeout_add(200, run)
    loop.run()

    try:
        ff01.StopNotify()
    except Exception:
        pass

    acks = state["acks"]
    print(f"[call] done ({acks}/4 ACKs)", file=sys.stderr)
    if acks < 4:
        raise RuntimeError(f"Incomplete delivery: only {acks}/4 ACKs received")

    return True


def main():
    parser = argparse.ArgumentParser(description="Simulate incoming call on C60-A82C bracelet")
    parser.add_argument("--mac",    default="A4:C1:38:9A:A8:2C", help="Device MAC address")
    parser.add_argument("--hci",    default="hci0",              help="HCI adapter (default: hci0)")
    parser.add_argument("--caller", default="",                  help="Caller name (max 32 chars)")
    parser.add_argument("--number", default="",                  help="Phone number / body text (max 128 chars)")
    parser.add_argument("--no-init", action="store_true",
                        help="Skip session init (GET/SET device state + time sync)")
    args = parser.parse_args()

    if not args.caller and not args.number:
        args.caller = "Unknown"

    try:
        simulate_call(args.mac, args.caller, args.number,
                      hci=args.hci, do_init=not args.no_init)
        print("Call notification sent")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
