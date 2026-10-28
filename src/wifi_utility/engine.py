"""Pipeline: SignalSource → Sampler → Detector → Mode → (store | notifier | stream). SPEC §4."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable

from .config import Config
from .detector import Baseline, Calibrator, Detector, DetectorOutput
from .display.stream import LiveStream
from .modes.home import HomeMode
from .modes.workplace import WorkplaceMode
from .sampler import Sampler
from .sources.base import LinkKey, Sample, SignalSource, SourceError
from .store import Store

log = logging.getLogger(__name__)

NO_SIGNAL_SECS = 3
PRUNE_EVERY_SECS = 86400


class CalibrationError(RuntimeError):
    pass


class Engine:
    def __init__(
        self,
        cfg: Config,
        source: SignalSource | None = None,
        *,
        store: Store | None = None,
        stream: LiveStream | None = None,
        mode: HomeMode | WorkplaceMode | None = None,
        baseline: Baseline | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.source = source
        self.store = store
        self.stream = stream
        self.mode = mode
        self.clock = clock
        self.sampler = Sampler(cfg.sample_hz, cfg.window_secs)
        self.link: LinkKey | None = None
        self.detector: Detector | None = None
        self.last_out: DetectorOutput | None = None
        self.last_event_ts: float | None = None
        self._given_baseline = baseline
        self._last_prune: float | None = None

    @property
    def calibration_quality(self) -> str | None:
        return self.detector.baseline.quality if self.detector else None

    def set_baseline(self, baseline: Baseline) -> None:
        self._given_baseline = baseline
        if self.link == baseline.link:
            self.detector = Detector(baseline, self.cfg.detector)

    def _baseline_for(self, link: LinkKey) -> Baseline | None:
        if self._given_baseline and self._given_baseline.link == link:
            return self._given_baseline
        return self.store.get_baseline(link) if self.store else None

    def add_sample(self, s: Sample) -> None:
        if s.link != self.link:
            if self.link is not None:
                # Never mix baselines across links (SPEC §4.1).
                log.warning("link changed %s → %s; switching baseline", self.link, s.link)
                if self.store:
                    self.store.audit(s.ts, "engine", "link_changed", f"channel={s.channel}")
            self.link = s.link
            self.sampler.clear()
            baseline = self._baseline_for(s.link)
            self.detector = Detector(baseline, self.cfg.detector) if baseline else None
            self.last_out = None
        self.sampler.add(s)
        if self.mode is not None:
            self.mode.on_sample(s)

    def tick(self, now: float) -> DetectorOutput | None:
        out = None
        f = self.sampler.features(now)
        if f is not None and self.detector is not None:
            out = self.detector.update(f)
            if out.started:
                self.last_event_ts = out.started.start_ts
        self.last_out = out

        if isinstance(self.mode, HomeMode):
            if out is not None:
                self.mode.on_output(out, now, self.calibration_quality)
        elif isinstance(self.mode, WorkplaceMode):
            self.mode.on_output(out, now)

        if self.stream is not None:
            self.stream.publish(self.message(now, out))
        self._maybe_prune(now)
        return out

    def message(self, now: float, out: DetectorOutput | None) -> dict:
        if isinstance(self.mode, WorkplaceMode):
            return self.mode.message(now)
        latest = self.sampler.latest
        has_signal = latest is not None and now - latest.ts <= NO_SIGNAL_SECS
        if not has_signal:
            state = "no_signal"
        elif self.detector is None:
            state = "uncalibrated"
        elif out is None:
            state = "warming_up"
        else:
            state = out.state
        armed = isinstance(self.mode, HomeMode) and self.mode.is_armed(
            now, self.calibration_quality
        )
        signal = None
        if has_signal:
            signal = {
                "rssi_dbm": latest.rssi_dbm,
                "noise_dbm": latest.noise_dbm,
                "tx_rate_mbps": latest.tx_rate_mbps,
                "channel": latest.channel,
            }
        return {
            "mode": "home",
            "ts": now,
            "state": state,
            "score": round(out.score, 2) if out else None,
            "trigger": self.cfg.detector.trigger,
            "release": self.cfg.detector.release,
            "confidence": out.confidence if out else None,
            "calibration_quality": self.calibration_quality,
            "armed": armed,
            "signal": signal,
            "last_event_ts": self.last_event_ts,
        }

    def _maybe_prune(self, now: float) -> None:
        if self.store is None:
            return
        if self._last_prune is not None and now - self._last_prune < PRUNE_EVERY_SECS:
            return
        self._last_prune = now
        days = (
            self.mode.retention_days
            if isinstance(self.mode, WorkplaceMode)
            else self.cfg.workplace.retention_days
        )
        self.store.prune(now, days)

    def run(self, stop: threading.Event) -> None:
        """Poll the source at sample_hz and tick once per second until `stop` is set."""
        if self.source is None:
            raise RuntimeError("engine has no source")
        self.source.start()
        period = 1.0 / self.cfg.sample_hz
        next_tick = math.floor(self.clock()) + 1
        try:
            while not stop.is_set():
                try:
                    s = self.source.read()
                except SourceError as e:
                    log.warning("source read failed: %s", e)
                    s = None
                if s is not None:
                    self.add_sample(s)
                now = self.clock()
                if now >= next_tick:
                    self.tick(now)
                    next_tick = math.floor(now) + 1
                stop.wait(period)
        finally:
            self.source.stop()


def calibrate_samples(samples: list[Sample], cfg: Config, secs: float | None = None) -> Baseline:
    """Build a baseline from the first `secs` of samples (empty, still room)."""
    if not samples:
        raise CalibrationError("no samples; is WiFi connected?")
    secs = secs or cfg.detector.calibration_secs
    link = samples[0].link
    end = samples[0].ts + secs
    sampler = Sampler(cfg.sample_hz, cfg.window_secs)
    cal = Calibrator()
    next_tick = math.floor(samples[0].ts) + 1
    for s in samples:
        if s.ts > end:
            break
        if s.link != link:
            raise CalibrationError("router link changed during calibration; run it again")
        while s.ts > next_tick:
            f = sampler.features(next_tick)
            if f is not None:
                cal.add(f)
            next_tick += 1
        sampler.add(s)
    f = sampler.features(next_tick)
    if f is not None:
        cal.add(f)
    try:
        return cal.build(link, samples[0].ts)
    except ValueError as e:
        raise CalibrationError(str(e)) from e


def collect_live(
    source: SignalSource,
    secs: float,
    sample_hz: float,
    clock: Callable[[], float] = time.time,
    on_progress: Callable[[float, Sample | None], None] | None = None,
    stop: threading.Event | None = None,
) -> list[Sample]:
    stop = stop or threading.Event()
    samples: list[Sample] = []
    source.start()
    try:
        t0 = clock()
        while not stop.is_set() and clock() - t0 < secs:
            s = source.read()
            if s is not None:
                samples.append(s)
            if on_progress:
                on_progress(clock() - t0, s)
            stop.wait(1.0 / sample_hz)
    finally:
        source.stop()
    return samples


def replay(
    samples: list[Sample],
    cfg: Config,
    baseline: Baseline,
    stream: LiveStream | None = None,
) -> list[DetectorOutput]:
    """Run the detector offline over recorded samples."""
    return drive(Engine(cfg, baseline=baseline, stream=stream), samples)


def drive(engine: Engine, samples: list[Sample]) -> list[DetectorOutput]:
    """Feed recorded samples through an engine, ticking on whole seconds of sample time."""
    outs: list[DetectorOutput] = []
    if not samples:
        return outs
    next_tick = math.floor(samples[0].ts) + 1
    for s in samples:
        while s.ts > next_tick:
            out = engine.tick(next_tick)
            if out is not None:
                outs.append(out)
            next_tick += 1
        engine.add_sample(s)
    out = engine.tick(next_tick)
    if out is not None:
        outs.append(out)
    return outs
