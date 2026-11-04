"""Session grid helpers: legal bar-end labels from real trading hours.

No fabricated shared sessions: grids come from each contract's `trading_hours`
string (e.g. "21:01-02:30,09:01-10:15,10:31-11:30,13:31-15:00"), where both
endpoints are already bar-end labels. Overnight segments wrap midnight.
"""
from __future__ import annotations

from typing import Iterable


def _to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_label(minute: int) -> str:
    minute %= 24 * 60
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_hours(hours: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in hours.split(","):
        seg = seg.strip()
        if not seg:
            continue
        a, b = seg.split("-")
        out.append((a.strip(), b.strip()))
    return out


def segment_labels(start: str, end: str) -> list[str]:
    """All bar-end labels from start to end inclusive; wraps past midnight."""
    a, b = _to_min(start), _to_min(end)
    labels = []
    cur = a
    while True:
        labels.append(_to_label(cur))
        if cur % (24 * 60) == b:
            break
        cur += 1
        if len(labels) > 24 * 60:
            raise ValueError(f"segment too long: {start}-{end}")
    return labels


def session_grid(hours: str) -> list[str]:
    grid: list[str] = []
    for a, b in parse_hours(hours):
        grid.extend(segment_labels(a, b))
    return grid


def window_grid(hours: str, start: str, end: str) -> list[str]:
    """Legal labels of `hours` inside the requested window [start, end].

    Supports overnight windows where start > end (e.g. 21:00 -> 02:30).
    """
    labels = session_grid(hours)
    a, b = _to_min(start), _to_min(end)
    out = []
    for lab in labels:
        m = _to_min(lab)
        if a <= b:
            if a <= m <= b:
                out.append(lab)
        else:  # window wraps midnight
            if m >= a or m <= b:
                out.append(lab)
    # keep session order, de-duplicated
    seen = set()
    ordered = []
    for lab in out:
        if lab not in seen:
            seen.add(lab)
            ordered.append(lab)
    return ordered


def trading_days(start: str, end: str, holidays: Iterable[str] = ()) -> list[str]:
    """Calendar days in range as YYYYMMDD (caller intersects with real data)."""
    from datetime import date, timedelta

    y, m, d = int(start[:4]), int(start[4:6]), int(start[6:8])
    y2, m2, d2 = int(end[:4]), int(end[4:6]), int(end[6:8])
    cur, last = date(y, m, d), date(y2, m2, d2)
    skip = set(holidays)
    out = []
    while cur <= last:
        key = cur.strftime("%Y%m%d")
        if cur.weekday() < 5 and key not in skip:
            out.append(key)
        cur += timedelta(days=1)
    return out


def periods(hours: str, gap_minutes: int = 60) -> list[list[tuple[str, str]]]:
    """Group session segments into trading periods separated by long breaks."""
    segs = parse_hours(hours)
    out: list[list[tuple[str, str]]] = []
    for seg in segs:
        if out:
            prev_end = _to_min(out[-1][-1][1])
            gap = (_to_min(seg[0]) - prev_end) % (24 * 60)
            if gap <= gap_minutes:
                out[-1].append(seg)
                continue
        out.append([seg])
    return out


def window_grid_freq(hours: str, start: str, end: str, freq: str = "1m") -> list[str]:
    """Expected bar-end labels for a requested frequency (1m/5m/15m/60m).

    Bars close on clock multiples of the frequency, and the last bar of each
    trading period closes at the period end. A short intra-period break does not
    close a bar: RB's 60m bar labelled 11:00 covers 10:01-10:15 and 10:31-11:00,
    there is no 10:15 bar, and 11:30 closes the morning period.
    """
    n = {"1m": 1, "5m": 5, "15m": 15, "60m": 60}.get(freq, 1)
    out: list[str] = []
    for group in periods(hours):
        labels: list[str] = []
        for a, b in group:
            labels.extend(segment_labels(a, b))
        period_close = labels[-1] if labels else None
        for lab in labels:
            if n == 1 or _to_min(lab) % n == 0 or lab == period_close:
                out.append(lab)
    a, b = _to_min(start), _to_min(end)
    keep = []
    for lab in out:
        m = _to_min(lab)
        if (a <= b and a <= m <= b) or (a > b and (m >= a or m <= b)):
            keep.append(lab)
    seen, ordered = set(), []
    for lab in keep:
        if lab not in seen:
            seen.add(lab)
            ordered.append(lab)
    return ordered
