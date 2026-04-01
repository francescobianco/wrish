from __future__ import annotations

import datetime as dt


def checksum(frame: list[int]) -> int:
    total = 0
    for byte in frame:
        total = (total + byte) & 0xFF
    return ((total * 0x56) + 0x5A) & 0xFF


def frame_set_device_state(state_payload: list[int]) -> list[int]:
    payload = list(state_payload)
    if len(payload) >= 9:
        payload[8] = 0x01
    if len(payload) >= 15:
        payload[14] = 0x02
    frame = [0x02, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return frame + [checksum(frame)]


def frame_set_time(now: dt.datetime | None = None) -> list[int]:
    now = now or dt.datetime.now()
    payload = [
        now.year & 0xFF,
        (now.year >> 8) & 0xFF,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        0x00,
    ]
    frame = [0x04, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF] + payload
    return frame + [checksum(frame)]


def frame_message_type(app_type: int) -> list[int]:
    frame = [0x0A, 0x02, 0x00, 0x00, app_type]
    return frame + [checksum(frame)]


def frame_message_part(kind: int, text: str, max_len: int) -> list[int]:
    payload_bytes = list(text.encode("utf-8")[:max_len])
    payload_length = 1 + len(payload_bytes)
    frame = [0x0A, payload_length & 0xFF, (payload_length >> 8) & 0xFF, kind] + payload_bytes
    return frame + [checksum(frame)]