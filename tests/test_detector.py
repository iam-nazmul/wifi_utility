import pytest
from synth import generate, walking

from wifi_utility.config import DetectorConfig
from wifi_utility.detector import (
    DRIFT_AFTER_SECS,
    IDLE,
    MOTION,
    Baseline,
    Calibrator,
    Detector,
    calibration_quality,
)
from wifi_utility.engine import calibrate_samples, replay
from wifi_utility.evaluate import evaluate
from wifi_utility.sampler import Features

ONE = {"rssi_std": 1.0, "rssi_mad": 1.0, "delta_energy": 1.0, "tx_rate_changes": 1.0}


def unit_baseline(quality="good"):
    zero = dict.fromkeys(ONE, 0.0)
    return Baseline("00:00:00:00:00:01", 36, 0.0, zero, dict(ONE), 180, 0.5, quality)


def feat(ts, score):
    # With a unit baseline and equal weights, every feature at `score` gives that score.
    return Features(ts, score, 0.0, score, score, score, 25)


def run(det, scores, t0=0.0):
    return [det.update(feat(t0 + i, sc)) for i, sc in enumerate(scores)]


@pytest.fixture
def det():
    return Detector(unit_baseline(), DetectorConfig(weights=dict(ONE)))


def test_score_is_weighted_mean_clipped(det):
    assert det.update(feat(0, 4.0)).score == pytest.approx(4.0)
    assert det.update(feat(1, 50.0)).score == 10.0


def test_trigger_requires_duration(det):
    outs = run(det, [5, 5, 5])  # t=0,1,2 → only 2 s above trigger
    assert all(o.state == IDLE for o in outs)
    o = det.update(feat(3, 5))  # 3 s above trigger
    assert o.state == MOTION and o.started is not None
    assert o.started.start_ts == 0


def test_brief_spike_does_not_trigger(det):
    outs = run(det, [5, 5, 1, 5, 5, 1, 5, 5])
    assert all(o.state == IDLE for o in outs)


def test_rising_flag_between_release_and_trigger(det):
    assert det.update(feat(0, 2.5)).rising
    assert not det.update(feat(1, 1.0)).rising


def test_release_hysteresis(det):
    run(det, [5] * 4)
    assert det.state == MOTION
    outs = run(det, [3.0] * 20, t0=4)  # between release and trigger: stays in motion
    assert all(o.state == MOTION for o in outs)
    outs = run(det, [1, 1, 1, 5] + [1] * 9, t0=24)  # dip interrupted, then 9 s below
    assert all(o.state == MOTION for o in outs)
    assert det.update(feat(37, 1)).state == MOTION  # 9 s below
    o = det.update(feat(38, 1))  # 10 s below release since t=28
    assert o.state == IDLE and o.ended is not None
    assert o.ended.end_ts == 28 and o.ended.peak_score == 5


def test_drift_only_while_idle(det):
    run(det, [0.5] * (DRIFT_AFTER_SECS - 1))
    assert det.baseline.med["rssi_std"] == 0.0  # not idle long enough yet
    det.update(feat(DRIFT_AFTER_SECS, 0.5))
    drifted = det.baseline.med["rssi_std"]
    assert drifted > 0.0
    run(det, [8] * 30, t0=DRIFT_AFTER_SECS + 1)
    assert det.state == MOTION
    assert det.baseline.med["rssi_std"] == drifted  # frozen during motion


def test_gap_resets_timers(det):
    run(det, [5, 5])
    o = det.update(feat(20, 5))  # 18 s gap: must not count as continuous
    assert o.state == IDLE


def test_confidence_levels():
    cfg = DetectorConfig(weights=dict(ONE))
    strong = Detector(unit_baseline("good"), cfg)
    run(strong, [9] * 4)
    assert strong.update(feat(4, 9)).confidence == "high"
    weak = Detector(unit_baseline("poor"), cfg)
    run(weak, [9] * 4)
    assert weak.update(feat(4, 9)).confidence == "low"


def test_calibration_quality_rules():
    assert calibration_quality(10, 0.5) == "poor"
    assert calibration_quality(180, 3.5) == "poor"
    assert calibration_quality(180, 2.0) == "fair"
    assert calibration_quality(180, 0.6) == "good"
    with pytest.raises(ValueError):
        Calibrator().build(None, 0)


def test_baseline_roundtrip():
    b = unit_baseline()
    again = Baseline.from_row(b.bssid, b.channel, b.created_ts, b.stats_json())
    assert again == b


# --- synthetic acceptance (model data, not a real room; see tests/synth.py) -----------------


def test_synthetic_walking_meets_targets(cfg):
    samples, labels = walking()
    baseline = calibrate_samples(samples, cfg, 180)
    assert baseline.quality == "good"
    report = evaluate(replay(samples, cfg, baseline), labels, grace_secs=15)
    on_path = report.by_label["walk_on_path"]
    assert on_path["rate"] >= 0.9
    assert on_path["median_latency"] <= 10
    assert report.false_events == 0


def test_synthetic_empty_room_eight_hours(cfg):
    samples, _ = generate(8 * 3600, seed=7)
    baseline = calibrate_samples(samples, cfg, 180)
    events = [o for o in replay(samples, cfg, baseline) if o.started]
    assert len(events) <= 1


def test_synthetic_fan_running_when_calibrated_no_events(cfg):
    noisy, _ = generate(2 * 3600, noise=0.9, seed=4)
    baseline = calibrate_samples(noisy, cfg, 180)
    events = [o for o in replay(noisy, cfg, baseline) if o.started]
    assert len(events) <= 1


@pytest.mark.xfail(
    strict=True,
    reason="Known limitation: a ~50% noise increase after calibration (e.g. a fan switched on) "
    "causes false events; recalibrate in the new conditions. Tune on real fixtures.",
)
def test_synthetic_fan_switched_on_after_calibration(cfg):
    calm, _ = generate(300, seed=3)
    noisy, _ = generate(3600, noise=0.9, seed=4, t0=calm[-1].ts + 0.2)
    baseline = calibrate_samples(calm, cfg, 180)
    events = [o for o in replay(noisy, cfg, baseline) if o.started]
    assert len(events) <= 1
