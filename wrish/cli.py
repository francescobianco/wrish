from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from .concurrency import BleLockBusyError, ble_lock_status, ble_session
from .config import load_config
from .devices.c60_a82c import (
    C60A82CDevice,
    DeviceError,
    decode_dialer_symbols,
)
from .relay import run_relay
from .systemd import follow_logs, run_systemd_wizard
from .systemd import systemd_action


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

    health = subparsers.add_parser("health", help="Read health snapshot (steps, HR, BP, SpO2)")
    health.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Also fetch historical HR/BP/SpO2 for this date (minute-by-minute)",
    )
    health.add_argument(
        "--json",
        action="store_true",
        help="Output raw data as JSON",
    )
    health.set_defaults(handler=_handle_health)

    find = subparsers.add_parser("find", help="Ring the bracelet")
    find.set_defaults(handler=_handle_find)

    vibrate = subparsers.add_parser("vibrate", help="Trigger bracelet vibration")
    vibrate.add_argument("--seconds", type=float, default=None, help="Repeat vibration for N seconds")
    vibrate.add_argument("--interval", type=float, default=2.0, help="Seconds between vibration attempts in loop mode")
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

    button = subparsers.add_parser("button", help="Listen for bracelet button events on FF01")
    button.add_argument("--timeout", type=float, default=None, help="Stop listening after N seconds")
    button.add_argument("--count", type=int, default=None, help="Stop after N button events")
    button.set_defaults(handler=_handle_button)

    horn = subparsers.add_parser("horn", help="Play a horn sound on each bracelet button press")
    horn.add_argument("--timeout", type=float, default=None, help="Stop listening after N seconds")
    horn.add_argument("--count", type=int, default=None, help="Stop after N button events")
    horn.add_argument("--duration", type=float, default=0.4, help="Horn sound duration in seconds (default: 0.4)")
    horn.set_defaults(handler=_handle_horn)

    lock_status = subparsers.add_parser("lock-status", help="Show the shared BLE lock status")
    lock_status.set_defaults(handler=_handle_lock_status)

    dialer = subparsers.add_parser("dialer", help="Decode K/T button sequences into dialed numbers")
    dialer.add_argument("--arm-timeout", type=float, default=10.0, help="Exit if the opening T T T sequence is not received in time")
    dialer.add_argument("--cluster-gap", type=float, default=0.5, help="Max gap in seconds between presses of the same cluster")
    dialer.add_argument("--k-min", type=int, default=4, help="Minimum fast presses to classify a cluster as K")
    dialer.add_argument("--k-max", type=int, default=4, help="Maximum fast presses to classify a cluster as K")
    dialer.add_argument(
        "--simulate",
        default=None,
        help="Test parser with a synthetic sequence like 'K T K T T K K'",
    )
    dialer.add_argument("--calibrate", action="store_true", help="Capture one raw K cluster and print timing data")
    dialer.add_argument("--timeout", type=float, default=8.0, help="Calibration/listen timeout in seconds")
    dialer.set_defaults(handler=_handle_dialer)

    relay = subparsers.add_parser("relay", help="Expose local HTTP commands through a Hookpool .relay endpoint")
    relay.add_argument("relay_url", help="Hookpool .relay URL")
    relay.add_argument("--bind", default="127.0.0.1", help="Local bind address")
    relay.add_argument("--port", default=8787, type=int, help="Local bind port, use 0 for auto")
    relay.add_argument("--sentinel", action="store_true", help="Run sentinel monitoring in the same process")
    relay.add_argument("--sentinel-interval", default=5.0, type=float, help="Sentinel check interval in seconds")
    relay.add_argument("--sentinel-app", default="whatsapp", help="Notification app type used by sentinel")
    relay.add_argument("--sentinel-title", default="wrish", help="Sentinel notification title")
    relay.add_argument("--sentinel-body", default="Connected", help="Sentinel notification body")
    relay.set_defaults(handler=_handle_relay)

    sentinel = subparsers.add_parser("sentinel", help="Keep reconnecting to the bracelet and announce when connected")
    sentinel.add_argument("--interval", default=5.0, type=float, help="Seconds between connectivity checks")
    sentinel.add_argument("--app", default="whatsapp", help="Notification app type used for the connection message")
    sentinel.add_argument("--title", default="wrish", help="Notification title sent on successful connection")
    sentinel.add_argument(
        "--body",
        default="Connected",
        help="Notification body sent on successful connection",
    )
    sentinel.set_defaults(handler=_handle_sentinel)

    systemd = subparsers.add_parser("systemd", help="Interactive wizard that creates a user-level systemd service")
    systemd.add_argument("action", nargs="?", choices=("start", "stop", "reset"), help="Run a non-interactive systemd shortcut")
    systemd.add_argument("--install", action="store_true", help="Force reinstall of the default wrish.service")
    systemd.add_argument("--logs", action="store_true", help="Follow journal logs of wrish.service")
    systemd.set_defaults(handler=_handle_systemd)

    return parser


