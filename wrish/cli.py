from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from ._sentinel import (
    SENTINEL_DIAGNOSIS_INTERVAL,
    SENTINEL_FIND_PHONE_POLL_INTERVAL,
    SENTINEL_NOTIFY_RETRY_INTERVAL,
    maybe_run_sentinel_dialer,
    maybe_cycle_sentinel_adapter,
    sentinel_state,
)
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

    game = subparsers.add_parser("game", help="Geometry Dash terminal game — bracelet button = jump")
    game.set_defaults(handler=_handle_game)

    listen = subparsers.add_parser("listen", help="Listen for the bracelet find-phone trigger on FF01")
    listen.add_argument("--timeout", type=float, default=None, help="Stop listening after N seconds")
    listen.add_argument("--count", type=int, default=None, help="Stop after N events")
    listen.add_argument("--no-sound", action="store_true", help="Do not play a ringtone on detection")
    listen.add_argument("--exec", dest="exec_cmd", default=None, metavar="CMD", help="Shell command to run on each event")
    listen.set_defaults(handler=_handle_listen)

    horn = subparsers.add_parser("horn", help="Play a horn sound on each bracelet button press")
    horn.add_argument("--timeout", type=float, default=None, help="Stop listening after N seconds")
    horn.add_argument("--count", type=int, default=None, help="Stop after N button events")
    horn.add_argument("--duration", type=float, default=0.4, help="Horn sound duration in seconds (default: 0.4)")
    horn.set_defaults(handler=_handle_horn)

    lock_status = subparsers.add_parser("lock-status", help="Show the shared BLE lock status")
    lock_status.set_defaults(handler=_handle_lock_status)

    dialer = subparsers.add_parser("dialer", help="Decode K/T button sequences into dialed numbers")
    dialer.add_argument("--arm-timeout", type=float, default=10.0, help="Exit if the opening T T T sequence is not received in time")
    dialer.add_argument("--cluster-gap", type=float, default=0.75, help="Max gap in seconds between presses of the same cluster")
    dialer.add_argument("--k-min", type=int, default=3, help="Minimum fast presses to classify a cluster as K")
    dialer.add_argument("--k-max", type=int, default=5, help="Maximum fast presses to classify a cluster as K")
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


