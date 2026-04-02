from __future__ import annotations

from typing import Callable


SENTINEL_DIAGNOSIS_INTERVAL = 300
SENTINEL_ADAPTER_RESET_THRESHOLD = 4
SENTINEL_NOTIFY_RETRY_INTERVAL = 60
SENTINEL_FIND_PHONE_POLL_INTERVAL = 1.0


def sentinel_state(status: dict[str, object]) -> str:
    if not status.get("adapter_present", False):
        return "adapter-missing"
    if not status.get("adapter_powered", False):
        return "adapter-off"
    if not status.get("present", False):
        return "device-missing"
    if not status.get("connected", False):
        return "device-disconnected"
    return "connected"


def maybe_cycle_sentinel_adapter(
    device,
    consecutive_failures: int,
    *,
    log_fn: Callable[[str], None] | None = None,
    threshold: int = SENTINEL_ADAPTER_RESET_THRESHOLD,
) -> bool:
    if consecutive_failures < threshold:
        return False
    if log_fn is not None:
        log_fn(
            "sentinel: "
            f"{consecutive_failures} consecutive recovery failures; cycling Bluetooth adapter"
        )
    device.cycle_bluetooth()
    return True


def maybe_run_sentinel_dialer(
    device,
    *,
    listen_timeout: float,
    log_fn: Callable[[str], None],
) -> bool:
    count = device.listen_for_find_phone(timeout=listen_timeout, max_events=1, quiet=True)
    if count <= 0:
        return False

    log_fn("[sentinel] find-phone received; entering dialer mode")

    digits: list[str] = []
    session_open = {"value": False}
    pending_taps = {"value": 0}

    def on_symbol(symbol: str) -> None:
        log_fn(f"[dialer] symbol {symbol}")
        if not session_open["value"]:
            return
        if symbol == "T":
            pending_taps["value"] += 1
            return
        if symbol != "K":
            return
        if pending_taps["value"] <= 0:
            return
        digit = str(pending_taps["value"])
        pending_taps["value"] = 0
        digits.append(digit)
        log_fn(f"[dialer] digit {digit}")

    def on_status(status: str) -> None:
        log_fn(f"[dialer] {status}")
        if status == "SESSION OPEN":
            session_open["value"] = True
            pending_taps["value"] = 0
        elif status == "SESSION CLOSE":
            session_open["value"] = False
            pending_taps["value"] = 0

    status = device.run_dialer(
        arm_timeout=10.0,
        cluster_gap=0.75,
        k_min=3,
        k_max=5,
        on_symbol=on_symbol,
        on_status=on_status,
    )
    if digits:
        log_fn(f"[dialer] number {''.join(digits)}")
    log_fn(f"[dialer] status {status}")
    return True
