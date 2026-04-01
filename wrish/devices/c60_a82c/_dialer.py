from __future__ import annotations


def decode_dialer_symbols(symbols: list[str]) -> str | None:
    armed = False
    digits: list[str] = []
    taps = 0

    for symbol in symbols:
        if not armed:
            if symbol == "K":
                armed = True
            continue

        if symbol == "T":
            taps += 1
            continue

        if symbol != "K":
            continue

        if taps == 0:
            return "".join(digits) if digits else None

        digits.append(str(taps))
        taps = 0

    return None


def format_calibration_report(press_times: list[float]) -> str:
    if not press_times:
        return "CALIBRATION\npresses=0\n"

    base = press_times[0]
    relative = [timestamp - base for timestamp in press_times]
    deltas = [press_times[index] - press_times[index - 1] for index in range(1, len(press_times))]

    lines = [
        "CALIBRATION",
        f"presses={len(press_times)}",
        "relative_seconds=" + ",".join(f"{value:.3f}" for value in relative),
        "delta_seconds=" + ",".join(f"{value:.3f}" for value in deltas) if deltas else "delta_seconds=",
    ]

    if deltas:
        suggested_gap = max(deltas) + 0.25
        lines.append(f"suggested_cluster_gap={suggested_gap:.3f}")

    return "\n".join(lines) + "\n"