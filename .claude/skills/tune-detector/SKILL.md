---
name: tune-detector
description: Evaluate and tune motion detector thresholds and feature weights against recorded fixtures and the SPEC §9 acceptance targets. Use when detection misses motion, raises false alarms, after changing detector.py or sampler features, or when the user asks how accurate detection is.
---

# Tune the detector

Measure first, then change one thing at a time. Everything here runs offline on fixtures.

## 1. Baseline measurement

For each fixture in `tests/fixtures/` that has a `.labels.csv`:

```bash
uv run wifi-utility replay tests/fixtures/<name>.csv --report
```

Compute and tabulate:
- **Detection rate**: labelled motion periods with a `motion` event overlapping them.
- **Latency**: motion label start → state change to `motion`.
- **False events**: `motion` events with no overlapping label (empty_room, fan_running, door).
- **False events per 8 h**, scaled from the empty-room recording length.

Compare against SPEC §9:

| Scenario | Target |
|---|---|
| walking (on path) | ≥ 90% detected, latency ≤ 10 s |
| empty room | ≤ 1 false event per 8 h |
| sitting still | not required — don't tune for it |
| walking off path | report only |

## 2. What to adjust

The parameters are in `[detector]` in `config.toml` (SPEC §7):
- Misses or slow → lower `trigger` or `trigger_secs`, or raise the weight of `delta_energy`.
- False alarms → raise `trigger`, raise `trigger_secs`, or lower the weight of the feature that
  spikes in the false event (check per-feature z-scores in the report).
- Flapping motion↔idle → widen the gap between `trigger` and `release`, or raise `release_secs`.
- Slow drift into false alarms over hours → check `drift_alpha`; confirm drift isn't updating
  during `motion`.

Change one parameter per run and keep a table of run → params → metrics.

## 3. Avoid overfitting

- Don't tune on a single fixture. A change must not make any other fixture worse than its target.
- Don't tune to detect `sitting_still`: RSSI can't support it (SPEC §2), and trying will raise
  false alarms.
- If targets can't be met on a fixture set, say so, show the numbers, and suggest what would
  help (Mac placement, more calibration time, the v2 ESP32 CSI source) instead of forcing it.

## 4. Finish

- Update the defaults in `config.example.toml` and SPEC §7 together if the defaults change.
- Add or update a regression test in `tests/test_detector.py` that asserts the targets on the
  fixtures, so later changes can't silently regress.
- Report the before/after table to the user.
