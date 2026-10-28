---
name: workplace-guard
description: Check a change against the workplace-mode policy and data-minimisation invariants in SPEC §6 and §4.7. Use before finishing any change that touches modes/workplace.py, store.py, the dashboard, display/, export, config validation or retention, and whenever a request would add data, views or alerts in workplace mode.
---

# Workplace mode guard

Workplace mode is only acceptable because the software enforces a disclosed policy and keeps
almost nothing. This skill checks a change hasn't eroded that.

## 1. Find what changed

```bash
git diff --stat            # or the files you edited, if not a git repo yet
```

Focus on anything reachable when `mode = "workplace"`.

## 2. Check each invariant

| # | Invariant | Where to look |
|---|---|---|
| 1 | Won't start without a complete policy, effective date reached, and posted-notice confirmation | `modes/workplace.py` startup |
| 2 | Policy change → new version, notice period restarts, collection stops until effective | policy publish path |
| 3 | Only `occupied: bool` per zone per 15-min bucket is persisted | `store.py`, any new table/column |
| 4 | No raw samples, scores or debug capture stored | `store.py`, sampler hooks, logging |
| 5 | Retention ≤ 90 days; higher config is rejected, not clamped | config validation |
| 6 | No real-time alerts in working hours; after-hours alert only if in policy purposes | `notifier.py`, mode policy |
| 7 | Screens show only current-bucket Occupied/Vacant; `watch` refuses | `display/`, `/live`, `/ws/live` |
| 8 | Exports are zone × bucket only | export command |
| 9 | Every view, export and config change is audited (append-only) | dashboard routes, CLI |
| 10 | Public policy page shows active status, policy version, 7-day aggregate | public page |
| 11 | Nothing identifies, counts or tracks individuals | whole diff, incl. UI text |

Also check indirect leaks: log lines at INFO/DEBUG that print scores or RSSI in workplace mode,
WebSocket payloads with extra fields, API responses that include more than the UI shows.

## 3. Tests

Every invariant must have a test in `tests/test_workplace_gates.py`. Run:

```bash
uv run pytest tests/test_workplace_gates.py -v
```

If the change touches an invariant without a test, write the test.

## 4. If a request conflicts

If the user asked for something that would break an invariant (per-person views, live score
in workplace mode, longer retention, skipping the notice period), don't implement it. Explain
which invariant and why, and offer an alternative that fits (e.g. more zones, coarser buckets,
home mode for spaces the user owns). If they still want it, it's a spec change; the decision
needs to be made in SPEC §6 first.

## 5. Report

List each invariant as pass / fail / not affected, with file:line for any fail.
