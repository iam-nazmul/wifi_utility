# WiFi Motion Sensing Utility — Specification

Status: Draft v0.1 · 2026-10-28 · Owner: iam-nazmul

## 1. Summary

A macOS utility that turns the Mac's existing WiFi link to the router into a motion sensor.
People moving between (or near) the Mac and the access point disturb the radio path, which shows
up as fluctuation in the signal the Mac receives. The utility samples that signal, learns what
"empty room" looks like, and flags periods of motion.

It runs in one of two modes:

| Mode | Purpose | Output |
|---|---|---|
| **Home Security** | Alert the owner when motion happens while the space should be empty | Real-time alerts + event log |
| **Workplace Occupancy** | Let an employer measure whether a space is in use, under a written policy disclosed to everyone affected | Aggregate occupancy for a zone, no alerts on individuals |

Both modes share one sensing engine. They differ in what is alerted, stored and shown.

## 2. What the hardware can and cannot do

This section sets expectations. Getting it wrong would make the product unreliable.

**Available on a stock Mac + consumer router (v1 target):**
- RSSI (dBm), noise floor (dBm), transmit rate, channel, and BSSID of the connected AP, via
  Apple's CoreWLAN framework (`CWWiFiClient.shared().interface()`).
- Practical sample rate: 2–10 Hz. No root required for RSSI; reading the SSID/BSSID on
  macOS 14+ requires Location Services permission for the app.

**Not available on a Mac:**
- Channel State Information (CSI), the per-subcarrier amplitude/phase data that research-grade
  WiFi sensing relies on. macOS and consumer routers do not expose it.

**Consequences for v1:**
- Detects *motion*, not presence of a still person, not identity, not count, not location
  within the room.
- Works best when people move through the line between the Mac and the router. Coverage
  outside that path is weak.
- Sensitive to environmental noise (fans, pets, doors, other networks, router rate changes).
  Requires calibration per site.

**Optional v2 upgrade (out of scope for v1, design must allow it):** an ESP32 sensor node running
ESP-IDF CSI capture, streaming over UDP to the Mac. Plugs into the same pipeline as a second
`SignalSource` (§4.1). Gives much better sensitivity and enables multi-zone coverage.

## 3. Goals and non-goals

### Goals
- G1. Detect motion from the connected router's signal with no extra hardware.
- G2. Home mode: notify the owner within 10 seconds of sustained motion while armed.
- G3. Workplace mode: report zone occupancy (occupied / vacant per 15-minute bucket) only while a
  published policy is active and acknowledged.
- G4. Run entirely on the local machine. No cloud service is required.
- G5. Be transparent about accuracy: show a confidence value and the calibration quality.

### Non-goals
- Identifying, tracking, or counting individual people.
- Monitoring any device, network, or person the operator does not own or is not authorised to monitor.
- Packet capture, traffic inspection, or reading other users' data from the network.
- Productivity scoring, attendance discipline, or per-employee reports (see §6.4).
- Life-safety use. This is not a certified alarm system.

## 4. Architecture

```
┌──────────────┐   samples   ┌─────────────┐  features  ┌────────────┐  states  ┌──────────────┐
│ SignalSource │ ──────────▶ │  Sampler    │ ─────────▶ │  Detector  │ ───────▶ │ Mode policy  │
│ (CoreWLAN)   │   ~5 Hz     │ ring buffer │  per 1 s   │ (baseline +│          │ (home / work)│
└──────────────┘             └─────────────┘            │  threshold)│          └──────┬───────┘
                                                        └────────────┘                 │
                                  ┌──────────────┬───────────────┬─────────────────────┤
                                  ▼              ▼               ▼                     ▼
                              SQLite store   Notifier      Local dashboard        Audit log
```

### 4.1 SignalSource
- Interface: `start()`, `stop()`, `read() -> Sample`.
- `Sample = {ts, rssi_dbm, noise_dbm, tx_rate_mbps, channel, bssid}`.
- v1 implementation: `CoreWLANSource` (Python via `pyobjc-framework-CoreWLAN`).
- If the BSSID or channel changes (roaming, router reboot), emit a `link_changed` event and
  force recalibration. Never mix baselines across links.
