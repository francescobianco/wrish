#!/usr/bin/env python3
"""Ring / find the C60-A82C bracelet via BlueZ D-Bus.

Command: fixed 20-byte frame (header 0x10, not standard cmd/len/payload/chk format)
  FF02 ← 10 08 00 00 00 00 00 01 00 00 00 c0 00 00 00 00 00 00 00 00
Response:
  FF01 → 90 01 00 00 10

Validated via BLE proxy on 2026-03-28.

Usage:
  python3 find.py [--mac AA:BB:CC:DD:EE:FF] [--hci hci0]
"""

import argparse
import sys
import time
import dbus
import dbus.mainloop.glib
from gi.repository import GLib

FIND_DEVICE_CMD = [
    0x10, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
    0x00, 0x00, 0x00, 0xc0, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]

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
        print("[find] already connected", file=sys.stderr)
        return
    print("[find] connecting...", file=sys.stderr)
    dbus.Interface(dev, DEVICE_IFACE).Connect()
    for _ in range(30):
        time.sleep(0.5)
        if props.Get(DEVICE_IFACE, "Connected"):
            print("[find] connected", file=sys.stderr)
            return
    raise RuntimeError("Could not connect to device")


def find_device(mac, hci="hci0"):
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    mac_path = mac.upper().replace(":", "_")
    dev_path = f"/org/bluez/{hci}/dev_{mac_path}"

    ensure_connected(bus, dev_path)

    print("[find] waiting for GATT services...", file=sys.stderr)
    ff01_path = ff02_path = None
    for _ in range(20):
        ff01_path = find_char(bus, dev_path, FF01_UUID_PREFIX)
        ff02_path = find_char(bus, dev_path, FF02_UUID_PREFIX)
        if ff01_path and ff02_path:
            break
        time.sleep(0.5)

    if not ff01_path or not ff02_path:
        raise RuntimeError("FF01/FF02 characteristics not found")

    print(f"[find] FF01={ff01_path}", file=sys.stderr)
    print(f"[find] FF02={ff02_path}", file=sys.stderr)

    ff01 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff01_path), GATT_IFACE)
    ff02 = dbus.Interface(bus.get_object(BLUEZ_SVC, ff02_path), GATT_IFACE)

    result = {}
    loop = GLib.MainLoop()

    def on_ff01_changed(iface, changed, _invalidated, path=None):
        if "Value" not in changed:
            return
        data = list(changed["Value"])
        hex_str = " ".join(f"{b:02x}" for b in data)
        print(f"[find] FF01 notification: {hex_str}", file=sys.stderr)
        # Expected response: 90 01 00 00 10
        if len(data) >= 1 and data[0] == 0x90:
            result["ack"] = True
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
        cmd_hex = " ".join(f"{b:02x}" for b in FIND_DEVICE_CMD)
        print(f"[find] sending: {cmd_hex}", file=sys.stderr)
        ff02.WriteValue(
            dbus.Array([dbus.Byte(b) for b in FIND_DEVICE_CMD], signature="y"),
            {},
        )
        GLib.timeout_add(8000, loop.quit)

    GLib.timeout_add(200, run)
    loop.run()

    try:
        ff01.StopNotify()
    except Exception:
        pass

    if not result.get("ack"):
        raise RuntimeError("No response from bracelet (timeout)")

    return True


def main():
    parser = argparse.ArgumentParser(description="Ring/find the C60-A82C bracelet")
    parser.add_argument("--mac", default="A4:C1:38:9A:A8:2C", help="Device MAC address")
    parser.add_argument("--hci", default="hci0", help="HCI adapter (default: hci0)")
    args = parser.parse_args()

    try:
        find_device(args.mac, args.hci)
        print("Bracelet found (vibrating)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
