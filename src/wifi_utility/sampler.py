"""Ring buffer and feature windows (SPEC §4.2, .claude/references/detector-math.md)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from .sources.base import Sample

BUFFER_SECS = 60
MIN_FILL = 0.6


@dataclass(frozen=True, slots=True)
class Features:
    ts: float
    rssi_std: float
    rssi_range: float
    rssi_mad: float
    delta_energy: float
    tx_rate_changes: float
    n: int

    def get(self, name: str) -> float:
        return getattr(self, name)


def compute_features(ts: float, rssi: np.ndarray, tx: np.ndarray) -> Features:
    med = np.median(rssi)
    diffs = np.diff(rssi)
    return Features(
        ts=ts,
        rssi_std=float(np.std(rssi)),
        rssi_range=float(rssi.max() - rssi.min()),
        rssi_mad=float(np.median(np.abs(rssi - med))),
        delta_energy=float(np.mean(diffs**2)) if len(diffs) else 0.0,
        tx_rate_changes=float(np.count_nonzero(np.diff(tx))),
        n=len(rssi),
    )


class Sampler:
    def __init__(self, sample_hz: float = 5, window_secs: float = 5):
        self.sample_hz = sample_hz
        self.window_secs = window_secs
        self._buf: deque[Sample] = deque()

    def add(self, sample: Sample) -> None:
        self._buf.append(sample)
        cutoff = sample.ts - BUFFER_SECS
        while self._buf and self._buf[0].ts < cutoff:
            self._buf.popleft()

    def clear(self) -> None:
        self._buf.clear()

    @property
    def latest(self) -> Sample | None:
        return self._buf[-1] if self._buf else None

    def recent(self, secs: float, now: float) -> list[Sample]:
        return [s for s in self._buf if now - secs < s.ts <= now]

    def features(self, now: float) -> Features | None:
        window = self.recent(self.window_secs, now)
        if len(window) < MIN_FILL * self.window_secs * self.sample_hz:
            return None
        rssi = np.fromiter((s.rssi_dbm for s in window), dtype=float)
        tx = np.fromiter((s.tx_rate_mbps for s in window), dtype=float)
        return compute_features(now, rssi, tx)
