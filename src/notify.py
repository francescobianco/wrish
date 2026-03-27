#!/usr/bin/env python3
"""Send a notification to a C60-A82C bracelet via BlueZ D-Bus."""

import argparse
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


def find_char_path(bus, dev_path, uuid_prefix):
    """Find GATT characteristic path by UUID prefix using ObjectManager."""
    mgr = dbus.Interface(
        bus.get_object("org.bluez", "/"),
        "org.freedesktop.DBus.ObjectManager"
    )
    for path, ifaces in mgr.GetManagedObjects().items():
        if "org.bluez.GattCharacteristic1" not in ifaces:
            continue
        if dev_path not in str(path):
            continue
        uuid = str(ifaces["org.bluez.GattCharacteristic1"].get("UUID", ""))
        if uuid_prefix in uuid:
            return str(path)
    return None


def send_notification(mac, app_name, title, body):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.replace(":", "_")
    dev_path = f"/org/bluez/hci0/dev_{mac_path}"

    dev = bus.get_object("org.bluez", dev_path)
    dev_props = dbus.Interface(dev, "org.freedesktop.DBus.Properties")

    if not dev_props.Get("org.bluez.Device1", "Connected"):
        print(f"[notify] connecting to {mac}...", file=sys.stderr)
        dbus.Interface(dev, "org.bluez.Device1").Connect()
        for _ in range(20):
            time.sleep(0.5)
            if dev_props.Get("org.bluez.Device1", "Connected"):
                break
        else:
            print(f"[notify] ERROR: could not connect to {mac}", file=sys.stderr)
            return False

    ff01_path = find_char_path(bus, dev_path, "0000ff01")
    ff02_path = find_char_path(bus, dev_path, "0000ff02")

    if not ff01_path or not ff02_path:
        print("[notify] ERROR: FF01/FF02 characteristics not found", file=sys.stderr)
        return False

    ff01_iface = dbus.Interface(bus.get_object("org.bluez", ff01_path), "org.bluez.GattCharacteristic1")
    ff02_iface = dbus.Interface(bus.get_object("org.bluez", ff02_path), "org.bluez.GattCharacteristic1")

    app_type = APP_TYPES.get(app_name.lower(), 7)
    frames = [
        frame_msg_type(app_type),
        frame_msg2(1, title, 32),
        frame_msg2(2, body, 128),
        END_MESSAGE,
    ]

    acks = {}
    loop = GLib.MainLoop()
    ff01_key = ff01_path.split("/")[-1]

    def on_props_changed(iface, changed, invalidated, path=None):
        if "Value" not in changed or ff01_key not in str(path or ""):
            return
        val = list(changed["Value"])
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
            for i in range(0, len(frame), 20):
                chunk = frame[i:i + 20]
                ff02_iface.WriteValue(
                    dbus.Array([dbus.Byte(b) for b in chunk], signature="y"), {}
                )
                time.sleep(0.2)

            deadline = time.time() + 8
            while stage not in acks and time.time() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.05)

            status = "OK" if stage in acks else "no ACK"
            print(f"[notify] stage {stage} {status}", file=sys.stderr)

        ff01_iface.StopNotify()
        loop.quit()

    GLib.timeout_add(200, run)
    loop.run()
    return len(acks) == 4


def main():
    parser = argparse.ArgumentParser(description="Send notification to C60-A82C bracelet")
    parser.add_argument("--mac", required=True, help="Device MAC address")
    parser.add_argument("--app", default="whatsapp", help="App name")
    parser.add_argument("--title", default="", help="Notification title")
    parser.add_argument("--body", default="", help="Notification body")
    args = parser.parse_args()

    ok = send_notification(args.mac, args.app, args.title, args.body)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
