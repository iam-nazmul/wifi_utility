# CLAUDE.md

macOS utility that detects motion from the Mac's WiFi link to the router (RSSI/noise/tx-rate
via CoreWLAN). Two modes: **home** (armed motion alerts) and **workplace** (aggregate zone
occupancy under a disclosed policy). [SPEC.md](SPEC.md) is the source of truth — read the
relevant section before changing behaviour, and update the spec in the same change if behaviour
intentionally diverges.

## Status

M1–M5 implemented; all tests pass on synthetic data (`tests/synth.py`). Not yet done: real
fixture recordings + tuning (SPEC §9), and a live-link run (needs Location Services permission
for the terminal/editor). One known limitation is pinned as a strict `xfail` in
`tests/test_detector.py` (noise jump after calibration) — don't delete it to make CI green.

## References (`.claude/references/`)

Read the relevant one before working in that area (index in `.claude/references/README.md`):
`corewlan.md` (WiFi API, permissions), `detector-math.md` (exact feature/score/state formulas),
`data-formats.md` (CSV, labels, stream/WebSocket, webhook, export schemas),
`rssi-sensing-background.md` (why the detector works, its limits),
`workplace-monitoring-law.md` (questions the policy template must cover; not legal advice).

## Project skills (`.claude/skills/`)

- `next-milestone` — find and build the next SPEC §10 milestone.
- `run-live` — launch the app and show live motion (terminal, live view, menu bar).
- `record-fixture` — record labelled signal CSVs into `tests/fixtures/`.
- `tune-detector` — measure against SPEC §9 targets and adjust thresholds.
- `workplace-guard` — check changes against the workplace-mode invariants. Run it before
  finishing any change that touches workplace mode, storage, display or export.

## Project subagents (`.claude/agents/`)

- `workplace-privacy-reviewer` — read-only, independent check of workplace invariants; verdict OK/Blocked.
- `detector-tuner` — offline tuning on fixtures against SPEC §9.
- `macos-integrator` — CoreWLAN, Location permission, menu bar, notifications, launchd.
- `spec-auditor` — read-only code ↔ SPEC ↔ docs consistency and milestone status.

## Hooks (`.claude/hooks/`, registered in `.claude/settings.json`)

- `guard_bash.py` blocks `sudo`, packet capture, scanning, `airport`/`wdutil`, turning WiFi off,
  `--no-verify`, commits with Claude attribution or a non-iam-nazmul email, and commits of
  fixtures containing real BSSIDs.
- `guard_files.py` blocks edits to `.env*`, `*.db`/`*.sqlite*`, `.git/`, and real BSSIDs in
  `tests/fixtures/*.csv`.
- `after_edit.py` runs `ruff format` + `ruff check --fix` on edited Python (once `pyproject.toml`
  exists) and reminds you to run `workplace-guard` after touching workplace-reachable files.

If a hook blocks something legitimate, fix the hook rather than working around it.

## Commands

```bash
uv sync                                  # install deps
uv run pytest                            # all tests
uv run pytest tests/test_detector.py -k hysteresis
uv run ruff check . && uv run ruff format .
uv run wifi-utility watch --secs 20            # live link; macOS has no `timeout` command
uv run wifi-utility record out.csv --secs 60   # needs real WiFi + Location permission
uv run wifi-utility replay tests/fixtures/walking.csv --report
```

Tests set `WIFI_UTILITY_HOME` to a temp dir (see `tests/conftest.py`); anything that runs the
CLI against real data writes to `~/Library/Application Support/wifi_utility/`.

## Stack and layout

- Python 3.12+, `uv`, `pyobjc-framework-CoreWLAN`, `numpy`, stdlib `sqlite3`, FastAPI dashboard.
- Code in `src/wifi_utility/` (layout in SPEC §8). Tests in `tests/`, recorded CSV fixtures in
  `tests/fixtures/`.
- Config: `config.toml` (example in `config.example.toml`). Data: 
  `~/Library/Application Support/wifi_utility/data.db`.

## Architecture rules

- Pipeline is `SignalSource → Sampler → Detector → Mode policy → (store | notifier | dashboard | audit)`.
  Keep stages decoupled; each takes plain data in and out.
- Everything below the mode layer must run without CoreWLAN. Tests use `ReplaySource` and
  CSV fixtures only — never require live WiFi or Location permission in tests.
- Inject time (a `clock` callable) into sampler, detector and modes. No `time.time()` calls
  inside logic, so state machines and notice periods are testable.
- Baselines are keyed by BSSID + channel. A link change forces recalibration; never reuse or
  blend baselines across links.
- Baseline drift updates only while `idle`, never during `motion`.
- All on-screen motion display (menu bar, `/live` page, `watch`) reads one shared 1 Hz stream
  from `display/stream.py`; don't let display code read the detector or source directly.

## Workplace mode invariants (do not weaken)

These are product requirements, enforced in code, not config (SPEC §6):

- Won't start without: a complete published policy, its effective date reached (notice period),
  and the posted-notice confirmation.
- Persists only `occupied: bool` per zone per 15-minute bucket. No raw samples, no debug capture,
  no scores, no per-person data.
- Retention hard cap 90 days; config values above it are rejected, not clamped silently.
- No real-time motion alerts in working hours; after-hours alert only if listed in the policy.
- On-screen display (menu bar, live view, `watch`) shows only current-bucket Occupied/Vacant —
  no live score, RSSI or per-second state; `watch` refuses to run (SPEC §4.7).
- Every view, export and config change goes to the append-only `audit` table.
- Policy change → new version, notice period restarts, collection stops until it takes effect.

Each invariant needs a test in `tests/test_workplace_gates.py`. If a request would relax one,
stop and ask rather than implementing it.

## Scope limits

- Only read the Mac's own link to its connected router. No packet capture, monitor mode,
  scanning or inspecting other devices' traffic.
- Don't add features that identify, count or track individuals, or that produce per-employee
  reports — the signal can't support it and the spec rules it out.
- Dashboard binds to `127.0.0.1` by default; any non-local bind requires a password.

## Accuracy honesty

v1 detects motion only (not stillness, identity, count or location). UI text, docs and log
messages must not claim more. Always surface confidence and calibration quality alongside a
detection.

## Git

- Author: `iam-nazmul <nazmul.cse48@gmail.com>`. Commit only when asked.
- No `Co-Authored-By: Claude` trailers or "Generated with Claude Code" lines.
