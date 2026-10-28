"""Command line interface (SPEC §4.8)."""

from __future__ import annotations

import argparse
import csv
import getpass
import logging
import signal
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from . import service
from .config import Config, ConfigError, load
from .display.stream import LiveStream
from .display.terminal import TerminalView
from .engine import CalibrationError, Engine, calibrate_samples, collect_live, replay
from .evaluate import evaluate, labels_path_for, read_labels
from .modes import home as home_mode
from .modes import workplace as wp
from .notifier import Notifier
from .sources.base import SourceError
from .sources.replay import read_samples_csv, write_samples_csv
from .store import Store

log = logging.getLogger("wifi_utility")


def actor() -> str:
    return f"cli:{getpass.getuser()}"


def fail(msg: str, code: int = 1) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return code


HIDDEN_BSSID_NOTE = (
    "note: macOS is hiding the router address (no Location permission for this process), so the\n"
    "      calibration is kept per WiFi channel. That's fine with one router; on a mesh network,\n"
    "      recalibrate if you see false alarms after the Mac switches access point."
)


def live_source():
    from .sources.corewlan import CoreWLANSource

    # macOS often withholds the BSSID from command-line tools even when the launching app has
    # Location permission, so continue with a placeholder and key baselines by channel.
    return CoreWLANSource(allow_missing_bssid=True)


def warn_if_hidden(source) -> None:
    if getattr(source, "bssid_hidden", False):
        print(HIDDEN_BSSID_NOTE, file=sys.stderr)


def open_store(cfg: Config) -> Store:
    return Store(workplace=cfg.mode == "workplace")


def audit_config(store: Store, cfg: Config, now: float) -> None:
    from .runtime import audit_config as _audit

    _audit(store, cfg, now, actor())


# --- commands -------------------------------------------------------------------------------


def cmd_calibrate(args, cfg: Config) -> int:
    secs = args.secs or cfg.detector.calibration_secs
    print(f"Calibrating for {secs:g}s. Leave the room empty and still…")

    def progress(elapsed: float, _s) -> None:
        print(f"\r  {elapsed:5.0f}/{secs:g}s", end="", flush=True)

    try:
        source = live_source()
        samples = collect_live(source, secs, cfg.sample_hz, on_progress=progress)
        print()
        warn_if_hidden(source)
        baseline = calibrate_samples(samples, cfg, secs)
    except (SourceError, CalibrationError) as e:
        print()
        return fail(str(e))
    store = open_store(cfg)
    store.save_baseline(baseline)
    store.audit(time.time(), actor(), "calibrated", f"quality={baseline.quality}")
    print(
        f"Calibration quality: {baseline.quality} "
        f"({baseline.n_windows} windows, mean RSSI std {baseline.mean_rssi_std:.2f} dB)"
    )
    if baseline.quality == "poor":
        print("Quality is poor: check nothing was moving, then try again (maybe for longer).")
        return 1
    return 0


