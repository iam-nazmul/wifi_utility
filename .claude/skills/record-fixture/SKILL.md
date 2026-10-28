---
name: record-fixture
description: Record a labelled WiFi signal fixture (CSV) from the live router link into tests/fixtures for detector tests and tuning. Use when the user wants to capture a scenario such as an empty room, walking, a fan, or a door, or when a test needs a fixture that doesn't exist yet.
---

# Record a test fixture

Fixtures are raw samples recorded with `wifi-utility record` and replayed with `ReplaySource`
(SPEC §4.1, §9). Tests depend only on these files, never on live WiFi. CSV and labels schema:
`.claude/references/data-formats.md`.

## Scenarios (SPEC §9)

| File | What the user does | Min length |
|---|---|---|
| `empty_room.csv` | Leave the room; nothing moving | 30 min |
| `walking.csv` | Walk across the Mac–router line, pause, repeat | 5 min, ≥ 10 passes |
| `walking_off_path.csv` | Walk in the room but away from that line | 5 min |
| `sitting_still.csv` | Sit still in the room | 10 min |
| `fan_running.csv` | Room empty, fan on | 10 min |
| `door.csv` | Room empty, open/close a door a few times | 5 min |

## Steps

1. Confirm the scenario and length with the user. They have to perform it; you can't.
2. Record:
   ```bash
   uv run wifi-utility record tests/fixtures/<name>.csv --secs <N>
   ```
   Run it in the background for long recordings.
3. For scenarios with motion, ask the user for the times of each pass (or have them say "now"
   while you note timestamps). Write them to `tests/fixtures/<name>.labels.csv` with columns
   `start_ts,end_ts,label`. Detector accuracy tests use these labels as ground truth.
4. Check the file: same BSSID and channel throughout (a change means the router roamed or
   rebooted — discard and re-record), expected sample rate (~5 Hz), no gaps > 2 s.
5. Add a line to `tests/fixtures/README.md`: file, date, room, Mac–router distance, router
   model if known, notes.

## Privacy

Fixtures contain only RSSI, noise, tx rate, channel and BSSID. Before committing, replace the
real BSSID with a placeholder such as `00:00:00:00:00:01` (consistently within the file). Never
record in a workplace zone for fixtures; use the user's own space.
