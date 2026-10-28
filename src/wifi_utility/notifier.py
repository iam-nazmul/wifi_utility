"""Alerts: macOS notification, optional webhook and ntfy-style push (SPEC §4.5)."""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import urllib.request
from collections.abc import Callable

log = logging.getLogger(__name__)

MIN_GAP_SECS = 120


def _applescript_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_notify(title: str, body: str) -> None:
    script = (
        f"display notification {_applescript_quote(body)} "
        f'with title {_applescript_quote(title)} sound name "Funk"'
    )
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=10)


def post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10).close()


def post_text(url: str, title: str, body: str) -> None:
    req = urllib.request.Request(url, data=body.encode(), headers={"Title": title}, method="POST")
    urllib.request.urlopen(req, timeout=10).close()


class Notifier:
    """Rate-limited: at most one alert per event and one per 2 minutes overall."""

    def __init__(
        self,
        webhook_url: str = "",
        ntfy_url: str = "",
        desktop: Callable[[str, str], None] | None = macos_notify,
        background: bool = True,
    ):
        self.webhook_url = webhook_url
        self.ntfy_url = ntfy_url
        self.desktop = desktop
        self.background = background
        self._last_alert_ts: float | None = None
        self._alerted_keys: set = set()
        self.sent: list[dict] = []  # what was sent, for tests and the dashboard

    def alert(self, key, ts: float, title: str, body: str, payload: dict) -> bool:
        if key in self._alerted_keys:
            return False
        if self._last_alert_ts is not None and ts - self._last_alert_ts < MIN_GAP_SECS:
            return False
        self._alerted_keys.add(key)
        self._last_alert_ts = ts
        payload = {**payload, "host": socket.gethostname()}
        self.sent.append(payload)
        if self.background:
            threading.Thread(target=self._send, args=(title, body, payload), daemon=True).start()
        else:
            self._send(title, body, payload)
        return True

    def _send(self, title: str, body: str, payload: dict) -> None:
        for name, fn in (
            ("desktop", lambda: self.desktop and self.desktop(title, body)),
            ("webhook", lambda: self.webhook_url and post_json(self.webhook_url, payload)),
            ("ntfy", lambda: self.ntfy_url and post_text(self.ntfy_url, title, body)),
        ):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - one failing channel must not stop the others
                log.warning("%s alert failed: %s", name, e)
