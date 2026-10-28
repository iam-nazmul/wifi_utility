"""Live samples from the Mac's own WiFi link via CoreWLAN (.claude/references/corewlan.md).

Reads only the connected link. No scanning, no capture, no root.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .base import Sample, SourceError

log = logging.getLogger(__name__)

# Used only when the caller allows a missing BSSID (recording). Fixtures use placeholders anyway.
UNKNOWN_BSSID = "00:00:00:00:00:00"

LOCATION_HELP = (
    "macOS returned no BSSID, which means this process lacks Location Services permission.\n"
    "Run `uv run wifi-utility permission` in Terminal (or iTerm) and click Allow, or enable the\n"
    "app in System Settings → Privacy & Security → Location Services, then run it again."
)


class LocationPermissionError(SourceError):
    pass


class CoreWLANSource:
    def __init__(self, clock: Callable[[], float] = time.time, allow_missing_bssid: bool = False):
        self.clock = clock
        self.allow_missing_bssid = allow_missing_bssid
        self.bssid_hidden = False
        self._iface = None

    def start(self) -> None:
        try:
            import CoreWLAN  # noqa: PLC0415 - macOS only, keep import local
        except ImportError as e:
            raise SourceError("CoreWLAN is unavailable; install pyobjc-framework-CoreWLAN") from e
        self._iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
        if self._iface is None:
            raise SourceError("no WiFi interface found")
        if not self._iface.powerOn():
            raise SourceError("WiFi is turned off")
        if self._iface.rssiValue() == 0:
            raise SourceError("WiFi is not connected to a network")
        self.bssid_hidden = not self._iface.bssid()
        if self.bssid_hidden and not self.allow_missing_bssid:
            raise LocationPermissionError(LOCATION_HELP)
        if self.bssid_hidden:
            log.warning("BSSID hidden by macOS (no Location permission); using %s", UNKNOWN_BSSID)

    def stop(self) -> None:
        self._iface = None

    def read(self) -> Sample | None:
        iface = self._iface
        if iface is None:
            raise SourceError("source not started")
        rssi = int(iface.rssiValue())
        channel = iface.wlanChannel()
        bssid = iface.bssid() or (UNKNOWN_BSSID if self.allow_missing_bssid else None)
        if rssi == 0 or channel is None or not bssid:
            return None  # not associated right now; never treat 0 as a reading
        return Sample(
            ts=self.clock(),
            rssi_dbm=rssi,
            noise_dbm=int(iface.noiseMeasurement()),
            tx_rate_mbps=float(iface.transmitRate()),
            channel=int(channel.channelNumber()),
            bssid=str(bssid).lower(),
        )