def cmd_run(args, cfg: Config) -> int:
    from .dashboard.app import DashboardContext, serve
    from .runtime import Runtime, SwitchError

    runtime = Runtime(cfg, live_source, Notifier(cfg.home.webhook_url, cfg.home.ntfy_url))
    try:
        runtime.prepare()  # policy gates first, before touching the WiFi hardware
    except SwitchError as e:
        return fail(f"{e} (or start in home mode with --mode home)", 2)
    source = live_source()
    try:
        source.start()
        source.stop()
    except SourceError as e:
        return fail(str(e))
    warn_if_hidden(source)
    runtime.start()

    ctx = DashboardContext(
        cfg=runtime.cfg,
        store=runtime.store,
        stream=runtime.stream,
        switch_mode=lambda mode: runtime.switch(mode, "dashboard"),
    )

    def sync_ctx(rt: Runtime) -> None:
        ctx.cfg, ctx.store, ctx.stream = rt.cfg, rt.store, rt.stream

    runtime.on_change.append(sync_ctx)
    stop = runtime.app_stop
    threading.Thread(target=serve, args=(ctx, stop), name="dashboard", daemon=True).start()
    url = f"http://{cfg.dashboard.bind}:{cfg.dashboard.port}"
    print(f"Running in {runtime.mode} mode. Dashboard: {url}/  Live view: {url}/live")
    print("Change mode from the dashboard (Mode panel).")
    if runtime.mode == "workplace":
        print(f"Public policy page: {url}/policy")
    else:
        print("Tell other household members and guests that the motion sensor is running.")

    def on_arm(_want: bool) -> str:
        # Toggle on the saved state, not the screen: during the exit delay the screen still
        # says "not armed", and re-arming there must not restart the delay.
        if runtime.mode != "home":
            return "The alarm is only available in home mode"
        store, now = runtime.store, time.time()
        if home_mode.armed_at(store) is None:
            try:
                home_mode.arm(store, now, "menubar", runtime.engine.calibration_quality)
            except home_mode.ArmError as e:
                return str(e)
            return "Alarm " + home_mode.describe_arm(store, now, runtime.cfg.home.exit_delay_secs)
        home_mode.disarm(store, now, "menubar")
        return "Disarmed"

    def arm_text() -> str:
        return home_mode.describe_arm(runtime.store, time.time(), runtime.cfg.home.exit_delay_secs)

    use_menubar = not args.no_menubar and sys.platform == "darwin"
    try:
        if use_menubar:
            try:
                from .display.menubar import run_menubar

                run_menubar(
                    lambda: runtime.stream,
                    url,
                    stop.set,
                    on_arm,
                    should_exit=stop.is_set,
                    armed_text=arm_text,
                )
                return 0
            except ImportError:
                print("rumps not available; running without the menu bar")
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        while not stop.wait(0.5):
            pass
        return 0
    finally:
        runtime.stop()


def cmd_watch(args, cfg: Config) -> int:
    if cfg.mode == "workplace":
        return fail("watch is not available in workplace mode (SPEC §4.7)", 2)
    store = open_store(cfg)
    stream = LiveStream("home")
    stream.subscribe(TerminalView())
    source = live_source()
    try:
        source.start()
        source.stop()
    except SourceError as e:
        return fail(str(e))
    warn_if_hidden(source)
    engine = Engine(cfg, source, store=store, stream=stream)
    stop = threading.Event()
    if args.secs:
        threading.Timer(args.secs, stop.set).start()
    try:
        engine.run(stop)
    except SourceError as e:
        return fail(str(e))
    except KeyboardInterrupt:
        pass
    return 0


def cmd_live(args, cfg: Config) -> int:
    url = f"http://{cfg.dashboard.bind}:{cfg.dashboard.port}/live"
    print(f"Opening {url} (start the app first with `wifi-utility run`)")
    webbrowser.open(url)
    return 0


def cmd_arm(args, cfg: Config) -> int:
    if cfg.mode != "home":
        return fail("arm is a home mode command")
    store = open_store(cfg)
    baseline = store.latest_baseline()
    try:
        home_mode.arm(store, time.time(), actor(), baseline.quality if baseline else None)
    except home_mode.ArmError as e:
        return fail(str(e))
    now = time.time()
    print(f"Alarm {home_mode.describe_arm(store, now, cfg.home.exit_delay_secs)}.")
    return 0


def cmd_disarm(args, cfg: Config) -> int:
    if cfg.mode != "home":
        return fail("disarm is a home mode command")
    home_mode.disarm(open_store(cfg), time.time(), actor())
    print("Disarmed.")
    return 0


def cmd_policy(args, cfg: Config) -> int:
    cfg.mode = "workplace"  # policy commands always operate on workplace data
    store = open_store(cfg)
    now = time.time()
    if args.policy_cmd == "publish":
        text = Path(args.file).read_text()
        try:
            version = wp.publish(store, cfg, text, now, actor())
        except wp.PolicyError as e:
            return fail(f"policy rejected:\n{e}")
        eff = datetime.fromtimestamp(store.active_policy()["effective_ts"])
        print(f"Published policy v{version}, effective {eff:%Y-%m-%d}.")
        print(
            "Next: post the printed notice in the zone, then run "
            "`wifi-utility policy confirm-notice`."
        )
        print("Collection is stopped until the new version takes effect.")
        return 0
    if args.policy_cmd == "confirm-notice":
        active = store.active_policy()
        if not active:
            return fail("no published policy")
        if not args.yes:
            answer = input(
                f"Is the printed notice for policy v{active['version']} posted in "
                f"zone '{cfg.workplace.zone_id}'? [y/N] "
            )
            if answer.strip().lower() not in ("y", "yes"):
                return fail("not confirmed")
        version = wp.confirm_notice(store, now, actor())
        print(f"Posted notice confirmed for policy v{version}.")
        return 0
    # status
    st = wp.collection_status(store, cfg, now)
    print(f"Collection: {'ACTIVE' if st.collecting else 'stopped'} — {st.reason}")
    for p in store.policies():
        print(
            f"  v{p['version']}{' (active)' if p['active'] else ''}: "
            f"published {datetime.fromtimestamp(p['published_ts']):%Y-%m-%d}, "
            f"effective {datetime.fromtimestamp(p['effective_ts']):%Y-%m-%d}, "
            f"notice {'confirmed' if p['notice_confirmed_ts'] else 'NOT confirmed'}, "
            f"acknowledgements {store.ack_count(p['version'])}"
        )
    return 0