- `ReplaySource` reads a recorded CSV, used for tests and tuning.

### 4.2 Sampler
- Polls the source at a configurable rate (default 5 Hz).
- Keeps a 60-second ring buffer.
- Every 1 second emits a feature window over the last 5 seconds:
  - `rssi_std`, `rssi_range`, `rssi_mad` (median absolute deviation)
  - `delta_energy` = mean of squared first differences of RSSI
  - `tx_rate_changes` = count of rate changes (rate adaptation reacts to motion)

### 4.3 Detector
- **Calibration**: user starts calibration with the space empty and still. Collect 3 minutes
  (configurable). Store per-feature median and MAD as the baseline for that BSSID+channel.
- **Score**: weighted sum of robust z-scores of each feature against the baseline, clipped
  to [0, 10]. Weights live in config.
- **State machine** with hysteresis:
  - `idle → motion` when score ≥ `trigger` (default 3.5) for ≥ `trigger_secs` (default 3).
  - `motion → idle` when score < `release` (default 2.0) for ≥ `release_secs` (default 10).
- **Confidence**: reported as `low / medium / high` from score margin and calibration quality.
- **Calibration quality**: `poor` if baseline RSSI std > 3 dB or too few samples. Poor quality
  shows a warning in the dashboard and blocks arming in Home mode.
- **Baseline drift**: while `idle` for a long time, slowly update the baseline (EMA, α small)
  so temperature and furniture changes don't cause false alarms. Never update during `motion`.

### 4.4 Storage (SQLite, `~/Library/Application Support/wifi_utility/data.db`)
| Table | Columns | Notes |
|---|---|---|
| `samples` | ts, rssi, noise, tx_rate, channel, bssid | Home mode only, if debug capture enabled; 24 h retention |
| `events` | id, mode, start_ts, end_ts, peak_score, confidence | Home mode motion events |
| `occupancy` | zone_id, bucket_start, occupied (bool) | Workplace mode only; 15-min buckets |
| `baselines` | bssid, channel, created_ts, stats_json | |
| `policy` | version, text_hash, published_ts, active | Workplace mode |
| `acknowledgements` | policy_version, ack_ts, ack_by | See §6.3 |
| `audit` | ts, actor, action, detail | Append-only |

Retention is enforced by a daily job and is configurable within the limits in §6.

### 4.5 Notifier (Home mode)
- macOS user notification (via `osascript` or `UserNotifications`).
- Optional webhook (POST JSON) and optional ntfy.sh-style push topic, both off by default.
- Rate limit: max 1 alert per event, and max 1 alert per 2 minutes across events.

### 4.6 Local dashboard
- Served on `127.0.0.1` only (default port 8765). No external binding unless explicitly set,
  and then only with a password.
- Views: live view (§4.7b), calibration quality, event list (Home), occupancy
  heatmap (Workplace), policy page (Workplace), audit log.

### 4.7 On-screen motion display
Motion is shown on screen in three places. All of them read the same detector output stream
(`{ts, score, state, confidence, calibration_quality}`) published once per second.

**a) Menu bar indicator** (always visible while running)
- Icon colour: grey = idle, amber = score rising above `release`, red = `motion`.
  A slash through the icon = not calibrated or calibration `poor`.
- Clicking the icon opens a dropdown with: current state, score, confidence, armed/disarmed
  (Home), time of last motion event, and "Open live view".
- Built with `rumps` (pyobjc wrapper), running in the same process as the engine.

**b) Live view window** (dashboard page `/live`, opened from the menu bar or browser)
- Large state banner: `NO MOTION` / `MOTION DETECTED`, with confidence and time since change.
- Rolling 2-minute chart of the motion score, with horizontal lines at `trigger` and `release`
  and shaded bands for periods in `motion`.
- Secondary chart of raw RSSI (dBm) on the same time axis, so the user can see the signal
  the score comes from. Home mode only.
