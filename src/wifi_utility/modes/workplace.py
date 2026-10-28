"""Workplace Occupancy mode (SPEC §6). Collection only runs under a disclosed, effective policy.

Invariants (do not weaken; see CLAUDE.md):
- won't start without a complete policy, its effective date reached, and posted-notice confirmation
- persists only occupied/vacant per zone per 15-minute bucket
- no real-time alerts in working hours; after-hours alert only if listed as a purpose
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from ..config import MAX_RETENTION_DAYS, Config
from ..detector import MOTION, DetectorOutput
from ..notifier import Notifier
from ..schedule import ScheduleError, Window, in_any, parse_windows
from ..store import Store

BUCKET_SECS = 15 * 60
DAY = 86400
STATUS_RECHECK_SECS = 60

REQUIRED_SECTIONS = [
    "Employer and contact",
    "Zones and sensor location",
    "What is measured",
    "What is not measured",
    "Purpose",
    "Hours of operation",
    "Retention period",
    "Who can access the data",
    "How to see the data and raise concerns",
    "Applicable law and consultation",
    "Effective date",
    "Acknowledgement records names",
]
NOT_MEASURED_TERMS = ["identity", "individual", "device", "network traffic", "audio", "video"]
PLACEHOLDER_RE = re.compile(r"<[^>]*>|\bTODO\b|\bTBD\b", re.IGNORECASE)


class PolicyError(ValueError):
    pass


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    sections: dict[str, str]
    effective_ts: float
    hours: list[Window]
    retention_days: int
    records_names: bool
    after_hours_purpose: bool


def bucket_start(ts: float) -> int:
    return int(ts // BUCKET_SECS * BUCKET_SECS)


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1)
            out[current] = ""
        elif current is not None:
            out[current] += line + "\n"
    return {k: v.strip() for k, v in out.items()}


def parse_policy(text: str) -> Policy:
    sections = _sections(text)
    problems = []
    for name in REQUIRED_SECTIONS:
        body = sections.get(name, "")
        if not body:
            problems.append(f"missing or empty section: ## {name}")
        elif PLACEHOLDER_RE.search(body):
            problems.append(f"section '## {name}' still contains a placeholder")
    if problems:
        raise PolicyError("\n".join(problems))

    measured = sections["What is measured"].lower()
    if "occupancy" not in measured or "15-minute" not in measured:
        problems.append("'What is measured' must state 15-minute occupancy of the zone")
    not_measured = sections["What is not measured"].lower()
    missing = [t for t in NOT_MEASURED_TERMS if t not in not_measured]
    if missing:
        problems.append(f"'What is not measured' must mention: {', '.join(missing)}")

    effective_ts = 0.0
    try:
        effective_ts = datetime.strptime(
            sections["Effective date"].split()[0], "%Y-%m-%d"
        ).timestamp()
    except ValueError:
        problems.append("'Effective date' must start with a date as YYYY-MM-DD")

    hours: list[Window] = []
    try:
        lines = [ln.lstrip("-* ").strip() for ln in sections["Hours of operation"].splitlines()]
        hours = parse_windows([ln for ln in lines if ln])
    except ScheduleError as e:
        problems.append(f"'Hours of operation': {e}")

    retention = re.search(r"(\d+)\s*days?", sections["Retention period"], re.IGNORECASE)
    retention_days = int(retention.group(1)) if retention else 0
    if not retention:
        problems.append("'Retention period' must state a number of days")
    elif not 1 <= retention_days <= MAX_RETENTION_DAYS:
        problems.append(f"'Retention period' must be 1..{MAX_RETENTION_DAYS} days")

    names = sections["Acknowledgement records names"].strip().lower()
    if names.split()[0] not in ("yes", "no"):
        problems.append("'Acknowledgement records names' must be 'yes' or 'no'")

    if problems:
        raise PolicyError("\n".join(problems))
    purpose = sections["Purpose"].lower()
    return Policy(
        sections=sections,
        effective_ts=effective_ts,
        hours=hours,
        retention_days=retention_days,
        records_names=names.startswith("yes"),
        after_hours_purpose="after-hours" in purpose or "after hours" in purpose,
    )


def publish(store: Store, cfg: Config, text: str, now: float, actor: str) -> int:
    policy = parse_policy(text)
    earliest = now + cfg.workplace.notice_days * DAY
    if policy.effective_ts < earliest:
        raise PolicyError(
            f"effective date must be at least {cfg.workplace.notice_days:g} days after publishing "
            f"(earliest {datetime.fromtimestamp(earliest):%Y-%m-%d %H:%M})"
        )
    digest = hashlib.sha256(text.encode()).hexdigest()
    active = store.active_policy()
    if active and active["text_hash"] == digest:
        raise PolicyError(f"policy unchanged; version {active['version']} is already published")
    version = store.add_policy(digest, now, policy.effective_ts, text)
    store.audit(
        now,
        actor,
        "policy_published",
        f"version={version} effective={datetime.fromtimestamp(policy.effective_ts):%Y-%m-%d}",
    )
    return version


def confirm_notice(store: Store, now: float, actor: str) -> int:
    active = store.active_policy()
    if not active:
        raise PolicyError("no published policy")
    store.confirm_notice(active["version"], now)
    store.audit(now, actor, "notice_posted", f"version={active['version']}")
    return active["version"]


def record_ack(store: Store, now: float, name: str | None) -> int:
    active = store.active_policy()
    if not active:
        raise PolicyError("no published policy")
    policy = parse_policy(active["text"])
    store.add_ack(active["version"], now, (name or None) if policy.records_names else None)
    return active["version"]


@dataclass(frozen=True)
class Status:
    collecting: bool
    reason: str
    version: int | None
    policy: Policy | None


def collection_status(store: Store, cfg: Config, now: float) -> Status:
    active = store.active_policy()
    if not active:
        return Status(False, "no policy published (wifi-utility policy publish)", None, None)
    version = active["version"]
    policy = parse_policy(active["text"])
    if now < active["effective_ts"]:
        when = datetime.fromtimestamp(active["effective_ts"])
        return Status(False, f"policy v{version} takes effect {when:%Y-%m-%d}", version, policy)
    if not active["notice_confirmed_ts"]:
        return Status(
            False,
            f"posted notice not confirmed for v{version} (wifi-utility policy confirm-notice)",
            version,
            policy,
        )
    if cfg.workplace.after_hours_alert and not policy.after_hours_purpose:
        return Status(
            False, "after_hours_alert is on but the policy purpose doesn't list it", version, policy
        )
    return Status(True, f"collection active under policy v{version}", version, policy)


class WorkplaceMode:
    name = "workplace"

    def __init__(self, cfg: Config, store: Store, notifier: Notifier, now: float):
        if not store.workplace:
            raise GateError("workplace mode requires a workplace store")
        status = collection_status(store, cfg, now)
        if not status.collecting:
            raise GateError(status.reason)
        self.cfg = cfg
        self.store = store
        self.notifier = notifier
        self.zone_id = cfg.workplace.zone_id
        self.status = status
        self._checked_at = now
        self._bucket: int | None = None
        self._occupied = False
        store.audit(now, "engine", "collection_started", f"zone={self.zone_id} v{status.version}")

    @property
    def retention_days(self) -> int:
        policy_days = self.status.policy.retention_days if self.status.policy else 0
        return min(self.cfg.workplace.retention_days, policy_days or MAX_RETENTION_DAYS)

    def on_sample(self, sample) -> None:
        pass  # raw samples are never stored in workplace mode

    def on_output(self, out: DetectorOutput | None, now: float) -> None:
        if now - self._checked_at >= STATUS_RECHECK_SECS:
            was = self.status.collecting
            self.status = collection_status(self.store, self.cfg, now)
            self._checked_at = now
            if was != self.status.collecting:
                self.store.audit(now, "engine", "collection_changed", self.status.reason)
        if not self.status.collecting:
            self._bucket, self._occupied = None, False
            return

        bucket = bucket_start(now)
        if bucket != self._bucket:
            self._bucket, self._occupied = bucket, False
            self.store.mark_occupancy(self.zone_id, bucket, False)
        if out is not None and out.state == MOTION and not self._occupied:
            self._occupied = True
            self.store.mark_occupancy(self.zone_id, bucket, True)
        if out is not None and out.started:
            self._maybe_after_hours_alert(now, bucket)

    def _maybe_after_hours_alert(self, now: float, bucket: int) -> None:
        policy = self.status.policy
        if not (self.cfg.workplace.after_hours_alert and policy and policy.after_hours_purpose):
            return
        if in_any(policy.hours, datetime.fromtimestamp(now)):
            return  # never alert during working hours
        self.notifier.alert(
            key=("after_hours", bucket),
            ts=now,
            title="After-hours occupancy",
            body=f"Zone {self.zone_id} became occupied outside working hours",
            payload={
                "event": "after_hours_occupancy",
                "zone_id": self.zone_id,
                "bucket_start": bucket,
            },
        )

    def message(self, now: float) -> dict:
        collecting = self.status.collecting
        return {
            "mode": "workplace",
            "zone_id": self.zone_id,
            "bucket_start": bucket_start(now),
            "occupied": bool(collecting and self._occupied),
            "policy_version": self.status.version,
            "collection_active": collecting,
        }