def build_device(args: argparse.Namespace) -> C60A82CDevice:
    if args.device != "C60-A82C":
        raise DeviceError(f"Unsupported device profile: {args.device}")
    return C60A82CDevice(mac=args.mac, hci=args.hci, debug=args.debug)


def _run_with_ble_lock(args: argparse.Namespace, action, *, reason: str):
    with ble_session(blocking=True, reason=reason):
        return action(build_device(args))


def _handle_info(args: argparse.Namespace) -> int:
    device = build_device(args)
    info = device.read_info()
    print(f"Device Name: {info['name']}")
    print(f"MAC:         {device.mac}")
    return 0


def _handle_battery(args: argparse.Namespace) -> int:
    percent = _run_with_ble_lock(args, lambda device: device.read_battery(), reason="battery")
    print(f"Battery: {percent}%")
    return 0


def _handle_health(args: argparse.Namespace) -> int:
    import datetime
    import json

    date = None
    if args.date:
        try:
            date = datetime.date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date '{args.date}', expected YYYY-MM-DD", file=sys.stderr)
            return 1

    data = _run_with_ble_lock(
        args,
        lambda device: device.read_health(date),
        reason="health",
    )

    if not data:
        print("No data received", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    if "last_measured" in data:
        print(f"Last measured: {data['last_measured']}")

    if "snapshot_steps" in data:
        s = data["snapshot_steps"]
        print(f"Steps:     {s['steps']}")
        print(f"Calories:  {s['calories_kcal']} kcal")
        print(f"Distance:  {s['distance_m']} m")

    if "snapshot_hart" in data:
        h = data["snapshot_hart"]
        print(f"HR:        {h['hr_bpm']} bpm")
        print(f"BP:        {h['bp_systolic_mmhg']}/{h['bp_diastolic_mmhg']} mmHg")
        print(f"SpO2:      {h['spo2_pct']}%")

    if "history_hart" in data:
        records = data["history_hart"]
        print(f"\nHistorical HR/BP/SpO2 — {args.date}  ({len(records)} measurements with data)")
        print(f"{'Time':>5}  {'HR':>6}  {'BP':>9}  {'SpO2':>4}")
        print(f"{'-----':>5}  {'------':>6}  {'---------':>9}  {'----':>4}")
        for r in records:
            bp = f"{r['bp_systolic_mmhg']}/{r['bp_diastolic_mmhg']}"
            print(f"{r['time']:>5}  {r['hr_bpm']:>4} bpm  {bp:>9}  {r['spo2_pct']:>3}%")

    return 0


def _handle_find(args: argparse.Namespace) -> int:
    _run_with_ble_lock(args, lambda device: device.find_device(), reason="find")
    print("Bracelet found (vibrating)")
    return 0


def _handle_vibrate(args: argparse.Namespace) -> int:
    if args.seconds is None:
        _run_with_ble_lock(args, lambda device: device.vibrate(), reason="vibrate")
        print("Vibration sent")
        return 0

    device = build_device(args)
    deadline = time.monotonic() + max(args.seconds, 0.0)
    interval = max(args.interval, 0.2)
    count = 0

    with ble_session(blocking=True, reason="vibrate-loop"):
        while time.monotonic() < deadline:
            device.vibrate()
            count += 1
            remaining = max(0.0, deadline - time.monotonic())
            print(f"Vibration #{count} sent, remaining {remaining:.1f}s")
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))

    print(f"Loop finished after {count} vibrations")
    return 0


