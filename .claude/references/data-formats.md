# Data formats

## Sample CSV (`record` output, `replay` input, fixtures)

```
ts,rssi_dbm,noise_dbm,tx_rate_mbps,channel,bssid
1793234112.204,-52,-94,866.0,36,00:00:00:00:00:01
```

- `ts`: Unix seconds, float, monotonic within the file.
- `rssi_dbm`, `noise_dbm`: int. Rows with RSSI 0 (not associated) are not written.
- `tx_rate_mbps`: float. `channel`: int. `bssid`: lower-case colon hex.
- Committed fixtures use a placeholder BSSID, consistent within a file.
- A BSSID or channel change inside a fixture makes it invalid for tuning.

## Labels CSV (`<fixture>.labels.csv`)

```
start_ts,end_ts,label
1793234130.0,1793234138.5,walk_on_path
```

Labels: `walk_on_path`, `walk_off_path`, `sit_still`, `door`, `fan_on`. Periods not covered
by a label are treated as "no motion" when scoring false events.

## Display stream message (`display/stream.py`, `/ws/live`)

Published once per second. Fields depend on mode — the stream itself enforces this, not the UI.

**Home mode**

```json
{
  "mode": "home",
  "ts": 1793234140.0,
  "state": "motion",
  "score": 5.2,
  "trigger": 3.5,
  "release": 2.0,
  "confidence": "medium",
  "calibration_quality": "good",
  "armed": true,
  "signal": {"rssi_dbm": -55, "noise_dbm": -94, "tx_rate_mbps": 650.0, "channel": 36},
  "last_event_ts": 1793234133.0
}
```

**Workplace mode** (nothing else allowed — SPEC §4.7, §6.4)

```json
{
  "mode": "workplace",
  "zone_id": "meeting-room-1",
  "bucket_start": 1793233800,
  "occupied": true,
  "policy_version": 2,
  "collection_active": true
}
```

## Webhook payload (Home mode alert)

```json
{"event": "motion", "start_ts": 1793234133.0, "peak_score": 5.2,
 "confidence": "medium", "host": "<mac hostname>"}
```

No raw samples or BSSID in outbound payloads.

## Export CSV (Workplace mode)

```
zone_id,bucket_start_iso,occupied
meeting-room-1,2026-10-28T09:00:00Z,1
```
