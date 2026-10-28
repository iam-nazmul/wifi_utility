"""The one 1 Hz display stream every screen reads (SPEC §4.7).

Message shapes are fixed per mode (.claude/references/data-formats.md). The stream rejects
anything else, so workplace screens can't receive scores or signal data by accident.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable

HOME_KEYS = frozenset(
    {
        "mode",
        "ts",
        "state",
        "score",
        "trigger",
        "release",
        "confidence",
        "calibration_quality",
        "armed",
        "signal",
        "last_event_ts",
    }
)
SIGNAL_KEYS = frozenset({"rssi_dbm", "noise_dbm", "tx_rate_mbps", "channel"})
WORKPLACE_KEYS = frozenset(
    {"mode", "zone_id", "bucket_start", "occupied", "policy_version", "collection_active"}
)
HISTORY_LEN = 120  # 2 minutes at 1 Hz


class StreamShapeError(ValueError):
    pass


def check_shape(mode: str, msg: dict) -> None:
    if msg.get("mode") != mode:
        raise StreamShapeError(f"message mode {msg.get('mode')!r} on a {mode} stream")
    allowed = HOME_KEYS if mode == "home" else WORKPLACE_KEYS
    if set(msg) != allowed:
        raise StreamShapeError(
            f"{mode} message keys must be exactly {sorted(allowed)}; "
            f"extra={sorted(set(msg) - allowed)} missing={sorted(allowed - set(msg))}"
        )
    if mode == "home" and msg["signal"] is not None and set(msg["signal"]) != SIGNAL_KEYS:
        raise StreamShapeError(f"signal keys must be {sorted(SIGNAL_KEYS)}")


class LiveStream:
    def __init__(self, mode: str):
        if mode not in ("home", "workplace"):
            raise ValueError(mode)
        self.mode = mode
        self.seq = 0
        self._latest: dict | None = None
        self._history: deque[dict] = deque(maxlen=HISTORY_LEN)
        self._subscribers: list[Callable[[dict], None]] = []
        self._lock = threading.Lock()

    def publish(self, msg: dict) -> None:
        check_shape(self.mode, msg)
        with self._lock:
            self.seq += 1
            self._latest = msg
            self._history.append(msg)
            subscribers = list(self._subscribers)
        for fn in subscribers:
            fn(msg)

    def subscribe(self, fn: Callable[[dict], None]) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def latest(self) -> tuple[int, dict | None]:
        with self._lock:
            return self.seq, self._latest

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)
