# Detector math

Exact definitions for `sampler.py` and `detector.py` (SPEC §4.2–4.3). Tests should assert
these formulas, so change this file and the code together.

## Feature window

Every 1 s, take RSSI values `r[0..n-1]` and tx rates `t[0..n-1]` from the last
`window_secs` (default 5 s → n ≈ 25 at 5 Hz). Skip the window if n < 0.6 × expected.

| Feature | Definition |
|---|---|
| `rssi_std` | population std of `r` |
| `rssi_range` | `max(r) − min(r)` |
| `rssi_mad` | `median(|r − median(r)|)` |
| `delta_energy` | `mean((r[i] − r[i−1])²)` for i = 1..n−1 |
| `tx_rate_changes` | count of i where `t[i] ≠ t[i−1]` |

`rssi_range` is reported but not scored by default (no weight in SPEC §7).

## Baseline (calibration)

Over `calibration_secs` of an empty, still room, compute each feature per window, then per
feature `f`:

```
base_med[f] = median(f over windows)
base_mad[f] = max(median(|f − base_med[f]|) × 1.4826, floor[f])
```

`1.4826` scales MAD to be comparable to a standard deviation. `floor` prevents divide-by-near-zero
in a very stable room: `rssi_std 0.3`, `rssi_mad 0.3`, `delta_energy 0.2`, `tx_rate_changes 0.5`.

Calibration quality:
- `poor` if fewer than 60 windows, or mean `rssi_std` > 3 dB
- `fair` if mean `rssi_std` > 1.5 dB
- `good` otherwise

## Score

```
z[f]   = max(0, (x[f] − base_med[f]) / base_mad[f])     # one-sided: only increases count
score  = clip( Σ w[f]·z[f] / Σ w[f] , 0, 10 )
```

One-sided because motion raises variability; a quieter-than-baseline window is not motion.

## State machine

```
idle   → motion  when score ≥ trigger  continuously for ≥ trigger_secs
motion → idle    when score < release  continuously for ≥ release_secs
```

A single window that breaks the condition resets its timer. Defaults: trigger 3.5 / 3 s,
release 2.0 / 10 s. Require `trigger > release`; reject config otherwise.

## Confidence

In `motion` (and for the event record):

```
margin = (peak_score_in_event − trigger) / trigger
high    if margin ≥ 0.5 and quality == good
medium  if margin ≥ 0.2 and quality != poor
low     otherwise
```

In `idle`, the same bands apply to how far the score is below the trigger:
`margin = (trigger − score) / trigger`.

## Drift

After ≥ 10 min continuously `idle`, per window whose score is below `release`:

```
base_med[f] ← (1 − α)·base_med[f] + α·x[f]          # α = drift_alpha (default 0.001)
```

Never update during `motion`, while the score is rising toward `trigger`, or within 10 min
after leaving `motion` (the idle timer restarts on every exit from motion). `base_mad` is not
drifted; it only changes on recalibration.

## Workplace occupancy bucket

A 15-minute bucket is `occupied = true` if any `motion` state occurred within it. Only this
boolean is persisted (SPEC §6.4); scores and windows are discarded in memory.
