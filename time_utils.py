import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")


def now_est() -> datetime:
    return datetime.now(tz=EST)


def parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Expected HH:MM, got '{s}'")
    return int(parts[0]), int(parts[1])


def time_in_window(now: datetime, start_str: str, end_str: str) -> bool:
    sh, sm = parse_hhmm(start_str)
    eh, em = parse_hhmm(end_str)
    current = now.hour * 60 + now.minute
    start = sh * 60 + sm
    end = eh * 60 + em
    if start <= end:
        return start <= current < end
    # crosses midnight
    return current >= start or current < end


def make_est_datetime(date, hhmm_str: str) -> datetime:
    h, m = parse_hhmm(hhmm_str)
    return datetime(date.year, date.month, date.day, h, m, 0, tzinfo=EST)


def parse_window(value: str) -> tuple[str, str]:
    """Parse 'HH:MM - HH:MM' into (start, end)."""
    pattern = r"^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$"
    m = re.match(pattern, value)
    if not m:
        raise ValueError(f"Expected HH:MM - HH:MM, got '{value}'")
    start, end = m.group(1).zfill(5), m.group(2).zfill(5)
    parse_hhmm(start)
    parse_hhmm(end)
    return start, end


def parse_minmax(value: str) -> tuple[float, float]:
    """Parse 'N - M' or 'N-M' into (min, max) floats."""
    pattern = r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$"
    m = re.match(pattern, value)
    if not m:
        raise ValueError(f"Expected N - M, got '{value}'")
    lo, hi = float(m.group(1)), float(m.group(2))
    if lo > hi:
        raise ValueError("Min must be <= max")
    return lo, hi