- Signal strip: RSSI, noise, tx rate, channel, link quality.
- Updates over a WebSocket (`/ws/live`) at 1 Hz; the page reconnects automatically.
- Optional full-screen mode (`F` key) for a spare monitor or tablet on the LAN (requires the
  non-local bind + password from §4.6).
- Optional on-screen flash + sound when a Home-mode alert fires while the view is open.

**c) Terminal live view** (`wifi-utility watch`)
- One line per second: time, sparkline of the last 60 scores, current score, state, RSSI.
- State changes printed in colour (red = motion, green = idle). Works over SSH; no GUI needed.

**Workplace mode restrictions** (consistent with §6.4)
- No live score, raw RSSI chart or second-by-second state is shown on any screen.
- The menu bar and live view show only the current 15-minute bucket as `Occupied` / `Vacant`
  for the zone, plus "Collection active under policy vN".
- `wifi-utility watch` refuses to run in workplace mode.

### 4.8 CLI
```
wifi-utility calibrate [--secs 180]
wifi-utility run --mode home|workplace [--no-menubar]
wifi-utility watch                      # live motion in the terminal (home mode)
wifi-utility live                       # open the live view window in the browser
wifi-utility arm | disarm               # home mode
wifi-utility policy publish <file.md>   # workplace mode
wifi-utility policy status
wifi-utility record <out.csv> --secs N  # raw samples for tuning
wifi-utility replay <in.csv> [--report] # run detector offline; --report scores against <in>.labels.csv
wifi-utility export --since <date>
```

## 5. Home Security mode

- **Armed / disarmed** states. Alerts fire only when armed.
- Optional schedule (e.g. armed 09:00–17:00 weekdays) and an exit delay (default 60 s) after
  arming so the owner can leave.
- Event record holds start, end, peak score and confidence. No raw samples are kept unless
  debug capture is on.
- Intended for spaces the operator owns or controls. The dashboard shows a one-line notice at
  setup: tell other household members or guests that the sensor is running.

## 6. Workplace Occupancy mode

This mode exists only alongside a disclosed monitoring policy. The software enforces the
disclosure rather than leaving it to the operator's memory.

### 6.1 Hard gates (the mode will not start without these)
1. A policy document is published with `wifi-utility policy publish`. The file must fill in
   every field in the template (§6.2); missing fields are rejected.
2. The policy has an effective date at least the configured notice period in the future at
   publish time (default 7 days; set it to whatever local law requires).
3. A physical-notice confirmation: the operator confirms that a printed notice is posted in
   the monitored zone. Recorded in the audit log.

### 6.2 Policy template (`policy/TEMPLATE.md`)
Required fields:
- Employer name and contact for questions
- Zones monitored and where the sensor is
- What is measured: "motion-based occupancy of the zone, in 15-minute buckets"
- What is **not** measured: identity, individual presence, device data, network traffic,
  audio, video