def cmd_record(args, cfg: Config) -> int:
    print(f"Recording {args.secs:g}s of samples to {args.out}…")

    def progress(elapsed: float, s) -> None:
        rssi = f"{s.rssi_dbm} dBm" if s else "-"
        print(f"\r  {elapsed:5.0f}/{args.secs:g}s  RSSI {rssi}   ", end="", flush=True)

    try:
        # Recording works without the BSSID: fixtures get a placeholder before commit anyway.
        source = live_source()
        samples = collect_live(source, args.secs, cfg.sample_hz, on_progress=progress)
    except SourceError as e:
        print()
        return fail(str(e))
    print()
    n = write_samples_csv(args.out, samples)
    links = {s.link for s in samples}
    print(f"Wrote {n} samples.")
    if len(links) > 1:
        print("warning: the router link changed during recording; don't use this file for tuning")
    if source.bssid_hidden:
        print(
            "note: macOS hid the router address (no Location permission), so the file uses "
            "00:00:00:00:00:00 and can't detect roaming between access points. Channel changes "
            "are still recorded. Run `wifi-utility permission` in Terminal to fix this."
        )
    else:
        print("Replace the real BSSID with a placeholder before committing it as a fixture.")
    return 0


def cmd_permission(args, cfg: Config) -> int:
    from .sources.location import location_status, request_location_access

    try:
        before = location_status()
    except ImportError:
        return fail("CoreLocation is unavailable; run `uv sync` on macOS")
    print(f"Location Services status: {before}")
    if before == "not determined":
        print("Asking macOS… click Allow in the prompt (waiting up to 60 s).")
        after = request_location_access(timeout=60)
        print(f"Status now: {after}")
        if after == "not determined":
            print(
                "No prompt appeared. Run this command from Terminal or iTerm (some editors can't "
                "show it), or enable the app in System Settings → Privacy & Security → "
                "Location Services."
            )
            return 1
        before = after
    if before in {"denied", "restricted"}:
        print(
            "Enable it in System Settings → Privacy & Security → Location Services for the app "
            "running this command, then restart that app."
        )
        return 1
    print("Location access granted; the router address (BSSID) is now readable.")
    return 0


def cmd_replay(args, cfg: Config) -> int:
    samples = read_samples_csv(args.file)
    if not samples:
        return fail("no samples in file")
    cal_samples = read_samples_csv(args.calibrate_from) if args.calibrate_from else samples
    try:
        baseline = calibrate_samples(cal_samples, cfg, args.calibrate_secs)
    except CalibrationError as e:
        return fail(str(e))
    if baseline.link != samples[0].link:
        return fail("calibration file is from a different router link")
    outs = replay(samples, cfg, baseline)
    t0 = samples[0].ts
    print(f"baseline quality {baseline.quality}; {len(outs)} windows scored")
    for o in outs:
        if o.started:
            print(f"  motion at +{o.started.start_ts - t0:7.1f}s (detected +{o.ts - t0:.1f}s)")
        if o.ended:
            print(
                f"  ended  at +{o.ended.end_ts - t0:7.1f}s  peak {o.ended.peak_score:.1f} "
                f"confidence {o.ended.confidence}"
            )
    if args.report:
        labels_file = labels_path_for(args.file)
        if not labels_file.exists():
            return fail(f"--report needs {labels_file}")
        grace = cfg.detector.release_secs + cfg.window_secs
        report = evaluate(outs, read_labels(labels_file), grace)
        print("report:")
        for line in report.lines():
            print(f"  {line}")
    return 0


