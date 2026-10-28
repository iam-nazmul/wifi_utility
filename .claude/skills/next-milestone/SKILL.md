---
name: next-milestone
description: Work out which SPEC §10 milestone is next and implement it end to end with tests. Use when the user says "build the next part", "continue", "start M1/M2/…", or asks what's left to build.
---

# Build the next milestone

## 1. Find where we are

- Read SPEC §10 and the project layout in SPEC §8.
- Check what exists in `src/wifi_utility/` and `tests/`, and run `uv run pytest` if there's a
  `pyproject.toml`.
- A milestone is done only when its features exist **and** its tests pass. The next milestone is
  the first one that isn't done. Tell the user which one it is before starting.

## 2. Scope per milestone

| Milestone | Build | Done when |
|---|---|---|
| M1 Sensing | `pyproject.toml` (uv), `sources/{base,corewlan,replay}.py`, `sampler.py`, `display/{stream,terminal}.py`, CLI `record`, `replay`, `watch` (RSSI only) | Replay tests pass; `watch` shows live RSSI (use `run-live`) |
| M2 Detector | `detector.py` (calibration, robust z-score, hysteresis, drift, confidence), `baselines` table, `watch` shows score/state | `test_detector.py` passes; `tune-detector` meets SPEC §9 on available fixtures |
| M3 Home | `modes/home.py`, `store.py` events, `notifier.py`, dashboard + `/live` + `/ws/live`, `display/menubar.py`, CLI `run`, `arm`, `disarm`, `live` | Motion alerts fire when armed only; live view matches SPEC §4.7 |
| M4 Workplace | `modes/workplace.py`, `policy/TEMPLATE.md`, policy publish/status, occupancy buckets, public policy page, audit log, export | `workplace-guard` all pass; `test_workplace_gates.py` covers every invariant |
| M5 Packaging | launchd plist, config validation, README | Fresh-clone install works from README alone |

## 3. How to build

- Follow CLAUDE.md architecture rules (injected clock, no CoreWLAN below the mode layer in
  tests, baselines keyed by BSSID+channel, one display stream).
- Use `.claude/references/` for formulas, schemas and API notes (see its README).
- Write tests alongside code. Use `ReplaySource` with fixtures; if a needed fixture is missing,
  generate a small synthetic CSV for unit tests and suggest `record-fixture` for real data.
- Check current library APIs (pyobjc CoreWLAN, FastAPI WebSockets, rumps, uPlot) with Context7
  before writing code against them.
- Run `uv run ruff check . && uv run ruff format . && uv run pytest` before calling it done.

## 4. Finish

- Summarise what was built, test results, and anything verified only on fixtures vs on the live link.
- Note any place the implementation diverged from SPEC and update SPEC to match if the
  divergence was intended.
- Don't commit unless asked.
