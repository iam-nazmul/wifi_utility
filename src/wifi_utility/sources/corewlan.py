"""Live samples from the Mac's own WiFi link via CoreWLAN (.claude/references/corewlan.md).

Reads only the connected link. No scanning, no capture, no root.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .base import Sample, SourceError

LOCATION_HELP = (
    "macOS returned no BSSID, which means this process lacks Location Services permission.\n"
    "Grant it in System Settings → Privacy & Security → Location Services for the app running\n"
    "this command (Terminal, iTerm, VS Code, …), then run it again."
)


class LocationPermissionError(SourceError):
    pass


class CoreWLANSource:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
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
        if not self._iface.bssid():
            raise LocationPermissionError(LOCATION_HELP)

    def stop(self) -> None:
        self._iface = None

    def read(self) -> Sample | None:
        iface = self._iface
        if iface is None:
            raise SourceError("source not started")
        rssi = int(iface.rssiValue())
        channel = iface.wlanChannel()
        bssid = iface.bssid()
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