def _handle_raw(args: argparse.Namespace) -> int:
    response = _run_with_ble_lock(args, lambda device: device.send_raw_hex(args.bytes), reason="raw")
    if response is None:
        print("No response received")
    else:
        print("Response:", " ".join(f"{byte:02x}" for byte in response))
    return 0


def _handle_notify(args: argparse.Namespace) -> int:
    def action(device: C60A82CDevice):
        device.send_notification(
            app_name=args.app,
            title=args.title,
            body=args.body,
            do_init=not args.no_init,
        )

    _run_with_ble_lock(args, action, reason="notify")
    print("Notification sent")
    return 0


def _handle_sms(args: argparse.Namespace) -> int:
    def action(device: C60A82CDevice):
        device.send_sms(sender=args.sender, text=args.body, do_init=not args.no_init)

    _run_with_ble_lock(args, action, reason="sms")
    print("SMS sent")
    return 0


def _handle_call(args: argparse.Namespace) -> int:
    def action(device: C60A82CDevice):
        device.send_call(caller=args.caller, number=args.number, do_init=not args.no_init)

    _run_with_ble_lock(args, action, reason="call")
    print("Call sent")
    return 0


def _handle_button(args: argparse.Namespace) -> int:
    print("Listening for bracelet button events...")
    count = _run_with_ble_lock(
        args,
        lambda device: device.listen_for_button(timeout=args.timeout, max_events=args.count),
        reason="button",
    )
    print(f"Button events received: {count}")
    return 0


def _play_horn(duration: float = 0.4) -> None:
    """Play a two-tone car horn using raw PCM piped to aplay (non-blocking).

    Generates a mix of 392 Hz (G4) + 523 Hz (C5) — the classic two-tone horn
    interval. Spawns aplay in a background process so it does not block the
    BLE event loop.  Silently does nothing if aplay is not installed.
    """
    import array
    import math
    import shutil
    import subprocess

    if not shutil.which("aplay"):
        return

    sample_rate = 22050
    n = int(sample_rate * duration)
    buf = array.array("h")
    for i in range(n):
        t = i / sample_rate
        # Brief linear fade-out over last 10 % to avoid click at end
        envelope = 1.0 if i < n * 0.9 else (n - i) / (n * 0.1)
        v = (math.sin(2 * math.pi * 392 * t) + math.sin(2 * math.pi * 523 * t)) / 2
        buf.append(int(v * envelope * 28000))

    subprocess.Popen(
        ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).communicate(buf.tobytes())


def _handle_horn(args: argparse.Namespace) -> int:
    print("Horn mode — press the bracelet button to honk. Ctrl-C to stop.")
    count = _run_with_ble_lock(
        args,
        lambda device: device.listen_for_button(
            timeout=args.timeout,
            max_events=args.count,
            on_event=lambda: _play_horn(args.duration),
        ),
        reason="horn",
    )
    print(f"Horn events: {count}")
    return 0


def _handle_lock_status(args: argparse.Namespace) -> int:
    status = ble_lock_status()
    if status["busy"]:
        details = [str(status["reason"])]
        if status.get("pid") is not None:
            details.append(f"pid={status['pid']}")
        age_seconds = status.get("age_seconds")
        if isinstance(age_seconds, (int, float)):
            details.append(f"age={age_seconds:.1f}s")
        print(f"BLE lock: busy ({', '.join(details)})")
    else:
        print("BLE lock: free")
    print(f"Path: {status['path']}")
    return 0


def _handle_dialer(args: argparse.Namespace) -> int:
    if args.calibrate:
        print("Dialer calibration: perform one K cluster now.")
        print("When finished, paste the output back here.")
        report = _run_with_ble_lock(
            args,
            lambda device: device.calibrate_button_cluster(timeout=args.timeout),
            reason="dialer-calibrate",
        )
        print(report, end="")
        return 0

    if args.simulate:
        symbols = [part for part in args.simulate.split() if part]
        result = decode_dialer_symbols(symbols)
        if result is None:
            print("No number decoded")
        else:
            print(f"N {result}")
        return 0

    print("Dialer listening...")
    print("Open sequence: T T T")
    print("Close sequence after open: K K")
    status = _run_with_ble_lock(
        args,
        lambda device: device.run_dialer(
            arm_timeout=args.arm_timeout,
            cluster_gap=args.cluster_gap,
            k_min=args.k_min,
            k_max=args.k_max,
        ),
        reason="dialer",
    )
    print(f"Dialer status: {status}")
    return 0


