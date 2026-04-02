from __future__ import annotations

from typing import Callable


SENTINEL_DIAGNOSIS_INTERVAL = 300
SENTINEL_ADAPTER_RESET_THRESHOLD = 4
SENTINEL_NOTIFY_RETRY_INTERVAL = 60


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
