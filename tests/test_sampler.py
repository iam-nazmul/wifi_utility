import numpy as np
import pytest

from wifi_utility.sampler import Sampler, compute_features
from wifi_utility.sources.base import Sample


def s(ts, rssi, rate=650.0):
    return Sample(ts, rssi, -94, rate, 36, "00:00:00:00:00:01")


def test_features_match_reference_formulas():
    rssi = np.array([-50.0, -52.0, -50.0, -54.0])
    tx = np.array([650.0, 650.0, 780.0, 650.0])
    f = compute_features(10.0, rssi, tx)
    assert f.rssi_std == pytest.approx(np.std(rssi))
    assert f.rssi_range == 4
    assert f.rssi_mad == pytest.approx(1.0)  # median -51; |dev| = 1,1,1,3 → 1
    assert f.delta_energy == pytest.approx((4 + 4 + 16) / 3)
    assert f.tx_rate_changes == 2


def test_window_needs_enough_samples():
    sampler = Sampler(sample_hz=5, window_secs=5)
    for i in range(10):  # 2 s of data, 40% of the window
        sampler.add(s(100 + i * 0.2, -50))
    assert sampler.features(102.0) is None
    for i in range(10, 25):
        sampler.add(s(100 + i * 0.2, -50))
    f = sampler.features(105.0)
    assert f is not None and f.n == 24  # window is (100, 105]: the sample at 100.0 is excluded


def test_ring_buffer_keeps_60_seconds():
    sampler = Sampler()
    for i in range(0, 100):
        sampler.add(s(float(i), -50))
    assert len(sampler.recent(1000, 99.0)) == 61
