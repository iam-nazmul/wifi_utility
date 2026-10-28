"""Weekly time windows like "mon-fri 09:00-17:00", "sat,sun 10:00-14:00", "daily 22:00-06:00"."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WINDOW_RE = re.compile(r"^\s*([a-z,\-]+)\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    days: frozenset[int]
    start_min: int
    end_min: int

    def contains(self, dt: datetime) -> bool:
        day, minute = dt.weekday(), dt.hour * 60 + dt.minute
        if self.start_min < self.end_min:
            return day in self.days and self.start_min <= minute < self.end_min
        # Overnight window: started yesterday or starts today.
        yesterday = (day - 1) % 7
        return (day in self.days and minute >= self.start_min) or (
            yesterday in self.days and minute < self.end_min
        )


def _parse_days(spec: str) -> frozenset[int]:
    if spec == "daily":
        return frozenset(range(7))
    days: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                raise ScheduleError(f"unknown day range {part!r}")
            i, j = DAYS.index(a), DAYS.index(b)
            days.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        elif part in DAYS:
            days.add(DAYS.index(part))
        else:
            raise ScheduleError(f"unknown day {part!r}")
    return frozenset(days)


def parse_window(text: str) -> Window:
    m = WINDOW_RE.match(text.lower())
    if not m:
        raise ScheduleError(f"can't parse schedule {text!r}; expected e.g. 'mon-fri 09:00-17:00'")
    days, h1, m1, h2, m2 = m.groups()
    start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
    if not (0 <= start < 1440 and 0 < end <= 1440 and int(m1) < 60 and int(m2) < 60):
        raise ScheduleError(f"invalid time in {text!r}")
    if start == end:
        raise ScheduleError(f"empty window {text!r}")
    return Window(_parse_days(days), start, end)


def parse_windows(texts: list[str]) -> list[Window]:
    return [parse_window(t) for t in texts]


def in_any(windows: list[Window], dt: datetime) -> bool:
    return any(w.contains(dt) for w in windows)
