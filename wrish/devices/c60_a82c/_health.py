"""Health data protocol for the C60-A82C bracelet.

Commands
--------
CMD_GET_CURRENT_STEP   20 01 00 00 70  → a0 0d 00 00 [steps u32 LE] [cals u32 LE] [dist u32 LE] [chk]
CMD_GET_HART_SNAPSHOT  21 01 00 00 c6  → a1 05 00 00 [HR] [diastolic] [systolic] [SpO2] [chk]

Historical (minute-by-minute, one full day):
  cmd  20: 20 05 00 01 [year_lo year_hi mm dd] [chk]  → a0 [len_lo len_hi] 01 [date] 0f [records] [chk]
  cmd  21: 21 05 00 01 [year_lo year_hi mm dd] [chk]  → a1 [len_lo len_hi] 01 [date] 0f [records] [chk]

HR/BP/SpO2 historical record: 4 bytes [HR diastolic systolic SpO2] per minute.
All-zero record = no measurement for that minute.

Responses arrive as multi-chunk BLE notifications (20 B ATT MTU).
The caller must reassemble chunks before passing data to the decoders.
"""

from __future__ import annotations

import datetime as dt
import struct

from ._protocol import checksum


def frame_health_hist_query(cmd: int, date: dt.date) -> list[int]:
    """Build a date-parameterised historical query frame.

    Example for cmd=0x21, date=2026-03-28:
        21 05 00 01 ea 07 03 1c <chk>
    """
    payload = [0x01, date.year & 0xFF, (date.year >> 8) & 0xFF, date.month, date.day]
    n = len(payload)
    frame = [cmd, n & 0xFF, (n >> 8) & 0xFF] + payload
    return frame + [checksum(frame)]


def decode_steps_snapshot(data: list[int]) -> dict[str, int] | None:
    """Decode CMD_GET_CURRENT_STEP response.

    Expected layout: a0 0d 00 00 [steps u32 LE] [cals u32 LE] [dist u32 LE] [chk]
    """
    if len(data) < 14 or data[0] != 0xA0 or data[3] != 0x00:
        return None
    steps = struct.unpack_from("<I", bytes(data), 4)[0]
    cals  = struct.unpack_from("<I", bytes(data), 8)[0]
    dist  = struct.unpack_from("<I", bytes(data), 12)[0]
    return {"steps": steps, "calories_kcal": cals, "distance_m": dist}


def decode_hart_snapshot(data: list[int]) -> dict[str, int] | None:
    """Decode CMD_GET_HART_SNAPSHOT response.

    Expected layout: a1 05 00 00 [HR] [diastolic] [systolic] [SpO2] [chk]
    """
    if len(data) < 9 or data[0] != 0xA1 or data[3] != 0x00:
        return None
    return {
        "hr_bpm":             data[4],
        "bp_diastolic_mmhg":  data[5],
        "bp_systolic_mmhg":   data[6],
        "spo2_pct":           data[7],
    }


def decode_hart_history(data: list[int], date: dt.date) -> list[dict] | None:
    """Decode a historical HR/BP/SpO2 response (multi-chunk reassembled frame).

    Expected layout:
        a1 [len_lo] [len_hi] 01 [year_lo year_hi mm dd] 0f [records...] [chk]

    Records start at byte 9; each is 4 bytes: [HR diastolic systolic SpO2].
    One record per minute (up to 1440). All-zero records are skipped.
    """
    if len(data) < 10 or data[0] != 0xA1 or data[3] != 0x01:
        return None
    records_raw = data[9:-1]  # strip 9-byte header and trailing checksum
    out: list[dict] = []
    for i in range(0, len(records_raw) - 3, 4):
        hr, dia, sys_, spo2 = records_raw[i], records_raw[i + 1], records_raw[i + 2], records_raw[i + 3]
        if hr == 0 and dia == 0 and sys_ == 0 and spo2 == 0:
            continue
        minute = i // 4
        ts = dt.datetime(date.year, date.month, date.day) + dt.timedelta(minutes=minute)
        out.append({
            "time":              ts.strftime("%H:%M"),
            "hr_bpm":            hr,
            "bp_diastolic_mmhg": dia,
            "bp_systolic_mmhg":  sys_,
            "spo2_pct":          spo2,
        })
    return out