def cmd_export(args, cfg: Config) -> int:
    since = datetime.strptime(args.since, "%Y-%m-%d").timestamp()
    store = open_store(cfg)
    out = open(args.out, "w", newline="") if args.out else sys.stdout
    try:
        if cfg.mode == "workplace":
            from .dashboard.app import occupancy_csv

            out.write(occupancy_csv(store.occupancy(since=since)))
        else:
            writer = csv.writer(out)
            writer.writerow(["start_iso", "end_iso", "peak_score", "confidence"])
            for e in reversed(store.events(since=since, limit=100000)):
                end = datetime.fromtimestamp(e["end_ts"]).isoformat() if e["end_ts"] else ""
                writer.writerow(
                    [
                        datetime.fromtimestamp(e["start_ts"]).isoformat(),
                        end,
                        e["peak_score"],
                        e["confidence"],
                    ]
                )
    finally:
        if args.out:
            out.close()
    store.audit(time.time(), actor(), "export", f"mode={cfg.mode} since={args.since}")
    return 0


def cmd_service(args, cfg: Config) -> int:
    if args.service_cmd == "install":
        path = service.install(cfg.mode, cfg.source_path)
        print(f"Installed and loaded {path}")
    else:
        print("Removed." if service.uninstall() else "Not installed.")
    return 0


# --- parser ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wifi-utility",
        description="Detect motion from this Mac's WiFi link to its router.",
    )
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("calibrate", help="learn the empty-room baseline")
    s.add_argument("--secs", type=float)
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("run", help="run the engine, dashboard and menu bar")
    s.add_argument("--mode", choices=["home", "workplace"])
    s.add_argument("--no-menubar", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("watch", help="live motion in the terminal (home mode)")
    s.add_argument("--secs", type=float, help="stop after this many seconds")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("live", help="open the live view in the browser")
    s.set_defaults(fn=cmd_live)

    sub.add_parser("arm", help="arm home alerts").set_defaults(fn=cmd_arm)
    sub.add_parser("disarm", help="disarm home alerts").set_defaults(fn=cmd_disarm)

    s = sub.add_parser("policy", help="workplace policy")
    psub = s.add_subparsers(dest="policy_cmd", required=True)
    pp = psub.add_parser("publish", help="publish a policy file (see policy/TEMPLATE.md)")
    pp.add_argument("file")
    pc = psub.add_parser("confirm-notice", help="confirm the printed notice is posted")
    pc.add_argument("--yes", action="store_true")
    psub.add_parser("status", help="show policy versions and collection status")
    s.set_defaults(fn=cmd_policy)

    s = sub.add_parser("permission", help="ask macOS for Location access (needed for BSSID)")
    s.set_defaults(fn=cmd_permission)

    s = sub.add_parser("record", help="record raw samples to CSV")
    s.add_argument("out")
    s.add_argument("--secs", type=float, required=True)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("replay", help="run the detector offline on a CSV")
    s.add_argument("file")
    s.add_argument("--report", action="store_true", help="score against <file>.labels.csv")
    s.add_argument("--calibrate-from", help="CSV of an empty room (default: start of FILE)")
    s.add_argument("--calibrate-secs", type=float)
    s.set_defaults(fn=cmd_replay)

    s = sub.add_parser("export", help="export events (home) or occupancy (workplace) as CSV")
    s.add_argument("--since", required=True, help="YYYY-MM-DD")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("service", help="run at login via launchd")
    ssub = s.add_subparsers(dest="service_cmd", required=True)
    ssub.add_parser("install")
    ssub.add_parser("uninstall")
    s.set_defaults(fn=cmd_service)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load(args.config, getattr(args, "mode", None))
    except ConfigError as e:
        return fail(f"config: {e}")
    try:
        return args.fn(args, cfg)
    except FileNotFoundError as e:
        hint = ""
        if "fixtures" in str(e.filename):
            hint = " (fixtures are real recordings; see tests/fixtures/README.md to record one)"
        return fail(f"file not found: {e.filename}{hint}")
    except SourceError as e:  # e.g. a CSV with the wrong columns
        return fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
