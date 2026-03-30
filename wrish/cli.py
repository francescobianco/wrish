from __future__ import annotations

import argparse
import sys

from .config import load_config
from .devices.c60_a82c import C60A82CDevice, DeviceError


def build_parser() -> argparse.ArgumentParser:
    config = load_config()

    parser = argparse.ArgumentParser(
        prog="wrish",
        description="Control supported wristbands over BlueZ D-Bus.",
    )
    parser.add_argument("--device", default=config.device, help="Device profile")
    parser.add_argument("--mac", default=config.mac, help="Device MAC address")
    parser.add_argument("--hci", default=config.hci, help="Bluetooth adapter")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose BLE debug logs on stderr",
    )
    parser.add_argument(
        "--no-init",
        action="store_true",
        help="Skip startup session initialization before notifications",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser("info", help="Read device information")
    info.set_defaults(handler=_handle_info)

    battery = subparsers.add_parser("battery", help="Read battery level")
    battery.set_defaults(handler=_handle_battery)

    find = subparsers.add_parser("find", help="Ring the bracelet")
    find.set_defaults(handler=_handle_find)

    vibrate = subparsers.add_parser("vibrate", help="Trigger bracelet vibration")
    vibrate.set_defaults(handler=_handle_vibrate)

    raw = subparsers.add_parser("raw", help="Send raw hex bytes to FF02")
    raw.add_argument("bytes", nargs="+", help="Hex bytes, for example: 0a 01 00 06 10")
    raw.set_defaults(handler=_handle_raw)

    notify = subparsers.add_parser("notify", help="Send a generic app notification")
    notify.add_argument("--app", default="whatsapp", help="App type")
    notify.add_argument("--title", required=True, help="Notification title")
    notify.add_argument("--body", required=True, help="Notification body")
    notify.set_defaults(handler=_handle_notify)

    sms = subparsers.add_parser("sms", help="Simulate an incoming SMS")
    sms.add_argument(
        "--from",
        dest="sender",
        required=True,
        help="Sender shown on the bracelet",
    )
    sms.add_argument("--body", required=True, help="SMS body")
    sms.set_defaults(handler=_handle_sms)

    call = subparsers.add_parser("call", help="Simulate an incoming call")
    call.add_argument("--from", dest="caller", default="", help="Caller name")
    call.add_argument("--number", default="", help="Phone number")
    call.set_defaults(handler=_handle_call)

    return parser


def build_device(args: argparse.Namespace) -> C60A82CDevice:
    if args.device != "C60-A82C":
        raise DeviceError(f"Unsupported device profile: {args.device}")
    return C60A82CDevice(mac=args.mac, hci=args.hci, debug=args.debug)


def _handle_info(args: argparse.Namespace) -> int:
    device = build_device(args)
    info = device.read_info()
    print(f"Device Name: {info['name']}")
    print(f"MAC:         {device.mac}")
    return 0


def _handle_battery(args: argparse.Namespace) -> int:
    device = build_device(args)
    percent = device.read_battery()
    print(f"Battery: {percent}%")
    return 0


def _handle_find(args: argparse.Namespace) -> int:
    device = build_device(args)
    device.find_device()
    print("Bracelet found (vibrating)")
    return 0


def _handle_vibrate(args: argparse.Namespace) -> int:
    device = build_device(args)
    device.vibrate()
    print("Vibration sent")
    return 0


def _handle_raw(args: argparse.Namespace) -> int:
    device = build_device(args)
    response = device.send_raw_hex(args.bytes)
    if response is None:
        print("No response received")
    else:
        print("Response:", " ".join(f"{byte:02x}" for byte in response))
    return 0


def _handle_notify(args: argparse.Namespace) -> int:
    device = build_device(args)
    device.send_notification(
        app_name=args.app,
        title=args.title,
        body=args.body,
        do_init=not args.no_init,
    )
    print("Notification sent")
    return 0


def _handle_sms(args: argparse.Namespace) -> int:
    device = build_device(args)
    device.send_sms(sender=args.sender, text=args.body, do_init=not args.no_init)
    print("SMS sent")
    return 0


def _handle_call(args: argparse.Namespace) -> int:
    device = build_device(args)
    device.send_call(caller=args.caller, number=args.number, do_init=not args.no_init)
    print("Call sent")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.handler(args)
    except DeviceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
