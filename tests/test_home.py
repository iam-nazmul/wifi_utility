from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from synth import walking

from wifi_utility.dashboard.app import DashboardContext, create_app
from wifi_utility.display.stream import LiveStream
from wifi_utility.engine import Engine, calibrate_samples, drive
from wifi_utility.modes import home
from wifi_utility.notifier import MIN_GAP_SECS, Notifier
from wifi_utility.schedule import ScheduleError, in_any, parse_window


def notifier():
    return Notifier(desktop=None, background=False)


def home_engine(cfg, store, n):
    mode = home.HomeMode(cfg, store, n)
    samples, _ = walking(n_passes=6)
    baseline = calibrate_samples(samples, cfg, 180)
    store.save_baseline(baseline)
    stream = LiveStream("home")
    return Engine(cfg, store=store, stream=stream, mode=mode), samples, stream


def test_arm_requires_good_calibration(home_store):
    with pytest.raises(home.ArmError, match="not calibrated"):
        home.arm(home_store, 0, "t", None)
    with pytest.raises(home.ArmError, match="poor"):
        home.arm(home_store, 0, "t", "poor")
    home.arm(home_store, 0, "t", "good")
    assert home.armed_at(home_store) == 0
    home.disarm(home_store, 1, "t")
    assert home.armed_at(home_store) is None


def test_exit_delay(cfg, home_store):
    mode = home.HomeMode(cfg, home_store, notifier())
    home.arm(home_store, 1000, "t", "good")
    assert not mode.is_armed(1059, "good")
    assert mode.is_armed(1060, "good")
    assert not mode.is_armed(1060, "poor")


def test_events_logged_but_alerts_only_when_armed(cfg, home_store):
    n = notifier()
    engine, samples, _ = home_engine(cfg, home_store, n)
    drive(engine, samples)
    assert len(home_store.events()) >= 5
    assert n.sent == []  # disarmed

    n2 = notifier()
    home_store2_engine, samples2, _ = home_engine(cfg, home_store, n2)
    home.arm(home_store, samples2[0].ts - 120, "t", "good")
    drive(home_store2_engine, samples2)
    assert n2.sent  # alerted
    assert set(n2.sent[0]) == {"event", "start_ts", "peak_score", "confidence", "host"}
    gaps = [b["start_ts"] - a["start_ts"] for a, b in zip(n2.sent, n2.sent[1:], strict=False)]
    assert all(g >= MIN_GAP_SECS - 10 for g in gaps)  # rate limited


def test_notifier_rate_limit():
    n = notifier()
    assert n.alert("a", 0, "t", "b", {})
    assert not n.alert("a", 500, "t", "b", {})  # same event
    assert not n.alert("b", 60, "t", "b", {})  # within 2 minutes
    assert n.alert("c", 121, "t", "b", {})


def test_debug_capture_stores_samples(cfg, home_store):
    cfg.home.debug_capture = True
    engine, samples, _ = home_engine(cfg, home_store, notifier())
    drive(engine, samples[:50])
    import sqlite3

    assert sqlite3.connect(home_store.path).execute("SELECT COUNT(*) FROM samples").fetchone()[0]


def test_schedule_windows():
    w = parse_window("mon-fri 09:00-17:00")
    assert w.contains(datetime(2026, 11, 17, 9, 0))  # Tuesday
    assert not w.contains(datetime(2026, 11, 17, 17, 0))
    assert not w.contains(datetime(2026, 11, 21, 10, 0))  # Saturday
    night = parse_window("daily 22:00-06:00")
    assert night.contains(datetime(2026, 11, 17, 23, 0))
    assert night.contains(datetime(2026, 11, 18, 5, 59))
    assert in_any([w, night], datetime(2026, 11, 17, 3, 0))
    with pytest.raises(ScheduleError):
        parse_window("weekdays 9-5")


def test_dashboard_arm_and_events(cfg, home_store):
    engine, samples, stream = home_engine(cfg, home_store, notifier())
    drive(engine, samples)
    ctx = DashboardContext(cfg=cfg, store=home_store, stream=stream, clock=lambda: samples[-1].ts)
    client = TestClient(create_app(ctx))
    assert client.get("/").status_code == 200
    assert client.get("/policy").status_code == 404  # workplace only
    assert client.post("/api/arm").status_code == 200
    assert client.get("/api/events").json()
    status = client.get("/api/status").json()
    assert "household" in status["notice"]