def _handle_game(args: argparse.Namespace) -> int:
    import queue
    import random
    import shutil
    import threading
    import time as _time

    # ── Display constants ──────────────────────────────────────────────
    W = min(shutil.get_terminal_size().columns - 2, 70)
    PLAY_H = 4           # usable play rows (0 = top, GROUND = bottom)
    GROUND = PLAY_H - 1  # row index of the ground
    CHAR_X = 5           # fixed horizontal column of the character
    TICK = 0.08          # seconds per frame

    RESET       = "\033[0m"
    BOLD        = "\033[1m"
    RED         = "\033[1;31m"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    CLEAR       = "\033[2J\033[H"
    ERASE_LINE  = "\033[2K"

    CHAR_GROUND = "■"    # on the ground
    CHAR_AIR    = "●"    # in the air
    SPIKE       = "▲"
    GROUND_FILL = "═"

    # Jump arc: height above GROUND (in rows) at each tick
    JUMP_ARC = [1, 2, 3, 3, 3, 2, 1, 0]
    JUMP_LEN = len(JUMP_ARC)

    # Total lines the game frame occupies (play rows + ground line + status)
    FRAME_LINES = PLAY_H + 2

    # ── Mutable game state ─────────────────────────────────────────────
    jump_pos: int = -1        # -1 = on ground; 0..JUMP_LEN-1 = mid-jump
    char_y: int = GROUND      # current display row of character
    spikes: list[int] = []    # x positions of active spikes (after moving)
    score: int = 0
    speed: int = 1            # columns spikes advance per tick
    alive: bool = True
    spike_countdown: int = 20  # ticks until next spike spawns

    q: queue.Queue[bool] = queue.Queue()
    stop = threading.Event()

    def _ble(device) -> None:
        device.listen_for_button(on_event=lambda: q.put(True), stop=stop)

    def _build_frame() -> list[str]:
        lines: list[str] = []
        for r in range(PLAY_H):
            row = [" "] * W
            if r == char_y:
                row[CHAR_X] = CHAR_GROUND if char_y == GROUND else CHAR_AIR
            if r == GROUND:
                for sx in spikes:
                    if 0 <= sx < W and sx != CHAR_X:
                        row[sx] = SPIKE
            lines.append("".join(row))
        lines.append(GROUND_FILL * W)
        lines.append(f" Score: {score:>5}   Speed: {speed}   [button = jump]")
        return lines

    def _write_frame(lines: list[str], *, first: bool) -> None:
        buf: list[str] = []
        if not first:
            buf.append(f"\033[{FRAME_LINES}A")
        for line in lines:
            buf.append(f"{ERASE_LINE}{line}\r\n")
        sys.stdout.write("".join(buf))
        sys.stdout.flush()

    def _game_loop(device) -> None:
        nonlocal jump_pos, char_y, spikes, score, speed, alive, spike_countdown

        t = threading.Thread(target=_ble, args=(device,), daemon=True)
        t.start()
        _time.sleep(0.6)  # let BLE session establish

        sys.stdout.write(HIDE_CURSOR + CLEAR)
        sys.stdout.write(f"{BOLD}GEOMETRY DASH{RESET}  —  press bracelet button to jump\n\n")
        sys.stdout.flush()
        _write_frame(_build_frame(), first=True)

        try:
            while alive:
                t0 = _time.monotonic()

                # ── Input ──────────────────────────────────────────────
                try:
                    q.get_nowait()
                    if jump_pos < 0:    # only jump when on ground
                        jump_pos = 0
                except queue.Empty:
                    pass

                # ── Physics ────────────────────────────────────────────
                if jump_pos >= 0:
                    char_y = GROUND - JUMP_ARC[jump_pos]
                    jump_pos += 1
                    if jump_pos >= JUMP_LEN:
                        jump_pos = -1
                        char_y = GROUND
                else:
                    char_y = GROUND

                # ── Move spikes left ───────────────────────────────────
                moved = [sx - speed for sx in spikes]

                # ── Collision (before removing off-screen spikes) ──────
                if char_y == GROUND:
                    for sx in moved:
                        if sx <= CHAR_X < sx + speed:
                            alive = False
                            break

                spikes = [sx for sx in moved if sx >= 0]

                # ── Spawn spike ────────────────────────────────────────
                spike_countdown -= 1
                if spike_countdown <= 0:
                    spikes.append(W - 1)
                    spike_countdown = random.randint(14, 32)

                # ── Score + speed ──────────────────────────────────────
                score += 1
                speed = 1 + score // 300

                # ── Render ─────────────────────────────────────────────
                _write_frame(_build_frame(), first=False)

                elapsed = _time.monotonic() - t0
                _time.sleep(max(0.0, TICK - elapsed))

        finally:
            stop.set()
            t.join(timeout=2.0)
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()

        sys.stdout.write("\n")
        print(f"{RED}{BOLD}GAME OVER{RESET}   Score: {BOLD}{score}{RESET}")
        print()

    _run_with_ble_lock(args, _game_loop, reason="game")
    return 0


def _play_ring() -> None:
    """Play a classic two-tone phone ring (480 Hz + 620 Hz, 1 s on) via aplay."""
    import array
    import math
    import shutil
    import subprocess

    if not shutil.which("aplay"):
        return

    sample_rate = 22050
    duration = 1.0
    n = int(sample_rate * duration)
    buf = array.array("h")
    fade = int(sample_rate * 0.02)  # 20 ms fade in/out
    for i in range(n):
        t = i / sample_rate
        envelope = 1.0
        if i < fade:
            envelope = i / fade
        elif i > n - fade:
            envelope = (n - i) / fade
        v = (math.sin(2 * math.pi * 480 * t) + math.sin(2 * math.pi * 620 * t)) / 2
        buf.append(int(v * envelope * 28000))

    subprocess.Popen(
        ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).communicate(buf.tobytes())


