# wifi_utility

Turns a Mac's existing WiFi connection into a motion sensor. People moving between the Mac and
the router disturb the radio signal; the utility learns what the empty room looks like and flags
motion. No extra hardware, no cloud service.

Two modes:

- **Home Security**: arm it when the space should be empty; you get a macOS notification
  (optionally a webhook or ntfy push) when motion is detected.
- **Workplace Occupancy**: records only whether a zone was *occupied or vacant* per 15-minute
  period, and only under a published, disclosed policy that the software enforces.

See [SPEC.md](SPEC.md) for the full design.

## What it can and can't do

It detects **motion**, mainly across the line between the Mac and the router. It cannot detect
a person sitting still, count people, identify them, or tell where they are. Fans, pets,
doors and other WiFi traffic can cause false alarms. **Calibrate in the conditions you'll run in**:
if a fan or heater is usually on, calibrate with it on. It is not a certified alarm system.

## Requirements

- macOS 14 or later, connected to WiFi
- [uv](https://docs.astral.sh/uv/)
- **Location Services permission** for the app you run it from (Terminal, iTerm, VS Code…).
  macOS hides the router's address (BSSID) without it, and the utility needs it to keep a separate
  calibration per router. Run `uv run wifi-utility permission` **in Terminal or iTerm** and click
  Allow (editor terminals may not show the prompt), or enable the app in System Settings →
  Privacy & Security → Location Services. `record` works without it, using a placeholder address.

## Install

```bash
uv sync
cp config.example.toml config.toml   # optional; defaults work for home mode
```

## Home mode quick start

```bash
uv run wifi-utility watch --secs 30     # check the live signal in the terminal
uv run wifi-utility calibrate           # 3 min; leave the room empty and still
uv run wifi-utility run                 # engine + menu bar icon + dashboard
uv run wifi-utility live                # open the live view (http://127.0.0.1:8765/live)
uv run wifi-utility arm                 # alerts start after the 60 s exit delay
uv run wifi-utility disarm
```

On screen:

- **Menu bar**: ⚪ no motion · 🟠 score rising · 🔴 motion · ⊘ not calibrated or poor calibration.
  Click it for score, confidence, arm/disarm and "Open live view".
- **Live view**: big NO MOTION / MOTION DETECTED banner, 2-minute score chart with trigger and
  release lines, raw signal chart, signal strip. Press **F** for full screen.
- **Terminal**: `wifi-utility watch` prints one line per second with a sparkline.

Tell other household members and guests that the sensor is running.

## Workplace mode

Workplace mode won't start until all of these are true:

1. A policy is published: copy [policy/TEMPLATE.md](policy/TEMPLATE.md), fill in every section,
   then `uv run wifi-utility policy publish my-policy.md`. Its effective date must be at least
   `notice_days` (default 7) after publishing.
2. The effective date has arrived.
3. You've posted a printed notice in the zone and confirmed it:
   `uv run wifi-utility policy confirm-notice`.

```bash
uv run wifi-utility policy status
uv run wifi-utility run --mode workplace
```

Employees can open `http://<sensor>:8765/policy` to read the policy, see the same 7-day occupancy
data the employer sees, and acknowledge it. To serve it on the LAN, set `dashboard.bind` to the
Mac's address and set `dashboard.password` (the admin pages require it; the policy page doesn't).

Workplace mode stores nothing but occupied/vacant per 15-minute period, keeps it at most 90 days,
shows no live score or signal, and records every dashboard view, export and config change in an
append-only audit log. Changing the policy creates a new version and pauses collection until it
takes effect and its notice is confirmed. The template is **not legal advice**; have it reviewed
for your jurisdiction.

## Recording and tuning

```bash
uv run wifi-utility record my-room.csv --secs 300
uv run wifi-utility replay my-room.csv                       # calibrates on the first 3 min
uv run wifi-utility replay walk.csv --calibrate-from empty.csv --report   # needs walk.labels.csv
```

Label format and fixture conventions: [tests/fixtures/README.md](tests/fixtures/README.md).

## Run at login

```bash
uv run wifi-utility service install      # launchd agent, uses the current config and mode
uv run wifi-utility service uninstall
```

Logs: `~/Library/Application Support/wifi_utility/logs/`.

## Other commands

```bash
uv run wifi-utility export --since 2026-11-01 --out out.csv   # events (home) / occupancy (workplace)
```

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
```

Tests use synthetic signal data ([tests/synth.py](tests/synth.py)) and never touch live WiFi.
Passing them shows the logic works as designed, **not** that the accuracy targets in SPEC §9 hold
in a real room; that needs real recordings.
