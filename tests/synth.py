"""Deterministic synthetic RSSI recordings for tests.

This is a model, not real data. It shows the detector logic behaves as designed; it does NOT
prove SPEC §9 accuracy on a real room. Use the record-fixture skill for that.
"""

from __future__ import annotations

import math
import random

from wifi_utility.evaluate import Label
from wifi_utility.sources.base import Sample

BSSID = "00:00:00:00:00:01"
CHANNEL = 36
RATES = [585.0, 650.0, 702.0, 780.0, 866.0]


def generate(
    duration: float,
    *,
    hz: float = 5,
    t0: float = 1_800_000_000.0,
    base: float = -55.0,
    noise: float = 0.6,
    passes: list[tuple[float, float]] = (),
    depth: float = 5.0,
    seed: int = 1,
    bssid: str = BSSID,
    channel: int = CHANNEL,
) -> tuple[list[Sample], list[Label]]:
    """`passes` are (start_offset, duration) of someone walking across the Mac–router path."""
    rng = random.Random(seed)
    samples: list[Sample] = []
    rate_i = 3
    held = None
    n = int(duration * hz)
    for i in range(n):
        t = i / hz
        moving = next(((s, d) for s, d in passes if s <= t < s + d), None)
        value = base + rng.gauss(0, noise)
        if moving:
            s, d = moving
            value += -depth * math.sin(math.pi * (t - s) / d) + rng.gauss(0, 2.5)
        # The driver refreshes RSSI roughly every other poll.
        if held is None or i % 2 == 0 or moving:
            held = round(value)
        if rng.random() < (0.15 if moving else 0.004):
            rate_i = min(len(RATES) - 1, max(0, rate_i + rng.choice([-1, 1])))
        samples.append(
            Sample(
                ts=t0 + t,
                rssi_dbm=int(held),
                noise_dbm=-94,
                tx_rate_mbps=RATES[rate_i],
                channel=channel,
                bssid=bssid,
            )
        )
    labels = [Label(t0 + s, t0 + s + d, "walk_on_path") for s, d in passes]
    return samples, labels


def walking(seed: int = 2, calm_secs: float = 240, n_passes: int = 12):
    passes = [(calm_secs + k * 45.0, 6.0 + (k % 3) * 2) for k in range(n_passes)]
    return generate(calm_secs + n_passes * 45.0 + 30, passes=passes, seed=seed)