def _handle_listen(args: argparse.Namespace) -> int:
    import subprocess as _sp

    def on_event():
        if not args.no_sound:
            _play_ring()
        if args.exec_cmd:
            _sp.Popen(args.exec_cmd, shell=True)

    print("Listening for find-phone trigger from bracelet. Ctrl-C to stop.")
    count = _run_with_ble_lock(
        args,
        lambda device: device.listen_for_find_phone(
            timeout=args.timeout,
            max_events=args.count,
            on_event=on_event,
        ),
        reason="listen",
    )
    print(f"Find-phone events received: {count}")
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

def _handle_sentinel(args: argparse.Namespace) -> int:
    device = build_device(args)
    announced = False
    last_diagnosis = 0.0
    last_state = "unknown"
    recovery_failures = 0
    next_notify_attempt = 0.0

    while True:
        now = time.monotonic()

        try:
            status = device.status()
            state = sentinel_state(status)

            if state != last_state and args.debug:
                print(f"Sentinel: state={state}", file=sys.stderr)
            last_state = state

            if state != "connected":
                announced = False
                if args.debug:
                    print("Sentinel: recovery attempt started", file=sys.stderr)

                # Proactive adapter self-diagnosis on degraded state and periodically.
                if state.startswith("adapter-") or (now - last_diagnosis >= SENTINEL_DIAGNOSIS_INTERVAL):
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
                        state = sentinel_state(status)
                        last_state = state
                        if args.debug:
                            print(f"Sentinel: recovery result state={state}", file=sys.stderr)
                except BleLockBusyError:
                    if args.debug:
                        print("Sentinel: paused, BLE busy with another command", file=sys.stderr)
                    time.sleep(max(args.interval, 0.2))
                    continue
                if state != "connected":
                    recovery_failures += 1
                    maybe_cycle_sentinel_adapter(
                        device,
                        recovery_failures,
                        log_fn=lambda message: print(message, file=sys.stderr) if args.debug else None,
                    )
                else:
                    recovery_failures = 0
            elif now - last_diagnosis >= SENTINEL_DIAGNOSIS_INTERVAL:
                if args.debug:
                    print("Sentinel: periodic diagnosis started", file=sys.stderr)
                result = device.diagnose_adapter()
                last_diagnosis = now
                if args.debug:
                    if result["powered"]:
                        print("Sentinel: periodic diagnosis ok", file=sys.stderr)
                    elif result["error"]:
                        print(f"Sentinel: periodic diagnosis failed: {result['error']}", file=sys.stderr)

            if state == "connected" and not announced and now >= next_notify_attempt:
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
                        recovery_failures = 0
                except BleLockBusyError:
                    if args.debug:
                        print("Sentinel: notification deferred, BLE busy with another command", file=sys.stderr)
                except DeviceError as exc:
                    next_notify_attempt = time.monotonic() + SENTINEL_NOTIFY_RETRY_INTERVAL
                    if args.debug:
                        print(
                            "Sentinel: notification failed; "
                            f"will retry in {SENTINEL_NOTIFY_RETRY_INTERVAL}s: {exc}",
                            file=sys.stderr,
                        )
            else:
                recovery_failures = 0
                if state != "connected":
                    next_notify_attempt = 0.0
                else:
                    try:
                        maybe_run_sentinel_dialer(
                            device,
                            listen_timeout=min(max(args.interval, 0.2), SENTINEL_FIND_PHONE_POLL_INTERVAL),
                            log_fn=lambda message: print(message, file=sys.stderr),
                        )
                    except DeviceError:
                        raise
        except DeviceError as exc:
            announced = False
            recovery_failures += 1
            next_notify_attempt = 0.0
            if args.debug:
                print(f"Sentinel: recovery failed: {exc}", file=sys.stderr)
            maybe_cycle_sentinel_adapter(
                device,
                recovery_failures,
                log_fn=lambda message: print(message, file=sys.stderr) if args.debug else None,
            )

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
