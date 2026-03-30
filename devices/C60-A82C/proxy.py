#!/usr/bin/env python3
"""
BLE transparent proxy for C60-A82C.

Architecture:
  Phone <--BLE--> [Linux peripheral GATT server]
                          |  bridge
                  [Linux central BLE client] <--BLE--> Real bracelet

- All phone→FF02 writes are forwarded to bracelet→FF02 and logged.
- All bracelet→FF01 notifications are forwarded to phone→FF01 and logged.
- Run with --no-upstream to test advertising without a real bracelet.

Requirements:
  pip install dbus-python PyGObject
  bluetoothd must be running (systemctl start bluetooth)

Usage:
  python3 proxy.py [--bracelet-mac AA:BB:CC:DD:EE:FF] [--name C60-A82C]
                   [--hci hci0] [--no-upstream] [--log-level DEBUG]
"""

import argparse
import logging
import sys
import time
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

LOG = logging.getLogger("ble-proxy")

# UUIDs
SERVICE_UUID = "0000ff00-0000-1000-8000-00805f9b34fb"
FF01_UUID    = "0000ff01-0000-1000-8000-00805f9b34fb"  # notify  (bracelet → phone)
FF02_UUID    = "0000ff02-0000-1000-8000-00805f9b34fb"  # write   (phone → bracelet)

# D-Bus interfaces
BLUEZ_SVC       = "org.bluez"
OM_IFACE        = "org.freedesktop.DBus.ObjectManager"
PROPS_IFACE     = "org.freedesktop.DBus.Properties"
DEVICE_IFACE    = "org.bluez.Device1"
GATT_MGR_IFACE  = "org.bluez.GattManager1"
GATT_SVC_IFACE  = "org.bluez.GattService1"
GATT_CHR_IFACE  = "org.bluez.GattCharacteristic1"
ADV_MGR_IFACE   = "org.bluez.LEAdvertisingManager1"
ADV_IFACE       = "org.bluez.LEAdvertisement1"

BASE_PATH = "/com/wrish/proxy"


def hex_str(data):
    return " ".join(f"{b:02x}" for b in data)


# ─── Upstream (central: Linux → bracelet) ─────────────────────────────────────

class UpstreamClient:
    """BLE central client that connects to the real bracelet."""

    def __init__(self, bus, mac, hci="hci0"):
        self.bus = bus
        self.mac = mac
        self.hci = hci
        self.ff01_path = None
        self.ff02_path = None
        mac_path = mac.upper().replace(":", "_")
        self.dev_path = f"/org/bluez/{hci}/dev_{mac_path}"
        self._notification_cb = None

    def set_notification_callback(self, cb):
        """cb(data: bytes) called when bracelet sends FF01 notification."""
        self._notification_cb = cb

    def connect(self):
        dev = self.bus.get_object(BLUEZ_SVC, self.dev_path)
        props = dbus.Interface(dev, PROPS_IFACE)
        if not props.Get(DEVICE_IFACE, "Connected"):
            LOG.info("[upstream] connecting to %s ...", self.mac)
            dbus.Interface(dev, DEVICE_IFACE).Connect()
            for _ in range(30):
                time.sleep(0.5)
                if props.Get(DEVICE_IFACE, "Connected"):
                    break
            else:
                raise RuntimeError(f"[upstream] cannot connect to {self.mac}")
        LOG.info("[upstream] connected to %s", self.mac)

    def resolve_chars(self):
        mgr = dbus.Interface(self.bus.get_object(BLUEZ_SVC, "/"), OM_IFACE)
        for path, ifaces in mgr.GetManagedObjects().items():
            if GATT_CHR_IFACE not in ifaces:
                continue
            if self.dev_path not in str(path):
                continue
            uuid = str(ifaces[GATT_CHR_IFACE].get("UUID", ""))
            if "0000ff01" in uuid:
                self.ff01_path = str(path)
            elif "0000ff02" in uuid:
                self.ff02_path = str(path)
        if not self.ff01_path or not self.ff02_path:
            raise RuntimeError("[upstream] FF01/FF02 not found on device")
        LOG.info("[upstream] FF01=%s", self.ff01_path)
        LOG.info("[upstream] FF02=%s", self.ff02_path)

    def start_notify(self):
        ff01 = dbus.Interface(
            self.bus.get_object(BLUEZ_SVC, self.ff01_path), GATT_CHR_IFACE
        )
        ff01.StartNotify()
        self.bus.add_signal_receiver(
            self._on_ff01_changed,
            signal_name="PropertiesChanged",
            dbus_interface=PROPS_IFACE,
            path=self.ff01_path,
        )
        LOG.info("[upstream] subscribed to FF01 notifications")

    def _on_ff01_changed(self, iface, changed, _invalidated):
        if "Value" not in changed:
            return
        data = bytes(changed["Value"])
        LOG.info("[bracelet → phone]  FF01  %s", hex_str(data))
        if self._notification_cb:
            self._notification_cb(data)

    def write_ff02(self, data: bytes):
        LOG.info("[phone → bracelet]  FF02  %s", hex_str(data))
        ff02 = dbus.Interface(
            self.bus.get_object(BLUEZ_SVC, self.ff02_path), GATT_CHR_IFACE
        )
        ff02.WriteValue(
            dbus.Array([dbus.Byte(b) for b in data], signature="y"), {}
        )


