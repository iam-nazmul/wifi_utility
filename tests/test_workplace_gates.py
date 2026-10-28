"""Workplace-mode invariants (SPEC §6, §4.7; CLAUDE.md). One or more tests per invariant."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from synth import walking

from wifi_utility import cli
from wifi_utility.config import Config, ConfigError, load
from wifi_utility.dashboard.app import DashboardContext, create_app
from wifi_utility.detector import MOTION, DetectorOutput, MotionEvent
from wifi_utility.display.stream import WORKPLACE_KEYS, LiveStream, StreamShapeError
from wifi_utility.engine import Engine, calibrate_samples, drive
from wifi_utility.modes import workplace as wp
from wifi_utility.notifier import Notifier
from wifi_utility.store import StoreError

DAY = 86400
EFFECTIVE = "2026-11-10"
EFFECTIVE_TS = datetime(2026, 11, 10).timestamp()
PUBLISH_TS = EFFECTIVE_TS - 8 * DAY
TUESDAY_10AM = datetime(2026, 11, 17, 10, 0).timestamp()
TUESDAY_10PM = datetime(2026, 11, 17, 22, 0).timestamp()


def policy_text(
    effective=EFFECTIVE,
    purpose="Meeting-room utilisation planning.",
    retention="30 days",
    names="no",
    drop=None,
):
    sections = {
        "Employer and contact": "Example Ltd. Facilities team, facilities@example.com",
        "Zones and sensor location": "meeting-room-1; sensor Mac on the shelf by the door.",
        "What is measured": "Motion-based occupancy of the zone, in 15-minute periods.",
        "What is not measured": (
            "No identity, individual presence, device data, network traffic, audio or video."
        ),
        "Purpose": purpose,
        "Hours of operation": "- mon-fri 08:00-18:00",
        "Retention period": retention,
        "Who can access the data": "Facilities manager.",
        "How to see the data and raise concerns": "Public page; email the facilities team.",
        "Applicable law and consultation": "Reviewed by counsel; staff rep consulted.",
        "Effective date": effective,
        "Acknowledgement records names": names,
    }
    if drop:
        sections.pop(drop)
    return "# Policy\n\n" + "\n\n".join(f"## {k}\n{v}" for k, v in sections.items())


def wp_cfg(**workplace) -> Config:
    cfg = Config(mode="workplace")
    for k, v in workplace.items():
        setattr(cfg.workplace, k, v)
    return cfg.validate()


def ready(store, cfg=None, text=None):
    """Publish a valid policy and confirm the notice."""
    cfg = cfg or wp_cfg()
    wp.publish(store, cfg, text or policy_text(), PUBLISH_TS, "test")
    wp.confirm_notice(store, PUBLISH_TS + 60, "test")
    return cfg


def started_output(ts):
    ev = MotionEvent(ts - 3, ts, None, 6.0, "high")
    return DetectorOutput(ts, 6.0, MOTION, "high", False, started=ev)


class FakeNotifier(Notifier):
    def __init__(self):
        super().__init__(desktop=None, background=False)


# 1. Won't start without complete policy, effective date reached, posted-notice confirmation


def test_no_policy_refuses_start(wp_store):
    with pytest.raises(wp.GateError, match="no policy"):
        wp.WorkplaceMode(wp_cfg(), wp_store, FakeNotifier(), TUESDAY_10AM)


@pytest.mark.parametrize("section", wp.REQUIRED_SECTIONS)
def test_every_section_is_required(section):
    with pytest.raises(wp.PolicyError, match="missing or empty"):
        wp.parse_policy(policy_text(drop=section))


def test_placeholders_rejected():
    with pytest.raises(wp.PolicyError, match="placeholder"):
        wp.parse_policy(policy_text(purpose="<why the data is collected>"))


def test_shipped_template_is_rejected_until_filled():
    from pathlib import Path

    template = Path(__file__).parents[1] / "policy" / "TEMPLATE.md"
    with pytest.raises(wp.PolicyError):
        wp.parse_policy(template.read_text())


def test_not_measured_must_list_everything():
    text = policy_text().replace("audio or video", "audio")
    with pytest.raises(wp.PolicyError, match="video"):
        wp.parse_policy(text)


def test_effective_date_must_respect_notice_period(wp_store):
    with pytest.raises(wp.PolicyError, match="at least 7 days"):
        wp.publish(wp_store, wp_cfg(), policy_text(), EFFECTIVE_TS - 6 * DAY, "test")


def test_refuses_before_effective_date(wp_store):
    cfg = ready(wp_store)
    with pytest.raises(wp.GateError, match="takes effect"):
        wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), EFFECTIVE_TS - 3600)


def test_refuses_without_notice_confirmation(wp_store):
    cfg = wp_cfg()
    wp.publish(wp_store, cfg, policy_text(), PUBLISH_TS, "test")
    with pytest.raises(wp.GateError, match="notice not confirmed"):
        wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), TUESDAY_10AM)


def test_starts_when_all_gates_pass(wp_store):
    cfg = ready(wp_store)
    mode = wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), TUESDAY_10AM)
    assert mode.status.collecting
    assert wp_store.last_audit("notice_posted") is not None


def test_cli_run_refuses_without_policy(capsys):
    assert cli.main(["run", "--mode", "workplace", "--no-menubar"]) == 2
    assert "will not start" in capsys.readouterr().err


# 2. Policy change → new version, notice restarts, collection stops until it takes effect


def test_republish_stops_collection_until_new_version_effective(wp_store):
    cfg = ready(wp_store)
    mode = wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), TUESDAY_10AM)
    new_effective = datetime(2026, 12, 1).timestamp()
    v2 = wp.publish(
        wp_store, cfg, policy_text(effective="2026-12-01", retention="20 days"), TUESDAY_10AM, "t"
    )
    assert v2 == 2
    later = TUESDAY_10AM + wp.STATUS_RECHECK_SECS
    mode.on_output(None, later)
    assert not mode.status.collecting
    assert mode.message(later)["collection_active"] is False
    # Even after the new date, the notice for v2 must be confirmed again.
    assert not wp.collection_status(wp_store, cfg, new_effective + 1).collecting
    wp.confirm_notice(wp_store, new_effective + 2, "t")
    assert wp.collection_status(wp_store, cfg, new_effective + 3).collecting


def test_unchanged_policy_is_not_a_new_version(wp_store):
    cfg = ready(wp_store)
    with pytest.raises(wp.PolicyError, match="unchanged"):
        wp.publish(wp_store, cfg, policy_text(), PUBLISH_TS + 10, "t")


# 3 & 4. Only occupied/vacant per 15-min bucket is persisted; no samples, scores, events


def run_workplace_engine(wp_store, cfg):
    samples, _ = walking(n_passes=4)
    mode = wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), samples[0].ts)
    baseline = calibrate_samples(samples, cfg, 180)
    stream = LiveStream("workplace")
    engine = Engine(cfg, store=wp_store, stream=stream, mode=mode, baseline=baseline)
    outs = drive(engine, samples)
    return outs, stream


def test_engine_persists_only_bucket_booleans(wp_store):
    cfg = ready(wp_store)
    outs, _ = run_workplace_engine(wp_store, cfg)
    assert any(o.state == MOTION for o in outs)  # motion did happen
    db = sqlite3.connect(wp_store.path)
    assert db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    cols = [r[1] for r in db.execute("PRAGMA table_info(occupancy)")]
    assert cols == ["zone_id", "bucket_start", "occupied"]
    rows = wp_store.occupancy()
    assert rows and all(r["bucket_start"] % wp.BUCKET_SECS == 0 for r in rows)
    assert {r["occupied"] for r in rows} <= {0, 1}
    assert any(r["occupied"] for r in rows)


def test_store_refuses_raw_samples_and_events(wp_store):
    samples, _ = walking(n_passes=1)
    with pytest.raises(StoreError):
        wp_store.add_sample(samples[0])
    with pytest.raises(StoreError):
        wp_store.start_event("workplace", MotionEvent(0, 0, None, 5, "low"))


def test_debug_capture_rejected_in_workplace_config():
    cfg = Config(mode="workplace")
    cfg.home.debug_capture = True
    with pytest.raises(ConfigError, match="debug capture"):
        cfg.validate()


# 5. Retention ≤ 90 days; higher values rejected, not clamped


def test_config_retention_over_90_rejected(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('mode = "workplace"\n[workplace]\nretention_days = 91\n')
    with pytest.raises(ConfigError, match="retention_days"):
        load(p)


def test_policy_retention_over_90_rejected():
    with pytest.raises(wp.PolicyError, match="Retention"):
        wp.parse_policy(policy_text(retention="120 days"))


def test_effective_retention_is_the_shorter_and_prune_applies(wp_store):
    cfg = ready(wp_store, wp_cfg(retention_days=60))
    mode = wp.WorkplaceMode(cfg, wp_store, FakeNotifier(), TUESDAY_10AM)
    assert mode.retention_days == 30  # policy says 30
    wp_store.mark_occupancy("meeting-room-1", wp.bucket_start(TUESDAY_10AM - 31 * DAY), True)
    wp_store.mark_occupancy("meeting-room-1", wp.bucket_start(TUESDAY_10AM - 1 * DAY), True)
    wp_store.prune(TUESDAY_10AM, mode.retention_days)
    assert len(wp_store.occupancy()) == 1


# 6. No real-time alerts in working hours; after-hours alert only if listed in the policy


def test_no_alerts_without_after_hours_setting(wp_store):
    cfg = ready(wp_store)
    notifier = FakeNotifier()
    mode = wp.WorkplaceMode(cfg, wp_store, notifier, TUESDAY_10AM)
    mode.on_output(started_output(TUESDAY_10AM), TUESDAY_10AM)
    mode.on_output(started_output(TUESDAY_10PM), TUESDAY_10PM)
    assert notifier.sent == []


def test_after_hours_alert_needs_policy_purpose(wp_store):
    cfg = ready(wp_store, wp_cfg(after_hours_alert=True))
    st = wp.collection_status(wp_store, cfg, TUESDAY_10AM)
    assert not st.collecting and "purpose" in st.reason


def test_after_hours_alert_only_outside_hours_and_without_scores(wp_store):
    cfg = wp_cfg(after_hours_alert=True)
    ready(wp_store, cfg, policy_text(purpose="Room planning and after-hours security."))
    notifier = FakeNotifier()
    mode = wp.WorkplaceMode(cfg, wp_store, notifier, TUESDAY_10AM)
    mode.on_output(started_output(TUESDAY_10AM), TUESDAY_10AM)
    assert notifier.sent == []  # working hours: never
    mode._checked_at = TUESDAY_10PM
    mode.on_output(started_output(TUESDAY_10PM), TUESDAY_10PM)
    assert len(notifier.sent) == 1
    assert set(notifier.sent[0]) == {"event", "zone_id", "bucket_start", "host"}


# 7. Screens get only current-bucket Occupied/Vacant; watch refuses


def test_workplace_stream_shape_is_exact(wp_store):
    cfg = ready(wp_store)
    _, stream = run_workplace_engine(wp_store, cfg)
    history = stream.history()
    assert history
    assert all(set(m) == WORKPLACE_KEYS for m in history)


def test_workplace_stream_rejects_home_messages():
    stream = LiveStream("workplace")
    with pytest.raises(StreamShapeError):
        stream.publish(
            {
                "mode": "workplace",
                "zone_id": "z",
                "bucket_start": 0,
                "occupied": True,
                "policy_version": 1,
                "collection_active": True,
                "score": 5.0,
            }
        )


def test_watch_refuses_in_workplace_mode(capsys, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('mode = "workplace"\n')
    assert cli.main(["--config", str(p), "watch"]) == 2
    assert "not available in workplace mode" in capsys.readouterr().err


def dashboard(wp_store, cfg, stream=None):
    ctx = DashboardContext(
        cfg=cfg,
        store=wp_store,
        stream=stream or LiveStream("workplace"),
        clock=lambda: TUESDAY_10AM,
    )
    return TestClient(create_app(ctx))


def test_websocket_sends_only_workplace_shape(wp_store):
    cfg = ready(wp_store)
    _, stream = run_workplace_engine(wp_store, cfg)
    with dashboard(wp_store, cfg, stream).websocket_connect("/ws/live") as ws:
        first = ws.receive_json()
    assert all(set(m) == WORKPLACE_KEYS for m in first["history"])


def test_home_only_endpoints_hidden(wp_store):
    client = dashboard(wp_store, ready(wp_store))
    assert client.get("/api/events").status_code == 404
    assert client.post("/api/arm").status_code == 404


# 8. Exports are zone × bucket only


def test_export_is_aggregate_csv(wp_store):
    cfg = ready(wp_store)
    wp_store.mark_occupancy("meeting-room-1", wp.bucket_start(TUESDAY_10AM), True)
    r = dashboard(wp_store, cfg).get("/api/export.csv")
    lines = r.text.strip().splitlines()
    assert lines[0] == "zone_id,bucket_start_iso,occupied"
    assert len(lines[1].split(",")) == 3


# 9. Every view, export and config change is audited; audit is append-only


def test_views_and_exports_are_audited(wp_store):
    client = dashboard(wp_store, ready(wp_store))
    for url in ("/", "/live", "/api/occupancy", "/api/audit", "/api/export.csv"):
        assert client.get(url).status_code == 200
    actions = [(a["action"], a["detail"]) for a in wp_store.audit_log()]
    assert ("view", "dashboard") in actions and ("view", "live") in actions
    assert ("view", "audit_log") in actions
    assert any(a == "export" for a, _ in actions)
    assert any(a == "view" and d.startswith("occupancy") for a, d in actions)


def test_config_change_is_audited(wp_store, tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('mode = "workplace"\n')
    cfg = load(p)
    cli.audit_config(wp_store, cfg, 1.0)
    cli.audit_config(wp_store, cfg, 2.0)  # unchanged: no new entry
    p.write_text('mode = "workplace"\n[workplace]\nzone_id = "lab"\n')
    cli.audit_config(wp_store, load(p), 3.0)
    assert len([a for a in wp_store.audit_log() if a["action"] == "config_changed"]) == 2


def test_audit_is_append_only(wp_store):
    wp_store.audit(1.0, "t", "x")
    db = sqlite3.connect(wp_store.path)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("DELETE FROM audit")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.execute("UPDATE audit SET actor = 'y'")


# 10. Public page: active status, policy version, 7-day aggregate; no password


def test_public_policy_page(wp_store):
    cfg = ready(wp_store, Config(mode="workplace", dashboard=_pw_dash()).validate())
    wp_store.mark_occupancy("meeting-room-1", wp.bucket_start(TUESDAY_10AM), True)
    client = dashboard(wp_store, cfg)
    assert client.get("/").status_code == 401  # admin needs password
    assert client.get("/policy").status_code == 200
    data = client.get("/policy/data").json()
    assert data["collection_active"] is True and data["version"] == 1
    assert len(data["occupancy"]) == 1
    assert "Meeting-room utilisation" in data["text"]


def _pw_dash():
    from wifi_utility.config import DashboardConfig

    return DashboardConfig(bind="0.0.0.0", password="s3cret")


def test_non_local_bind_requires_password():
    from wifi_utility.config import DashboardConfig

    with pytest.raises(ConfigError, match="password"):
        Config(dashboard=DashboardConfig(bind="0.0.0.0")).validate()


# 11. Nothing identifies individuals: acknowledgements anonymous unless the policy says so


def test_ack_is_anonymous_by_default(wp_store):
    client = dashboard(wp_store, ready(wp_store))
    client.post("/policy/ack", json={"name": "Alex Doe"})
    row = sqlite3.connect(wp_store.path).execute("SELECT ack_by FROM acknowledgements").fetchone()
    assert row[0] is None
    assert client.get("/policy/data").json()["acknowledgements"] == 1


def test_ack_name_kept_only_when_policy_says_so(wp_store):
    client = dashboard(wp_store, ready(wp_store, text=policy_text(names="yes")))
    client.post("/policy/ack", json={"name": "Alex Doe"})
    row = sqlite3.connect(wp_store.path).execute("SELECT ack_by FROM acknowledgements").fetchone()
    assert row[0] == "Alex Doe"
