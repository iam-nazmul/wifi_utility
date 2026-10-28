"""Calibration, motion score and state machine (SPEC §4.3, .claude/references/detector-math.md)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np

from .config import FEATURES, DetectorConfig
from .sampler import Features
from .sources.base import LinkKey

MAD_SCALE = 1.4826
MAD_FLOOR = {"rssi_std": 0.3, "rssi_mad": 0.3, "delta_energy": 0.2, "tx_rate_changes": 0.5}
MIN_CALIBRATION_WINDOWS = 60
POOR_RSSI_STD = 3.0
FAIR_RSSI_STD = 1.5
DRIFT_AFTER_SECS = 600
MAX_SCORE = 10.0
GAP_RESET_SECS = 5.0

IDLE = "idle"
MOTION = "motion"


@dataclass
class Baseline:
    bssid: str
    channel: int
    created_ts: float
    med: dict[str, float]
    mad: dict[str, float]
    n_windows: int
    mean_rssi_std: float
    quality: str

    @property
    def link(self) -> LinkKey:
        return LinkKey(self.bssid, self.channel)

    def stats_json(self) -> str:
        d = asdict(self)
        for k in ("bssid", "channel", "created_ts"):
            d.pop(k)
        return json.dumps(d)

    @classmethod
    def from_row(cls, bssid: str, channel: int, created_ts: float, stats_json: str) -> Baseline:
        return cls(bssid=bssid, channel=channel, created_ts=created_ts, **json.loads(stats_json))


def calibration_quality(n_windows: int, mean_rssi_std: float) -> str:
    if n_windows < MIN_CALIBRATION_WINDOWS or mean_rssi_std > POOR_RSSI_STD:
        return "poor"
    if mean_rssi_std > FAIR_RSSI_STD:
        return "fair"
    return "good"


class Calibrator:
    """Collects feature windows from an empty, still room and builds a Baseline."""

    def __init__(self):
        self.windows: list[Features] = []

    def add(self, f: Features) -> None:
        self.windows.append(f)

    def build(self, link: LinkKey, created_ts: float) -> Baseline:
        if not self.windows:
            raise ValueError("no feature windows collected; is the WiFi link up?")
        med, mad = {}, {}
        for name in FEATURES:
            values = np.array([w.get(name) for w in self.windows])
            m = float(np.median(values))
            med[name] = m
            mad[name] = max(float(np.median(np.abs(values - m))) * MAD_SCALE, MAD_FLOOR[name])
        mean_std = float(np.mean([w.rssi_std for w in self.windows]))
        n = len(self.windows)
        return Baseline(
            bssid=link.bssid,
            channel=link.channel,
            created_ts=created_ts,
            med=med,
            mad=mad,
            n_windows=n,
            mean_rssi_std=mean_std,
            quality=calibration_quality(n, mean_std),
        )


def confidence(margin: float, quality: str) -> str:
    if margin >= 0.5 and quality == "good":
        return "high"
    if margin >= 0.2 and quality != "poor":
        return "medium"
    return "low"


@dataclass(frozen=True)
class MotionEvent:
    start_ts: float  # score first crossed trigger
    detected_ts: float  # state changed to motion
    end_ts: float | None
    peak_score: float
    confidence: str


@dataclass(frozen=True)
class DetectorOutput:
    ts: float
    score: float
    state: str
    confidence: str
    rising: bool  # idle, but score above release (menu bar amber)
    started: MotionEvent | None = None
    ended: MotionEvent | None = None
    z: dict[str, float] = field(default_factory=dict)


class Detector:
    def __init__(self, baseline: Baseline, cfg: DetectorConfig):
        self.baseline = baseline
        self.cfg = cfg
        self.state = IDLE
        self._above_since: float | None = None
        self._below_since: float | None = None
        self._idle_since: float | None = None
        self._pending_peak = 0.0
        self._event_start = 0.0
        self._detected_ts = 0.0
        self._peak = 0.0
        self._last_ts: float | None = None

    def zscores(self, f: Features) -> dict[str, float]:
        b = self.baseline
        return {
            name: max(0.0, (f.get(name) - b.med[name]) / b.mad[name]) for name in self.cfg.weights
        }

    def score(self, z: dict[str, float]) -> float:
        w = self.cfg.weights
        total = sum(w[name] * z[name] for name in w) / sum(w.values())
        return float(min(max(total, 0.0), MAX_SCORE))

    def update(self, f: Features) -> DetectorOutput:
        cfg, ts = self.cfg, f.ts
        if self._last_ts is not None and ts - self._last_ts > GAP_RESET_SECS:
            self._above_since = self._below_since = None  # data gap: restart duration timers
        self._last_ts = ts
        if self._idle_since is None:
            self._idle_since = ts

        z = self.zscores(f)
        score = self.score(z)
        started = ended = None

        if self.state == IDLE:
            if score >= cfg.trigger:
                if self._above_since is None:
                    self._above_since = ts
                    self._pending_peak = 0.0
                self._pending_peak = max(self._pending_peak, score)
                if ts - self._above_since >= cfg.trigger_secs:
                    self.state = MOTION
                    self._event_start = self._above_since
                    self._detected_ts = ts
                    self._peak = self._pending_peak
                    self._below_since = None
                    started = MotionEvent(
                        self._event_start, ts, None, self._peak, self._event_confidence()
                    )
            else:
                self._above_since = None
            # Drift only on quiet idle windows, never while a trigger may be building.
            quiet = score < cfg.release
            if self.state == IDLE and quiet and ts - self._idle_since >= DRIFT_AFTER_SECS:
                self._drift(f)
        else:
            self._peak = max(self._peak, score)
            if score < cfg.release:
                if self._below_since is None:
                    self._below_since = ts
                if ts - self._below_since >= cfg.release_secs:
                    ended = MotionEvent(
                        self._event_start,
                        self._detected_ts,
                        self._below_since,
                        self._peak,
                        self._event_confidence(),
                    )
                    self.state = IDLE
                    self._idle_since = ts
                    self._above_since = self._below_since = None
            else:
                self._below_since = None

        if self.state == MOTION:
            conf = self._event_confidence()
        else:
            conf = confidence((cfg.trigger - score) / cfg.trigger, self.baseline.quality)
        return DetectorOutput(
            ts=ts,
            score=score,
            state=self.state,
            confidence=conf,
            rising=self.state == IDLE and score >= cfg.release,
            started=started,
            ended=ended,
            z=z,
        )

    def _event_confidence(self) -> str:
        margin = (self._peak - self.cfg.trigger) / self.cfg.trigger
        return confidence(margin, self.baseline.quality)

    def _drift(self, f: Features) -> None:
        a = self.cfg.drift_alpha
        med = self.baseline.med
        for name in FEATURES:
            med[name] = (1 - a) * med[name] + a * f.get(name)