# ─── Peripheral (GATT server: phone-facing) ───────────────────────────────────

class Advertisement(dbus.service.Object):

    PATH = BASE_PATH + "/advertisement0"

    def __init__(self, bus, device_name):
        self.path = self.PATH
        self._props = {
            ADV_IFACE: dbus.Dictionary({
                "Type":        "peripheral",
                "LocalName":   device_name,
                "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s"),
            }, signature="sv")
        }
        dbus.service.Object.__init__(self, bus, self.path)

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self._props.get(interface, dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(ADV_IFACE)
    def Release(self):
        LOG.info("[adv] released")


class FF01Characteristic(dbus.service.Object):
    """Notify characteristic (bracelet→phone direction)."""

    def __init__(self, bus, svc_path):
        self.path = svc_path + "/char1"
        self._value = dbus.Array([], signature="y")
        self._notifying = False
        dbus.service.Object.__init__(self, bus, self.path)

    def get_props(self):
        return {
            GATT_CHR_IFACE: dbus.Dictionary({
                "Service":   dbus.ObjectPath(BASE_PATH + "/service0"),
                "UUID":      FF01_UUID,
                "Flags":     dbus.Array(["notify"], signature="s"),
                "Value":     self._value,
                "Notifying": dbus.Boolean(self._notifying),
            }, signature="sv")
        }

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_props().get(interface, dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(GATT_CHR_IFACE)
    def StartNotify(self):
        self._notifying = True
        LOG.info("[peripheral] phone subscribed to FF01")

    @dbus.service.method(GATT_CHR_IFACE)
    def StopNotify(self):
        self._notifying = False
        LOG.info("[peripheral] phone unsubscribed from FF01")

    @dbus.service.signal(PROPS_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def push_notification(self, data: bytes):
        """Send a notification to the connected phone."""
        self._value = dbus.Array([dbus.Byte(b) for b in data], signature="y")
        self.PropertiesChanged(
            GATT_CHR_IFACE,
            dbus.Dictionary({"Value": self._value}, signature="sv"),
            dbus.Array([], signature="s"),
        )


class FF02Characteristic(dbus.service.Object):
    """Write characteristic (phone→bracelet direction)."""

    def __init__(self, bus, svc_path, upstream):
        self.path = svc_path + "/char0"
        self._upstream = upstream
        dbus.service.Object.__init__(self, bus, self.path)

    def get_props(self):
        return {
            GATT_CHR_IFACE: dbus.Dictionary({
                "Service": dbus.ObjectPath(BASE_PATH + "/service0"),
                "UUID":    FF02_UUID,
                "Flags":   dbus.Array(["write", "write-without-response"], signature="s"),
                "Value":   dbus.Array([], signature="y"),
            }, signature="sv")
        }

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_props().get(interface, dbus.Dictionary({}, signature="sv"))

    @dbus.service.method(GATT_CHR_IFACE, in_signature="aya{sv}")
    def WriteValue(self, value, options):
        data = bytes(value)
        LOG.info("[phone → proxy]     FF02  %s", hex_str(data))
        if self._upstream:
            try:
                self._upstream.write_ff02(data)
            except Exception as exc:
                LOG.warning("[proxy] upstream write failed: %s", exc)


class ProxyService(dbus.service.Object):

    PATH = BASE_PATH + "/service0"

    def __init__(self, bus, upstream):
        self.ff01 = FF01Characteristic(bus, self.PATH)
        self.ff02 = FF02Characteristic(bus, self.PATH, upstream)
        dbus.service.Object.__init__(self, bus, self.PATH)

    def get_props(self):
        return {
            GATT_SVC_IFACE: dbus.Dictionary({
                "UUID":    SERVICE_UUID,
                "Primary": dbus.Boolean(True),
                "Characteristics": dbus.Array(
                    [dbus.ObjectPath(self.ff01.path),
                     dbus.ObjectPath(self.ff02.path)],
                    signature="o",
                ),
            }, signature="sv")
        }

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.get_props().get(interface, dbus.Dictionary({}, signature="sv"))


class GATTApplication(dbus.service.Object):

    PATH = BASE_PATH + "/app"

    def __init__(self, bus, service):
        self._service = service
        dbus.service.Object.__init__(self, bus, self.PATH)

    @dbus.service.method(OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        result = {}
        result[dbus.ObjectPath(self._service.PATH)] = self._service.get_props()
        result[dbus.ObjectPath(self._service.ff01.path)] = self._service.ff01.get_props()
        result[dbus.ObjectPath(self._service.ff02.path)] = self._service.ff02.get_props()
        return result


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BLE transparent proxy / MITM for C60-A82C"
    )
    parser.add_argument("--bracelet-mac", default="A4:C1:38:9A:A8:2C",
                        metavar="MAC", help="Real bracelet MAC (default: A4:C1:38:9A:A8:2C)")
    parser.add_argument("--name", default="C60-A82C",
                        help="BLE advertised name shown to the phone (default: C60-A82C)")
    parser.add_argument("--hci", default="hci0",
                        help="HCI adapter to use (default: hci0)")
    parser.add_argument("--no-upstream", action="store_true",
                        help="Run peripheral only, don't connect to bracelet (test mode)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d  %(message)s",
        datefmt="%H:%M:%S",
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
    )

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    upstream = None
    if not args.no_upstream:
        upstream = UpstreamClient(bus, args.bracelet_mac, hci=args.hci)
        upstream.connect()
        upstream.resolve_chars()
        upstream.start_notify()

    service = ProxyService(bus, upstream)
    app = GATTApplication(bus, service)
    adv = Advertisement(bus, args.name)

    # Wire bracelet FF01 notifications → phone FF01 notifications
    if upstream:
        upstream.set_notification_callback(service.ff01.push_notification)

    adapter_path = f"/org/bluez/{args.hci}"
    gatt_mgr = dbus.Interface(bus.get_object(BLUEZ_SVC, adapter_path), GATT_MGR_IFACE)
    adv_mgr  = dbus.Interface(bus.get_object(BLUEZ_SVC, adapter_path), ADV_MGR_IFACE)

    loop = GLib.MainLoop()

    # RegisterApplication and RegisterAdvertisement must be called after the
    # GLib main loop is running, because bluetoothd calls back into our process
    # (GetManagedObjects) as part of the registration handshake.
    def _on_app_registered():
        LOG.info("[peripheral] GATT application registered")
        adv_mgr.RegisterAdvertisement(
            adv.PATH, {},
            reply_handler=_on_adv_registered,
            error_handler=_on_adv_error,
        )

    def _on_app_error(exc):
        LOG.error("[peripheral] RegisterApplication failed: %s", exc)
        loop.quit()

    def _on_adv_registered():
        LOG.info("[peripheral] advertising as '%s'", args.name)
        LOG.info("=" * 60)
        if upstream:
            LOG.info("Proxy active:")
            LOG.info("  bracelet  :  %s", args.bracelet_mac)
            LOG.info("  phone sees:  '%s' (this Linux adapter)", args.name)
        else:
            LOG.info("Test mode: advertising '%s', logging phone writes only", args.name)
        LOG.info("Ctrl-C to stop")
        LOG.info("=" * 60)

    def _on_adv_error(exc):
        LOG.error("[peripheral] RegisterAdvertisement failed: %s", exc)
        loop.quit()

    def _start_peripheral():
        LOG.info("[peripheral] registering GATT application...")
        gatt_mgr.RegisterApplication(
            app.PATH, {},
            reply_handler=_on_app_registered,
            error_handler=_on_app_error,
        )
        return False  # don't repeat

    GLib.timeout_add(100, _start_peripheral)

    try:
        loop.run()
    except KeyboardInterrupt:
        LOG.info("Shutting down...")
    finally:
        try:
            adv_mgr.UnregisterAdvertisement(adv.PATH)
        except Exception:
            pass
        try:
            gatt_mgr.UnregisterApplication(app.PATH)
        except Exception:
            pass


if __name__ == "__main__":
    main()
