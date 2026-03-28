#!/usr/bin/env python3
"""Send a notification to a C60-A82C bracelet via BlueZ D-Bus.

Session init (mirrors app startup from proxy log):
  1. CMD_GET_DEVICE_STATE  02 00 00 06  → read 16-byte state payload
  2. CMD_SET_DEVICE_STATE  02 10 00 [payload with byte[14]=0x02] [chk]  → ACK 82 01 00 00 5c
  3. CMD_SET_TIME          04 08 00 [year_lo year_hi mm dd hh min sec 00] [chk]  → ACK 84 01 00 00 08

Notification (4 stages, each waits for ACK before proceeding):
  stage 0  setMessageType   0a 02 00 00 [appType] [chk]       ACK: 8a ... 00 ...
  stage 1  title            0a [len] 00 01 [utf8...] [chk]    ACK: 8a ... 01 ...
  stage 2  body             0a [len] 00 02 [utf8...] [chk]    ACK: 8a ... 02 ...
  stage 3  END_MESSAGE      0a 01 00 03 0e                     ACK: 8a ... 03 ...

Usage:
  python3 notify.py --title "Hello" --body "World" [--app whatsapp] [--mac ...] [--hci hci0] [--no-init]
"""

import argparse
import datetime
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

APP_TYPES = {
    "wechat": 2, "qq": 3, "facebook": 4, "skype": 5,
    "twitter": 6, "whatsapp": 7, "line": 8, "linkedin": 9,
    "instagram": 10, "messenger": 12, "vk": 13, "viber": 14,
    "telegram": 16, "kakaotalk": 18, "douyin": 32, "kuaishou": 33,
    "douyin_lite": 34, "maimai": 52, "pinduoduo": 53,
    "work_wechat": 54, "tantan": 56, "taobao": 57,
}

STAGE_NAMES = ["setMessageType", "title", "body", "END_MESSAGE"]

BLUEZ_SVC    = "org.bluez"
PROPS_IFACE  = "org.freedesktop.DBus.Properties"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE   = "org.bluez.GattCharacteristic1"
OM_IFACE     = "org.freedesktop.DBus.ObjectManager"

FF01_UUID_PREFIX = "0000ff01"
FF02_UUID_PREFIX = "0000ff02"


# ─── Frame builders ───────────────────────────────────────────────────────────

def checksum(bs):
    s = 0
    for b in bs:
        s = (s + b) & 0xFF
    return ((s * 0x56) + 0x5A) & 0xFF


