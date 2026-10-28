"""Menu bar indicator (SPEC §4.7a). rumps must own the main thread."""

from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable
from datetime import datetime

from ..display.stream import LiveStream

ICONS = {"idle": "⚪", "rising": "🟠", "motion": "🔴", "off": "⊘"}


def home_title(m: dict | None) -> str:
    if not m or m["state"] in ("uncalibrated", "no_signal", "warming_up"):
        return ICONS["off"]
    if m["calibration_quality"] == "poor":
        return ICONS["off"]
    if m["state"] == "motion":
        return ICONS["motion"]
    if m["score"] is not None and m["score"] >= m["release"]:
        return ICONS["rising"]
    return ICONS["idle"]


def workplace_title(m: dict | None) -> str:
    if not m or not m["collection_active"]:
        return f"{ICONS['off']} Not collecting"
    return "🔵 Occupied" if m["occupied"] else "⚪ Vacant"


def home_lines(m: dict | None) -> list[str]:
    if not m:
        return ["Starting…"]
    last = (
        datetime.fromtimestamp(m["last_event_ts"]).strftime("%H:%M:%S")
        if m["last_event_ts"]
        else "none"
    )
    score = f"{m['score']:.1f}" if m["score"] is not None else "–"
    return [
        f"State: {m['state']}",
        f"Score: {score}  (trigger {m['trigger']})",
        f"Confidence: {m['confidence'] or '–'}",
        f"Calibration: {m['calibration_quality'] or 'none'}",
        f"Armed: {'yes' if m['armed'] else 'no'}",
        f"Last motion: {last}",
    ]


def workplace_lines(m: dict | None) -> list[str]:
    if not m:
        return ["Starting…"]
    if not m["collection_active"]:
        return ["Not collecting (no effective policy)"]
    return [
        f"Zone {m['zone_id']}: {'Occupied' if m['occupied'] else 'Vacant'} (this 15-min period)",
        f"Collection active under policy v{m['policy_version']}",
    ]


def run_menubar(
    stream: LiveStream,
    live_url: str,
    on_quit: Callable[[], None],
    on_arm: Callable[[bool], str] | None = None,
    should_exit: Callable[[], bool] | None = None,
) -> None:
    import rumps

    workplace = stream.mode == "workplace"
    app = rumps.App("WiFi Motion", title=ICONS["off"], quit_button=None)
    n_lines = 2 if workplace else 6
    info = [rumps.MenuItem("") for _ in range(n_lines)]

    def open_live(_):
        webbrowser.open(live_url)

    def toggle_arm(sender):
        if on_arm is None:
            return
        _, m = stream.latest()
        msg = on_arm(not (m and m["armed"]))
        rumps.notification("WiFi Motion", "", msg)

    def quit_app(_):
        on_quit()
        rumps.quit_application()

    menu: list = [*info, None, rumps.MenuItem("Open live view", callback=open_live)]
    if not workplace and on_arm is not None:
        menu.append(rumps.MenuItem("Arm / disarm", callback=toggle_arm))
    menu += [None, rumps.MenuItem("Quit", callback=quit_app)]
    app.menu = menu

    def update(_):
        if should_exit and should_exit():
            rumps.quit_application()
            return
        _, m = stream.latest()
        if workplace:
            app.title, lines = workplace_title(m), workplace_lines(m)
        else:
            app.title, lines = home_title(m), home_lines(m)
        for item, text in zip(info, lines + [""] * n_lines, strict=False):
            item.title = text

    rumps.Timer(update, 1).start()
    try:
        app.run()
    finally:
        on_quit()
        time.sleep(0.2)
