"""Home Security mode (SPEC §5)."""

from __future__ import annotations

from datetime import datetime

from ..config import Config
from ..detector import DetectorOutput
from ..notifier import Notifier
from ..schedule import in_any, parse_windows
from ..sources.base import Sample
from ..store import Store

ARMED_AT_KEY = "home.armed_at"


class ArmError(RuntimeError):
    pass


def arm(store: Store, now: float, actor: str, calibration_quality: str | None) -> float:
    """Arm alerts; returns the arm time. Arming again keeps the original time, so repeated
    clicks during the exit delay don't keep pushing it back."""
    if calibration_quality is None:
        raise ArmError("not calibrated; run `wifi-utility calibrate` with the room empty and still")
    if calibration_quality == "poor":
        raise ArmError("calibration quality is poor; recalibrate before arming")
    existing = armed_at(store)
    if existing is not None:
        return existing
    store.kv_set(ARMED_AT_KEY, repr(now))
    store.audit(now, actor, "armed")
    return now


def disarm(store: Store, now: float, actor: str) -> None:
    store.kv_set(ARMED_AT_KEY, "")
    store.audit(now, actor, "disarmed")


def armed_at(store: Store) -> float | None:
    value = store.kv_get(ARMED_AT_KEY, "")
    return float(value) if value else None


def arm_state(store: Store, now: float, exit_delay_secs: float) -> tuple[str, int]:
    """Manual arm state: ("off", 0), ("arming", seconds left) or ("on", 0)."""
    at = armed_at(store)
    if at is None:
        return "off", 0
    left = at + exit_delay_secs - now
    return ("arming", max(1, round(left))) if left > 0 else ("on", 0)


def describe_arm(store: Store, now: float, exit_delay_secs: float) -> str:
    state, left = arm_state(store, now, exit_delay_secs)
    return {"off": "off", "on": "on"}.get(state, f"arming… active in {left}s")


class HomeMode:
    name = "home"

    def __init__(self, cfg: Config, store: Store, notifier: Notifier):
        self.cfg = cfg
        self.store = store
        self.notifier = notifier
        self.windows = parse_windows(cfg.home.schedule)
        self.last_event_ts: float | None = None
        self._event_id: int | None = None

    def is_armed(self, now: float, calibration_quality: str | None) -> bool:
        if calibration_quality in (None, "poor"):
            return False
        at = armed_at(self.store)
        if at is not None and now >= at + self.cfg.home.exit_delay_secs:
            return True
        return in_any(self.windows, datetime.fromtimestamp(now))

    def on_sample(self, sample: Sample) -> None:
        if self.cfg.home.debug_capture:
            self.store.add_sample(sample)

    def on_output(self, out: DetectorOutput, now: float, calibration_quality: str | None) -> None:
        if out.started:
            ev = out.started
            self._event_id = self.store.start_event("home", ev)
            self.last_event_ts = ev.start_ts
            if self.is_armed(now, calibration_quality):
                self.notifier.alert(
                    key=("home", self._event_id),
                    ts=now,
                    title="Motion detected",
                    body=f"Peak score {ev.peak_score:.1f}, confidence {ev.confidence}",
                    payload={
                        "event": "motion",
                        "start_ts": ev.start_ts,
                        "peak_score": round(ev.peak_score, 2),
                        "confidence": ev.confidence,
                    },
                )
        if out.ended and self._event_id is not None:
            self.store.end_event(self._event_id, out.ended)
            self._event_id = None
