from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time


_LOCK_PATH = Path("/tmp/wrish-ble.lock")


class BleLockBusyError(RuntimeError):
    pass


def _read_lock_metadata(raw: str) -> dict[str, object]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"reason": raw.strip()}


def ble_lock_status() -> dict[str, object]:
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+", encoding="utf-8") as handle:
        handle.seek(0)
        metadata = _read_lock_metadata(handle.read())
        started_at = metadata.get("started_at")
        age_seconds = None
        if isinstance(started_at, (int, float)):
            age_seconds = max(0.0, time.time() - float(started_at))
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {
                "busy": True,
                "reason": str(metadata.get("reason", "active")),
                "pid": metadata.get("pid"),
                "started_at": started_at,
                "age_seconds": age_seconds,
                "path": str(_LOCK_PATH),
            }
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return {
                "busy": False,
                "reason": str(metadata.get("reason", "")),
                "pid": metadata.get("pid"),
                "started_at": started_at,
                "age_seconds": age_seconds,
                "path": str(_LOCK_PATH),
            }


@contextmanager
def ble_session(*, blocking: bool, reason: str | None = None):
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            detail = reason or "busy"
            raise BleLockBusyError(f"BLE session busy ({detail})") from exc

        try:
            payload = {
                "reason": reason or "active",
                "pid": os.getpid(),
                "started_at": time.time(),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
            handle.flush()
            yield
        finally:
            try:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
