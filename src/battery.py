#!/usr/bin/env python3
"""Read battery level from C60-A82C bracelet via BlueZ D-Bus.

Query:   CMD_GET_CURRENT_POWER  FF02 ← 27 00 00 74
Response:                       FF01 → a7 01 00 [percent%] [chk]

Usage:
  python3 battery.py [--mac AA:BB:CC:DD:EE:FF] [--hci hci0]
"""

import argparse
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

CMD_GET_CURRENT_POWER = [0x27, 0x00, 0x00, 0x74]

BLUEZ_SVC    = "org.bluez"
PROPS_IFACE  = "org.freedesktop.DBus.Properties"
DEVICE_IFACE = "org.bluez.Device1"
GATT_IFACE   = "org.bluez.GattCharacteristic1"
OM_IFACE     = "org.freedesktop.DBus.ObjectManager"

FF01_UUID_PREFIX = "0000ff01"
FF02_UUID_PREFIX = "0000ff02"


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


def ensure_connected(bus, dev_path):
    dev = bus.get_object(BLUEZ_SVC, dev_path)
    props = dbus.Interface(dev, PROPS_IFACE)
    if props.Get(DEVICE_IFACE, "Connected"):
        print(f"[battery] already connected", file=sys.stderr)
        return
    print(f"[battery] connecting...", file=sys.stderr)
    dbus.Interface(dev, DEVICE_IFACE).Connect()
    for _ in range(30):
        time.sleep(0.5)
        if props.Get(DEVICE_IFACE, "Connected"):
            print(f"[battery] connected", file=sys.stderr)
            return
    raise RuntimeError("Could not connect to device")


def read_battery(mac, hci="hci0"):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.upper().replace(":", "_")
    dev_path = f"/org/bluez/{hci}/dev_{mac_path}"

    ensure_connected(bus, dev_path)

    # Wait for GATT service discovery
    print("[battery] waiting for GATT services...", file=sys.stderr)
    ff01_path = ff02_path = None
    for _ in range(20):
        ff01_path = find_char(bus, dev_path, FF01_UUID_PREFIX)
        ff02_path = find_char(bus, dev_path, FF02_UUID_PREFIX)
        if ff01_path and ff02_path:
            break
        time.sleep(0.5)

    if not ff01_path or not ff02_path:
        raise RuntimeError("FF01/FF02 characteristics not found")

    print(f"[battery] FF01={ff01_path}", file=sys.stderr)
    print(f"[battery] FF02={ff02_path}", file=sys.stderr)

    ff01 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
    ff02 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)

    result = {}
    loop = GLib.MainLoop()

    def on_ff01_changed(iface, changed, _invalidated, path=None):
        if "Value" not in changed:
            return
        data = list(changed["Value"])
        hex_str = " ".join(f"{b:02x}" for b in data)
        print(f"[battery] FF01 notification: {hex_str}", file=sys.stderr)
        # Battery response: a7 01 00 [percent] [chk]
        if len(data) >= 4 and data[0] == 0xa7:
            result["percent"] = int(data[3])
            loop.quit()

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
        print(f"[battery] sending CMD_GET_CURRENT_POWER: 27 00 00 74", file=sys.stderr)
        ff02.WriteValue(
            dbus.Array([dbus.Byte(b) for b in CMD_GET_CURRENT_POWER], signature="y"),
            {},
        )
        # Timeout after 8s
        GLib.timeout_add(8000, loop.quit)

    GLib.timeout_add(200, run)
    loop.run()

    try:
        ff01.StopNotify()
    except Exception:
        pass

    if "percent" not in result:
        raise RuntimeError("No battery response received (timeout)")

    return result["percent"]


def main():
    parser = argparse.ArgumentParser(description="Read battery level from C60-A82C")
    parser.add_argument("--mac", default="A4:C1:38:9A:A8:2C", help="Device MAC address")
    parser.add_argument("--hci", default="hci0", help="HCI adapter (default: hci0)")
    args = parser.parse_args()

    try:
        pct = read_battery(args.mac, args.hci)
        print(f"Battery: {pct}%")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
