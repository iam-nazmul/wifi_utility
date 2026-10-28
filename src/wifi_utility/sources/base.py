"""SignalSource interface and Sample type (SPEC §4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Protocol


class SourceError(RuntimeError):
    pass


class LinkKey(NamedTuple):
    bssid: str
    channel: int


@dataclass(frozen=True, slots=True)
class Sample:
    ts: float
    rssi_dbm: int
    noise_dbm: int
    tx_rate_mbps: float
    channel: int
    bssid: str

    @property
    def link(self) -> LinkKey:
        return LinkKey(self.bssid, self.channel)


class SignalSource(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read(self) -> Sample | None:
        """Return the current sample, or None if no valid reading is available right now."""
        ...
