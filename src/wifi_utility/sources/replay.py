"""Replay recorded samples from CSV (format: .claude/references/data-formats.md)."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from .base import Sample, SourceError

COLUMNS = ["ts", "rssi_dbm", "noise_dbm", "tx_rate_mbps", "channel", "bssid"]


def read_samples_csv(path: str | Path) -> list[Sample]:
    with Path(path).open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise SourceError(f"{path}: expected columns {COLUMNS}, got {reader.fieldnames}")
        return [
            Sample(
                ts=float(row["ts"]),
                rssi_dbm=int(row["rssi_dbm"]),
                noise_dbm=int(row["noise_dbm"]),
                tx_rate_mbps=float(row["tx_rate_mbps"]),
                channel=int(row["channel"]),
                bssid=row["bssid"].lower(),
            )
            for row in reader
        ]


def write_samples_csv(path: str | Path, samples: Iterable[Sample]) -> int:
    count = 0
    with Path(path).open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for s in samples:
            writer.writerow(
                [f"{s.ts:.3f}", s.rssi_dbm, s.noise_dbm, s.tx_rate_mbps, s.channel, s.bssid]
            )
            count += 1
    return count


class ReplaySource:
    """Yields recorded samples in order. `read()` returns None once exhausted."""

    def __init__(self, path_or_samples: str | Path | list[Sample]):
        if isinstance(path_or_samples, list):
            self.samples = path_or_samples
        else:
            self.samples = read_samples_csv(path_or_samples)
        self._i = 0

    def start(self) -> None:
        self._i = 0

    def stop(self) -> None:
        pass

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self.samples)

    def read(self) -> Sample | None:
        if self.exhausted:
            return None
        sample = self.samples[self._i]
        self._i += 1
        return sample
