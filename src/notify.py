#!/usr/bin/env python3
"""Send a notification to the C60-A82C bracelet via BlueZ D-Bus."""

import argparse
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

DEV_MAC    = "A4:C1:38:9A:A8:2C"
DEV_PATH   = "/org/bluez/hci0/dev_A4_C1_38_9A_A8_2C"
FF01_PATH  = DEV_PATH + "/service000c/char000d"
FF02_PATH  = DEV_PATH + "/service000c/char0010"

APP_TYPES = {
    "wechat": 2, "qq": 3, "facebook": 4, "skype": 5,
    "twitter": 6, "whatsapp": 7, "line": 8, "linkedin": 9,
    "instagram": 10, "messenger": 12, "vk": 13, "viber": 14,
    "telegram": 16, "kakaotalk": 18, "douyin": 32, "kuaishou": 33,
    "douyin_lite": 34, "maimai": 52, "pinduoduo": 53,
    "work_wechat": 54, "tantan": 56, "taobao": 57,
}


def checksum(bs):
    s = sum(bs) & 0xFF
    return ((s * 0x56) + 0x5A) & 0xFF


def frame_msg_type(app_type):
    bs = [0x0A, 0x02, 0x00, 0x00, app_type]
    return bs + [checksum(bs)]


def frame_msg2(kind, text, max_len):
    tb = list(text[:max_len].encode("utf-8"))
    plen = 1 + len(tb)
    bs = [0x0A, plen & 0xFF, (plen >> 8) & 0xFF, kind] + tb
    return bs + [checksum(bs)]


END_MESSAGE = [0x0A, 0x01, 0x00, 0x03, 0x0E]


def send_notification(app_name, title, body, verbose=False):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    dev = bus.get_object("org.bluez", DEV_PATH)
    dev_props = dbus.Interface(dev, "org.freedesktop.DBus.Properties")

    connected = dev_props.Get("org.bluez.Device1", "Connected")
    if not connected:
        print("[notify] connecting...", file=sys.stderr)
        dbus.Interface(dev, "org.bluez.Device1").Connect()
        for _ in range(20):
            time.sleep(0.5)
            if dev_props.Get("org.bluez.Device1", "Connected"):
                break
        else:
            print("[notify] ERROR: could not connect", file=sys.stderr)
            return False

    ff01 = bus.get_object("org.bluez", FF01_PATH)
    ff02 = bus.get_object("org.bluez", FF02_PATH)
    ff01_iface = dbus.Interface(ff01, "org.bluez.GattCharacteristic1")
    ff02_iface = dbus.Interface(ff02, "org.bluez.GattCharacteristic1")

    app_type = APP_TYPES.get(app_name.lower(), 7)
    frames = [
        frame_msg_type(app_type),
        frame_msg2(1, title, 32),
        frame_msg2(2, body, 128),
        END_MESSAGE,
    ]

    acks = {}
    loop = GLib.MainLoop()

    def on_props_changed(iface, changed, invalidated, path=None):
        if "Value" not in changed or "char000d" not in str(path or ""):
            return
        val = list(changed["Value"])
        if verbose:
            print(f"  ACK: {[hex(b) for b in val]}", file=sys.stderr)
        if len(val) >= 4 and val[0] == 0x8A:
            acks[val[3]] = val

    bus.add_signal_receiver(
        on_props_changed,
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path_keyword="path",
    )

    def run():
        ff01_iface.StartNotify()
        time.sleep(0.3)

        for stage, frame in enumerate(frames):
            if verbose:
                print(f"  stage {stage}: {[hex(b) for b in frame]}", file=sys.stderr)
            for i in range(0, len(frame), 20):
                chunk = frame[i:i+20]
                ff02_iface.WriteValue(
                    dbus.Array([dbus.Byte(b) for b in chunk], signature="y"), {}
                )
                time.sleep(0.2)

            deadline = time.time() + 8
            while stage not in acks and time.time() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.05)

            if stage in acks:
                print(f"[notify] stage {stage} OK", file=sys.stderr)
            else:
                print(f"[notify] stage {stage} no ACK", file=sys.stderr)

        ff01_iface.StopNotify()
        loop.quit()

    GLib.timeout_add(200, run)
    loop.run()
    return len(acks) == 4


def main():
    parser = argparse.ArgumentParser(description="Send notification to C60-A82C bracelet")
    parser.add_argument("--app", default="whatsapp")
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--mac", default=DEV_MAC)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    ok = send_notification(args.app, args.title, args.body, args.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
