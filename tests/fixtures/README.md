# Fixtures

Real recordings for detector tuning (SPEC §9). **None recorded yet.** Unit tests use synthetic
data from `tests/synth.py` instead; recordings here are what prove accuracy in a real room.

Record with the `record-fixture` skill or:

```bash
uv run wifi-utility record tests/fixtures/<name>.csv --secs <N>
```

Formats: `.claude/references/data-formats.md`. Replace the real BSSID with
`00:00:00:00:00:01` before committing (a hook blocks commits otherwise). For scenarios with
motion, add `<name>.labels.csv` (`start_ts,end_ts,label`).

| File | Date | Room | Mac–router distance | Router | Notes |
|---|---|---|---|---|---|
