from __future__ import annotations


def commas(n: float) -> str:
    return f"{round(n):,}"


def pct(value: float, places: int = 2) -> str:
    return f"{value * 100:.{places}f}%"


def bar(fraction: float, width: int = 24, fill: str = "#", empty: str = "-") -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return fill * filled + empty * (width - filled)


def fine_bar(fraction: float, width: int = 24) -> str:
    # plain ASCII, one character of resolution per cell - no unicode
    # sub-block shading. the printed percentage carries the real precision;
    # this is a rough-at-a-glance shape, not a second source of truth.
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return ("#" * filled).ljust(width, "-")


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
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def abbreviate(n: float) -> str:
    value = float(n)
    for suffix, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= scale:
            return f"{value / scale:.1f}{suffix}".replace(".0", "")
    return str(int(value))
