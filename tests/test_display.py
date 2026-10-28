import io

import pytest
from synth import BSSID, generate, walking

from wifi_utility import cli
from wifi_utility.display.menubar import home_title, workplace_title
from wifi_utility.display.stream import HOME_KEYS, LiveStream, StreamShapeError
from wifi_utility.display.terminal import TerminalView, sparkline
from wifi_utility.engine import Engine, calibrate_samples, drive
from wifi_utility.sources.replay import write_samples_csv


def test_stream_emits_one_message_per_second(cfg):
    samples, _ = walking(n_passes=2)
    stream = LiveStream("home")
    baseline = calibrate_samples(samples, cfg, 180)
    drive(Engine(cfg, stream=stream, baseline=baseline), samples)
    msgs = stream.history()
    # One tick per whole second from the first second boundary through the final one.
    assert stream.seq == int(samples[-1].ts) - int(samples[0].ts) + 1
    assert all(set(m) == HOME_KEYS for m in msgs)
    assert all(b["ts"] - a["ts"] == 1 for a, b in zip(msgs, msgs[1:], strict=False))


def test_home_stream_rejects_extra_fields():
    with pytest.raises(StreamShapeError):
        LiveStream("home").publish({"mode": "home", "ts": 0})


def test_uncalibrated_then_link_change(cfg):
    samples, _ = generate(30, seed=5)
    other, _ = generate(30, seed=6, t0=samples[-1].ts + 0.2, bssid="00:00:00:00:00:02")
    baseline = calibrate_samples(samples, cfg, 30)
    stream = LiveStream("home")
    engine = Engine(cfg, stream=stream, baseline=baseline)
    drive(engine, samples)
    assert engine.detector is not None
    drive(engine, other)
    assert engine.detector is None  # never reuse a baseline from another link
    assert stream.latest()[1]["state"] == "uncalibrated"


def test_terminal_line_and_colours():
    view = TerminalView(io.StringIO(), color=False)
    base = {
        "mode": "home",
        "ts": 0,
        "state": "idle",
        "score": 0.4,
        "trigger": 3.5,
        "release": 2.0,
        "confidence": "high",
        "calibration_quality": "good",
        "armed": False,
        "signal": {"rssi_dbm": -55, "noise_dbm": -94, "tx_rate_mbps": 650.0, "channel": 36},
        "last_event_ts": None,
    }
    line = view.format(base)
    assert "idle" in line and "-55 dBm" in line
    line = view.format({**base, "ts": 1, "state": "motion", "score": 6.0})
    assert "MOTION" in line and "← motion" in line
    with pytest.raises(ValueError):
        view.format({"mode": "workplace"})


def test_sparkline():
    assert sparkline([0, 10, None]) == "▁█ "


def test_menubar_titles():
    m = {"state": "idle", "score": 2.5, "release": 2.0, "calibration_quality": "good"}
    assert home_title(m) == "🟠"
    assert home_title({**m, "score": 0.5}) == "⚪"
    assert home_title({**m, "state": "motion"}) == "🔴"
    assert home_title({**m, "calibration_quality": "poor"}) == "⊘"
    assert home_title(None) == "⊘"
    assert workplace_title({"collection_active": True, "occupied": True}) == "🔵 Occupied"
    assert "Not collecting" in workplace_title({"collection_active": False, "occupied": False})


def test_cli_replay_report(tmp_path, capsys):
    samples, labels = walking()
    path = tmp_path / "walk.csv"
    write_samples_csv(path, samples)
    lines = ["start_ts,end_ts,label"] + [f"{lb.start_ts},{lb.end_ts},{lb.label}" for lb in labels]
    (tmp_path / "walk.labels.csv").write_text("\n".join(lines) + "\n")
    assert cli.main(["replay", str(path), "--report"]) == 0
    out = capsys.readouterr().out
    assert "walk_on_path: detected" in out and "false events: 0" in out
    assert BSSID in path.read_text()
