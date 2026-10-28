"""Switching mode while running (dashboard Mode panel)."""

import time

import pytest
from fastapi.testclient import TestClient
from synth import generate
from test_workplace_gates import TUESDAY_10AM, policy_text, ready

from wifi_utility.config import load
from wifi_utility.dashboard.app import DashboardContext, create_app
from wifi_utility.notifier import Notifier
from wifi_utility.runtime import Runtime, SwitchError, write_mode
from wifi_utility.sources.replay import ReplaySource
from wifi_utility.store import Store


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('# my settings\nmode = "home"\n\n[home]\nexit_delay_secs = 10\n')
    return p


@pytest.fixture
def runtime(config_file):
    samples, _ = generate(60, seed=3)
    rt = Runtime(
        load(config_file),
        lambda: ReplaySource(list(samples)),
        Notifier(desktop=None, background=False),
        clock=lambda: TUESDAY_10AM,
    )
    rt.start()
    yield rt
    rt.stop()


def wait_for_messages(rt, n=1, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and rt.stream.seq < n:
        time.sleep(0.05)


def test_switch_to_workplace_refused_without_policy(runtime, config_file):
    with pytest.raises(SwitchError, match="no policy"):
        runtime.switch("workplace", "test")
    assert runtime.mode == "home"
    assert runtime._thread.is_alive()  # the home engine kept running
    assert 'mode = "home"' in config_file.read_text()


def test_switch_both_ways_persists_and_audits(runtime, config_file):
    ready(Store(workplace=True))  # publish + confirm a policy effective before TUESDAY_10AM
    assert runtime.switch("workplace", "dashboard") == "Switched to workplace mode"
    assert runtime.mode == "workplace" and runtime.stream.mode == "workplace"
    assert runtime.store.workplace
    text = config_file.read_text()
    assert 'mode = "workplace"' in text and "# my settings" in text and "exit_delay_secs" in text
    wait_for_messages(runtime)
    assert runtime.stream.latest()[1]["mode"] == "workplace"

    assert runtime.switch("home", "dashboard") == "Switched to home mode"
    assert runtime.mode == "home" and 'mode = "home"' in config_file.read_text()
    actions = [(a["action"], a["detail"]) for a in runtime.store.audit_log()]
    assert ("mode_changed", "home → workplace") in actions
    assert ("mode_changed", "workplace → home") in actions


def test_same_mode_is_a_no_op(runtime):
    assert runtime.switch("home", "t") == "Already in home mode"


def test_write_mode_inserts_when_missing(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[home]\nexit_delay_secs = 5\n")
    write_mode(p, "workplace")
    assert p.read_text().startswith('mode = "workplace"\n[home]')


def test_mode_api(runtime):
    ctx = DashboardContext(
        cfg=runtime.cfg,
        store=runtime.store,
        stream=runtime.stream,
        clock=lambda: TUESDAY_10AM,
        switch_mode=lambda m: runtime.switch(m, "dashboard"),
    )
    runtime.on_change.append(lambda rt: setattr(ctx, "stream", rt.stream))
    client = TestClient(create_app(ctx))
    status = client.get("/api/status").json()
    assert status["can_switch"] and "no policy" in status["workplace_status"]
    r = client.post("/api/mode", json={"mode": "workplace"})
    assert r.status_code == 409 and "no policy" in r.json()["detail"]

    ready(Store(workplace=True), text=policy_text())
    r = client.post("/api/mode", json={"mode": "workplace"})
    assert r.status_code == 200 and r.json()["message"] == "Switched to workplace mode"


def test_mode_api_needs_running_app(home_store, cfg):
    from wifi_utility.display.stream import LiveStream

    client = TestClient(create_app(DashboardContext(cfg, home_store, LiveStream("home"))))
    assert client.post("/api/mode", json={"mode": "workplace"}).status_code == 501