def _handle_relay(args: argparse.Namespace) -> int:
    run_relay(
        relay_url=args.relay_url,
        mac=args.mac,
        hci=args.hci,
        bind=args.bind,
        port=args.port,
        debug=args.debug,
        sentinel=args.sentinel,
        sentinel_interval=args.sentinel_interval,
        sentinel_app=args.sentinel_app,
        sentinel_title=args.sentinel_title,
        sentinel_body=args.sentinel_body,
    )
    return 0


_SENTINEL_DIAGNOSIS_INTERVAL = 300  # seconds between proactive adapter health checks


def _sentinel_state(status: dict[str, object]) -> str:
    if not status.get("adapter_present", False):
        return "adapter-missing"
    if not status.get("adapter_powered", False):
        return "adapter-off"
    if not status.get("present", False):
        return "device-missing"
    if not status.get("connected", False):
        return "device-disconnected"
    return "connected"


def _handle_sentinel(args: argparse.Namespace) -> int:
    device = build_device(args)
    announced = False
    last_diagnosis = 0.0
    last_state = "unknown"

    while True:
        now = time.monotonic()

        try:
            status = device.status()
            state = _sentinel_state(status)

            if state != last_state and args.debug:
                print(f"Sentinel: state={state}", file=sys.stderr)
            last_state = state

            if state != "connected":
                announced = False
                if args.debug:
                    print("Sentinel: recovery attempt started", file=sys.stderr)

                # Proactive adapter self-diagnosis on degraded state and periodically.
                if state.startswith("adapter-") or (now - last_diagnosis >= _SENTINEL_DIAGNOSIS_INTERVAL):
                    if args.debug:
                        print(f"Sentinel: diagnosis started (state={state})", file=sys.stderr)
                    result = device.diagnose_adapter()
                    last_diagnosis = now
                    if args.debug:
                        if result["powered"]:
                            print("Sentinel: diagnosis ok", file=sys.stderr)
                        elif result["error"]:
                            print(f"Sentinel: diagnosis failed: {result['error']}", file=sys.stderr)

                try:
                    with ble_session(blocking=False, reason="sentinel"):
                        device.connect()
                        status = device.status()
                        state = _sentinel_state(status)
                        last_state = state
                        if args.debug:
                            print(f"Sentinel: recovery result state={state}", file=sys.stderr)
                except BleLockBusyError:
                    if args.debug:
                        print("Sentinel: paused, BLE busy with another command", file=sys.stderr)
                    time.sleep(max(args.interval, 0.2))
                    continue
            elif now - last_diagnosis >= _SENTINEL_DIAGNOSIS_INTERVAL:
                if args.debug:
                    print("Sentinel: periodic diagnosis started", file=sys.stderr)
                result = device.diagnose_adapter()
                last_diagnosis = now
                if args.debug:
                    if result["powered"]:
                        print("Sentinel: periodic diagnosis ok", file=sys.stderr)
                    elif result["error"]:
                        print(f"Sentinel: periodic diagnosis failed: {result['error']}", file=sys.stderr)

            if state == "connected" and not announced:
                try:
                    with ble_session(blocking=False, reason="sentinel-notify"):
                        if args.debug:
                            print("Sentinel: connected, sending notification...", file=sys.stderr)
                        device.send_notification(
                            app_name=args.app,
                            title=args.title,
                            body=args.body,
                            do_init=True,
                        )
                        if args.debug:
                            print("Sentinel: notification sent", file=sys.stderr)
                        announced = True
                except BleLockBusyError:
                    if args.debug:
                        print("Sentinel: notification deferred, BLE busy with another command", file=sys.stderr)
        except DeviceError as exc:
            announced = False
            if args.debug:
                print(f"Sentinel: recovery failed: {exc}", file=sys.stderr)

        time.sleep(max(args.interval, 0.2))


def _handle_systemd(args: argparse.Namespace) -> int:
    if args.action:
        return systemd_action(args.action)
    if args.logs:
        return follow_logs()
    binary = str(Path.home() / ".local/bin/wrish")
    service_path = run_systemd_wizard(binary, force_install=args.install)
    print(f"Systemd service created: {service_path}")
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