- Purpose (e.g. meeting-room utilisation, energy/HVAC scheduling, after-hours security)
- Hours of operation
- Retention period
- Who can access the data
- How employees can see the data and raise concerns
- Applicable law / works-council or union consultation status (free text, operator's responsibility)

### 6.3 Acknowledgement
- The dashboard serves a read-only policy page that anyone on the LAN can open (separate from
  the admin dashboard).
- Acknowledgements are recorded as count + timestamp. `ack_by` stores a name only if the
  employer's policy says it does; the default is anonymous.
- Republishing a changed policy creates a new version, restarts the notice period, and
  stops collection until the new version takes effect.

### 6.4 Data minimisation (enforced in code, not config)
- No raw samples are stored in this mode. Debug capture is disabled.
- Only `occupied: bool` per zone per 15-minute bucket is persisted.
- No real-time alerts on motion during working hours. The only optional alert is an
  after-hours alert, and it must be listed as a purpose in the policy.
- Maximum retention: 90 days. Config cannot exceed it.
- Exports are aggregate only (CSV of zone × bucket). There is no per-person view because the
  data cannot support one, and the UI must not imply otherwise.
- Every dashboard view, export and config change is written to the audit log.

### 6.5 Transparency view
The public policy page also shows: whether collection is currently active, the current
policy version, and the last 7 days of aggregate occupancy for each zone. Employees see the
same data the employer sees.

## 7. Configuration (`config.toml`)

```toml
mode = "home"                 # "home" | "workplace"
sample_hz = 5
window_secs = 5

[detector]
trigger = 3.5
trigger_secs = 3
release = 2.0
release_secs = 10
calibration_secs = 180
drift_alpha = 0.001
weights = { rssi_std = 1.0, rssi_mad = 1.0, delta_energy = 1.5, tx_rate_changes = 0.5 }

[home]
exit_delay_secs = 60
schedule = []                 # e.g. ["mon-fri 09:00-17:00"]
webhook_url = ""
debug_capture = false

[workplace]
zone_id = "meeting-room-1"
notice_days = 7
retention_days = 90           # hard max 90
after_hours_alert = false

[dashboard]
bind = "127.0.0.1"
port = 8765
```

## 8. Tech stack

- Python 3.12+, `pyobjc-framework-CoreWLAN`, `numpy`
- SQLite (stdlib `sqlite3`)
- Dashboard: FastAPI (+ WebSocket) + static HTML pages with a small chart library (uPlot)
- Menu bar: `rumps`
- Packaging: `uv` project; a `launchd` plist for running at login
- macOS 14+ (tested on macOS 26)

### Project layout
```
wifi_utility/
  pyproject.toml
  config.example.toml
  policy/TEMPLATE.md
  src/wifi_utility/
    sources/{base.py, corewlan.py, replay.py}
    sampler.py
    detector.py
    modes/{home.py, workplace.py}
    store.py
    notifier.py
    display/{stream.py, menubar.py, terminal.py}
    dashboard/{app.py, static/{index.html, live.html}}
    cli.py
  tests/
    fixtures/{empty_room.csv, walking.csv, fan_running.csv}
    test_detector.py
    test_workplace_gates.py
```

## 9. Testing and acceptance

**Unit**
- Detector state machine: hysteresis, minimum durations, drift only updates while idle.
- Workplace gates: refuses to start without a complete policy, before the effective date,
  or without the notice confirmation; refuses retention > 90; refuses debug capture.
- Display: the stream emits one update per second; in workplace mode the stream carries only
  bucket occupancy (no score/RSSI) and `watch` exits with an error.

**Recorded-data (ReplaySource)**
- Record at least: empty room (30 min), person walking through the Mac–router path, person
  walking outside it, person sitting still, fan running, door opening.

**Acceptance targets on recorded data at the default thresholds**
| Scenario | Target |
|---|---|
| Walking across Mac–router path | ≥ 90% of passes detected, latency ≤ 10 s |
| Empty room, 8 h | ≤ 1 false event |
| Person sitting still | Not required to detect (documented limitation) |
| Walking outside the path | Report measured rate; no target in v1 |

## 10. Milestones

1. **M1 Sensing**: CoreWLAN source, record/replay, `wifi-utility watch` showing live RSSI.
2. **M2 Detector**: calibration, state machine, test fixtures, tune thresholds; `watch`
   shows score and state.
3. **M3 Home mode**: arm/disarm, events, notifications, dashboard v1, live view window,
   menu bar indicator.
4. **M4 Workplace mode**: policy publish and gates, occupancy buckets, public policy page, audit log.
5. **M5 Packaging**: launchd service, config validation, README.
6. **v2 (later)**: ESP32 CSI `SignalSource`, multi-zone.

## 11. Open questions

- Should Home mode support more than one Mac as a sensor (each with its own link to the router)?
- Location Services prompt: ship as a signed `.app` bundle (needed for the permission dialog) or
  as a CLI that tells the user how to grant it?
- Workplace mode: which jurisdictions to provide sample policy wording for? (Legal review needed
  before shipping any sample text.)