def frame_set_device_state(state_payload):
    """Build CMD_SET_DEVICE_STATE with byte[14] of payload forced to 0x02."""
    payload = list(state_payload)
    if len(payload) >= 15:
        payload[14] = 0x02
    bs = [0x02, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return bs + [checksum(bs)]


def frame_set_time():
    """Build CMD_SET_TIME from current local time."""
    now = datetime.datetime.now()
    year = now.year
    payload = [
        year & 0xFF, (year >> 8) & 0xFF,
        now.month, now.day,
        now.hour, now.minute, now.second,
        0x00,  # trailing zero (seen in proxy log)
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


END_MESSAGE = [0x0A, 0x01, 0x00, 0x03, 0x0E]

CMD_GET_DEVICE_STATE = [0x02, 0x00, 0x00, 0x06]


# ─── BlueZ helpers ────────────────────────────────────────────────────────────

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
        print("[notify] already connected", file=sys.stderr)
        return
    print(f"[notify] connecting to {mac}...", file=sys.stderr)
    dbus.Interface(dev, DEVICE_IFACE).Connect()
    for _ in range(30):
        time.sleep(0.5)
        if props.Get(DEVICE_IFACE, "Connected"):
            print("[notify] connected", file=sys.stderr)
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


# ─── Main ─────────────────────────────────────────────────────────────────────

def send_notification(mac, app_name, title, body, hci="hci0", do_init=True):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.upper().replace(":", "_")
    dev_path = f"/org/bluez/{hci}/dev_{mac_path}"

    ensure_connected(bus, dev_path, mac)

    print("[notify] waiting for GATT services...", file=sys.stderr)
    ff01_path = ff02_path = None
    for _ in range(20):
        ff01_path = find_char(bus, dev_path, FF01_UUID_PREFIX)
        ff02_path = find_char(bus, dev_path, FF02_UUID_PREFIX)
        if ff01_path and ff02_path:
            break
        time.sleep(0.5)

    if not ff01_path or not ff02_path:
        raise RuntimeError("FF01/FF02 characteristics not found")

    print(f"[notify] FF01={ff01_path}", file=sys.stderr)
    print(f"[notify] FF02={ff02_path}", file=sys.stderr)

    ff01 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
    ff02 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)

    app_type = APP_TYPES.get(app_name.lower(), 7)
    notif_frames = [
        frame_msg_type(app_type),
        frame_msg2(1, title, 32),
        frame_msg2(2, body, 128),
        END_MESSAGE,
    ]

    # State machine phases:
    #   "get_state"  → waiting for CMD_GET_DEVICE_STATE response (0x82, len>1)
    #   "set_state"  → waiting for CMD_SET_DEVICE_STATE ACK     (0x82, len=1)
    #   "set_time"   → waiting for CMD_SET_TIME ACK             (0x84, len=1)
    #   "notify"     → notification stages 0..3
    state = {
        "phase": "get_state" if do_init else "notify",
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
        print(f"[notify] sending stage {stage} ({STAGE_NAMES[stage]}): {hex_frame(frame)}", file=sys.stderr)
        write_value(ff02, frame)

    def on_ff01_changed(iface, changed, _invalidated, path=None):
        if "Value" not in changed:
            return
        data = list(changed["Value"])
        first = data[0] if data else 0
        length = (data[2] << 8 | data[1]) if len(data) >= 3 else 0
        hex_str = " ".join(f"{b:02x}" for b in data)
        print(f"[notify] FF01: {hex_str}", file=sys.stderr)

        phase = state["phase"]

        # ── Init phase: CMD_GET_DEVICE_STATE response ────────────────────────
        if phase == "get_state" and first == 0x82 and length > 1:
            # payload starts at byte[3], ends before last byte (checksum)
            payload = data[3:-1]
            print(f"[notify] got device state ({len(payload)} bytes)", file=sys.stderr)
            state["device_state_payload"] = payload
            state["phase"] = "set_state"
            frame = frame_set_device_state(payload)
            print(f"[notify] sending CMD_SET_DEVICE_STATE: {hex_frame(frame)}", file=sys.stderr)
            GLib.timeout_add(200, lambda: write_value(ff02, frame) or False)
            return

        # ── Init phase: CMD_SET_DEVICE_STATE ACK ─────────────────────────────
        if phase == "set_state" and first == 0x82 and length == 1:
            print("[notify] CMD_SET_DEVICE_STATE ACK", file=sys.stderr)
            state["phase"] = "set_time"
            frame = frame_set_time()
            print(f"[notify] sending CMD_SET_TIME: {hex_frame(frame)}", file=sys.stderr)
            GLib.timeout_add(200, lambda: write_value(ff02, frame) or False)
            return

        # ── Init phase: CMD_SET_TIME ACK ──────────────────────────────────────
        if phase == "set_time" and first == 0x84 and length == 1:
            print("[notify] CMD_SET_TIME ACK — init complete", file=sys.stderr)
            state["phase"] = "notify"
            GLib.timeout_add(300, lambda: send_notify_stage(0) or False)
            return

        # ── Notification ACKs ─────────────────────────────────────────────────
        if phase == "notify" and first == 0x8A and len(data) >= 4:
            ack_stage = int(data[3])
            if ack_stage != state["notify_stage"]:
                return
            print(f"[notify] ACK stage {ack_stage} ({STAGE_NAMES[ack_stage]})", file=sys.stderr)
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
            print(f"[notify] sending CMD_GET_DEVICE_STATE: {' '.join(f'{b:02x}' for b in CMD_GET_DEVICE_STATE)}", file=sys.stderr)
            write_value(ff02, CMD_GET_DEVICE_STATE)
        else:
            send_notify_stage(0)
        GLib.timeout_add(30000, loop.quit)

    GLib.timeout_add(200, run)
    loop.run()

    try:
        ff01.StopNotify()
    except Exception:
        pass

    acks = state["acks"]
    print(f"[notify] done ({acks}/4 ACKs)", file=sys.stderr)
    if acks < 4:
        raise RuntimeError(f"Incomplete delivery: only {acks}/4 ACKs received")

    return True


def main():
    parser = argparse.ArgumentParser(description="Send notification to C60-A82C bracelet")
    parser.add_argument("--mac", default="A4:C1:38:9A:A8:2C", help="Device MAC address")
    parser.add_argument("--hci", default="hci0", help="HCI adapter (default: hci0)")
    parser.add_argument("--app", default="whatsapp", help="App name (default: whatsapp)")
    parser.add_argument("--title", default="", help="Notification title (max 32 chars)")
    parser.add_argument("--body", default="", help="Notification body (max 128 chars)")
    parser.add_argument("--no-init", action="store_true",
                        help="Skip session init (GET/SET device state + time sync)")
    args = parser.parse_args()

    try:
        send_notification(args.mac, args.app, args.title, args.body,
                          hci=args.hci, do_init=not args.no_init)
        print("Notification sent")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
