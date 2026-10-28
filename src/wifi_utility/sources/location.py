"""Ask macOS for Location Services access, which it requires before revealing the BSSID.

A command-line process never appears in System Settings → Location Services until something
asks; this asks on behalf of the app that launched us (Terminal, iTerm, VS Code, …).
"""

from __future__ import annotations

import time

STATUS_NAMES = {
    0: "not determined",
    1: "restricted",
    2: "denied",
    3: "authorized",
    4: "authorized",
}
AUTHORIZED = {3, 4}


def location_status() -> str:
    import CoreLocation  # noqa: PLC0415 - macOS only

    return STATUS_NAMES.get(int(CoreLocation.CLLocationManager.authorizationStatus()), "unknown")


def request_location_access(timeout: float = 30.0) -> str:
    """Show the macOS permission prompt if needed and wait for an answer. Returns the status."""
    import CoreLocation  # noqa: PLC0415 - macOS only
    from Foundation import NSDate, NSRunLoop  # noqa: PLC0415

    manager = CoreLocation.CLLocationManager.alloc().init()
    status = int(manager.authorizationStatus())
    if status == 0:
        manager.requestWhenInUseAuthorization()
        manager.startUpdatingLocation()  # also triggers the prompt on older macOS
        deadline = time.monotonic() + timeout
        while status == 0 and time.monotonic() < deadline:
            # CoreLocation delivers the answer through the run loop.
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.25))
            status = int(manager.authorizationStatus())
        manager.stopUpdatingLocation()
    return STATUS_NAMES.get(status, "unknown")
