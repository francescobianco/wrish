from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_DEVICE = "C60-A82C"
DEFAULT_MAC = "A4:C1:38:9A:A8:2C"
DEFAULT_HCI = "hci0"


@dataclass(slots=True)
class Config:
    device: str = DEFAULT_DEVICE
    mac: str = DEFAULT_MAC
    hci: str = DEFAULT_HCI


def _load_rc_vars() -> dict[str, str]:
    for candidate in (Path.cwd() / ".wrishrc", Path.home() / ".wrishrc"):
        if not candidate.is_file():
            continue

        values: dict[str, str] = {}
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("WRISH_"):
                values[key] = value.strip()
        return values

    return {}


def load_config() -> Config:
    rc_values = _load_rc_vars()
    env = os.environ
    return Config(
        device=env.get("WRISH_DEVICE", rc_values.get("WRISH_DEVICE", DEFAULT_DEVICE)),
        mac=env.get("WRISH_MAC", rc_values.get("WRISH_MAC", DEFAULT_MAC)),
        hci=env.get("WRISH_HCI", rc_values.get("WRISH_HCI", DEFAULT_HCI)),
    )
