---
name: run-live
description: Launch the WiFi motion utility and show live motion on screen (terminal `watch`, live view page, menu bar). Use when asked to run, start, demo or screenshot the app, or to confirm a sensing/detector/display change works against the real WiFi link rather than only in tests.
---

# Run the app and see live motion

Shows motion from the Mac's link to its router, as specified in SPEC §4.7. Pick the lightest
surface that proves the change.

## 1. Preflight

```bash
uv sync
networksetup -getairportpower en0      # must say On
uv run wifi-utility --help             # confirms the CLI is installed
```

- Raw link probe and troubleshooting: `.claude/references/corewlan.md`.
- If the BSSID/SSID comes back empty, the process lacks Location Services permission (macOS 14+).
  Tell the user to grant it in System Settings → Privacy & Security → Location Services for the
  terminal/app running Python. Don't try to work around it.
- If the CLI command doesn't exist yet, check which milestone (SPEC §10) is done and use only the
  surfaces that exist.

## 2. Pick a surface

| Goal | Command | Available from |
|---|---|---|
| Quick check, raw signal or score | `uv run wifi-utility watch` | M1 (RSSI), M2 (score/state) |
| Charts, banner, full picture | `uv run wifi-utility run --mode home` then `uv run wifi-utility live` | M3 |
| Menu bar icon | `uv run wifi-utility run --mode home` (omit `--no-menubar`) | M3 |
| No live WiFi available | `uv run wifi-utility replay tests/fixtures/walking.csv` | M1 |

Run long-lived commands in the background and stop them when done. `watch` runs until
interrupted, so limit it: `uv run wifi-utility watch --secs 30` (macOS has no `timeout`).

## 3. Calibrate if needed

If output says "not calibrated" or calibration quality is `poor`, ask the user to leave the room
still and empty, then run `uv run wifi-utility calibrate --secs 180`. You can't produce an empty
room yourself, so don't fake calibration with a fixture on the live link.

## 4. Produce motion

You can't walk in front of the router. Ask the user to walk between the Mac and the router a few
times while `watch` or the live view runs, then read the output. Without the user, use `replay`
on a fixture and say clearly that the result came from recorded data, not the live link.

## 5. Report

- What surface you used, live link or replay.
- Observed state changes with timestamps, peak score, confidence, calibration quality.
- For the live view, take a screenshot with the browser tools (`http://127.0.0.1:8765/live`).
- Anything that didn't match SPEC §4.7 (wrong colour, missing threshold lines, updates slower than 1 Hz).

## Workplace mode

Only run `--mode workplace` when the task is about workplace mode. Expect `watch` to refuse and
the live view to show only Occupied/Vacant for the current 15-minute bucket. If it shows a score,
RSSI or per-second state, that's a bug against SPEC §6.4. Report it.
