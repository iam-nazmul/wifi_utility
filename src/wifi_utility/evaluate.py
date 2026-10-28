"""Score detector output against labelled fixtures (SPEC §9, `replay --report`)."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from .detector import MOTION, DetectorOutput, MotionEvent

LATENCY_TARGET_SECS = 10
EIGHT_HOURS = 8 * 3600


@dataclass(frozen=True)
class Label:
    start_ts: float
    end_ts: float
    label: str


@dataclass
class Report:
    duration_secs: float
    events: list[MotionEvent]
    by_label: dict[str, dict] = field(default_factory=dict)
    false_events: int = 0

    @property
    def false_per_8h(self) -> float:
        return self.false_events * EIGHT_HOURS / self.duration_secs if self.duration_secs else 0.0

    def lines(self) -> list[str]:
        out = [
            f"duration: {self.duration_secs / 60:.1f} min, motion events: {len(self.events)}",
            f"false events: {self.false_events} ({self.false_per_8h:.2f} per 8 h; target ≤ 1)",
        ]
        for label, m in sorted(self.by_label.items()):
            lat = f"{m['median_latency']:.1f}s" if m["median_latency"] is not None else "-"
            out.append(
                f"{label}: detected {m['detected']}/{m['total']} ({m['rate']:.0%}), "
                f"median latency {lat}"
            )
        return out


def labels_path_for(samples_path: str | Path) -> Path:
    p = Path(samples_path)
    return p.with_name(p.stem + ".labels.csv")


def read_labels(path: str | Path) -> list[Label]:
    with Path(path).open(newline="") as f:
        return [
            Label(float(r["start_ts"]), float(r["end_ts"]), r["label"]) for r in csv.DictReader(f)
        ]


def events_from(outputs: list[DetectorOutput]) -> list[MotionEvent]:
    events = [o.ended for o in outputs if o.ended]
    if outputs and outputs[-1].state == MOTION:
        started = [o.started for o in outputs if o.started]
        if started:
            events.append(started[-1])  # still open at end of recording
    return events


def evaluate(outputs: list[DetectorOutput], labels: list[Label], grace_secs: float) -> Report:
    duration = outputs[-1].ts - outputs[0].ts if len(outputs) > 1 else 0.0
    events = events_from(outputs)
    report = Report(duration_secs=duration, events=events)

    def overlaps(e: MotionEvent, lab: Label) -> bool:
        end = e.end_ts if e.end_ts is not None else e.detected_ts
        return e.detected_ts <= lab.end_ts + grace_secs and end >= lab.start_ts

    for name in sorted({lab.label for lab in labels}):
        group = [lab for lab in labels if lab.label == name]
        latencies = []
        for lab in group:
            hits = [e for e in events if overlaps(e, lab)]
            if hits:
                latencies.append(max(0.0, min(e.detected_ts for e in hits) - lab.start_ts))
        report.by_label[name] = {
            "total": len(group),
            "detected": len(latencies),
            "rate": len(latencies) / len(group),
            "median_latency": median(latencies) if latencies else None,
        }
    report.false_events = sum(1 for e in events if not any(overlaps(e, lab) for lab in labels))
    return report
