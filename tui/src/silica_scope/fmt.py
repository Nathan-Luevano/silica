from __future__ import annotations

BLOCKS = " ▏▎▍▌▋▊▉█"


def commas(n: float) -> str:
    return f"{round(n):,}"


def pct(value: float, places: int = 2) -> str:
    return f"{value * 100:.{places}f}%"


def bar(fraction: float, width: int = 24, fill: str = "█", empty: str = "░") -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return fill * filled + empty * (width - filled)


def fine_bar(fraction: float, width: int = 24) -> str:
    # eighth-block resolution: at 24 cells the difference between 84.8% and
    # 87.6% is a fifth of a cell, and a coarse bar renders both identically.
    fraction = max(0.0, min(1.0, fraction))
    total_eighths = round(fraction * width * 8)
    full, rest = divmod(total_eighths, 8)
    out = "█" * full
    if rest:
        out += BLOCKS[rest]
    return out.ljust(width, "·")


def duration(ms: float) -> str:
    seconds = float(ms) / 1000.0
    if seconds < 1:
        return f"{ms:.0f} ms"
    if seconds < 90:
        return f"{seconds:.1f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.1f} min"
    return f"{minutes / 60:.1f} h"


def human_bytes(n: float) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def truncate(text: str, width: int) -> str:
    if width <= 1:
        return text[:width]
    return text if len(text) <= width else text[: width - 1] + "…"


def abbreviate(n: float) -> str:
    value = float(n)
    for suffix, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= scale:
            return f"{value / scale:.1f}{suffix}".replace(".0", "")
    return str(int(value))
