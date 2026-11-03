"""动态可观测性算法单元测试：3σ 水位线 / median-MAD / PSI。

用固定种子生成的数值验证判定核心，重点覆盖：
  * 检测正确性（尖峰/暴跌/正常日/恒定窗口）
  * 稳健性（历史窗口被异常日污染时 MAD 不漏报）
  * 降级策略（窗口不足 → 相对变化率）
  * PSI 数值性质（相同分布=0、错位分布大、零频箱无 NaN、分级阈值）
"""

from __future__ import annotations

import numpy as np
import pytest
from dq_engine.anomaly.stats import (
    mad_detect,
    psi,
    psi_from_histograms,
    psi_level,
    zscore_detect,
)


def _normal(n, loc=10.0, scale=1.0, seed=0):
    return np.random.RandomState(seed).normal(loc, scale, n).tolist()


# ---------------------------------------------------------------- 3σ / MAD


def test_zscore_spike_detected():
    r = zscore_detect(_normal(30), current=16.0)
    assert r.flagged and r.direction == "high" and r.method == "zscore"


def test_zscore_dip_detected():
    r = zscore_detect(_normal(30), current=4.0)
    assert r.flagged and r.direction == "low"


def test_zscore_normal_day_not_flagged():
    r = zscore_detect(_normal(30), current=10.5)
    assert not r.flagged and r.direction is None


def test_zscore_threshold_boundary():
    # 样本均值≈10.44、标准差≈1.10：current=13.0 → z≈2.32，k=2 检出、k=4 不检出
    r1 = zscore_detect(_normal(30), current=13.0, k=2.0)
    r2 = zscore_detect(_normal(30), current=13.0, k=4.0)
    assert r1.flagged and not r2.flagged


def test_zscore_constant_window():
    r1 = zscore_detect([10.0] * 10, current=10.0)
    r2 = zscore_detect([10.0] * 10, current=11.0)
    assert not r1.flagged and r2.flagged and r2.direction == "high"


def test_short_window_falls_back_to_relative():
    r = zscore_detect([10.0, 11.0], current=20.0)  # 窗口 < 3 → 相对变化率
    assert r.method == "relative" and r.flagged and r.direction == "high"
    r2 = zscore_detect([10.0, 11.0], current=12.0, relative_threshold=0.5)
    assert not r2.flagged


def test_empty_history_not_flagged():
    r = zscore_detect([], current=5.0)
    assert r.method == "relative" and not r.flagged


def test_mad_robust_when_history_polluted():
    """历史窗口混入 1000 的异常日：zscore 均值被拉偏导致漏报，MAD 仍能检出当日尖峰。"""
    rng = np.random.RandomState(0)
    history = np.concatenate([rng.normal(10.0, 0.5, 20), [1000.0]]).tolist()
    z = zscore_detect(history, current=60.0)
    m = mad_detect(history, current=60.0)
    assert not z.flagged  # z ≈ 0.01，被污染历史掩盖
    assert m.flagged and m.direction == "high" and m.method == "mad"


def test_mad_normal_day_not_flagged():
    r = mad_detect(_normal(30, scale=1.0), current=10.4)
    assert not r.flagged


def test_mad_short_window_relative():
    r = mad_detect([10.0, 11.0, 12.0], current=20.0)  # 窗口 < 5 → relative
    assert r.method == "relative" and r.flagged


def test_anomaly_result_to_dict():
    r = zscore_detect(_normal(30), current=16.0)
    d = r.to_dict()
    assert set(d) == {"current", "window_size", "method", "mean", "std", "z", "threshold", "flagged", "direction"}


# ---------------------------------------------------------------- PSI


def test_psi_identical_distributions_is_zero():
    x = _normal(5000)
    assert psi(x, x) < 1e-9


def test_psi_disjoint_distributions_is_large():
    e = np.random.RandomState(0).uniform(0, 1, 2000).tolist()
    a = np.random.RandomState(1).uniform(2, 3, 2000).tolist()
    assert psi(e, a) > 1.0


def test_psi_small_shift_is_moderate():
    e = _normal(20000, seed=3)
    a = _normal(20000, loc=10.15, seed=4)
    assert 0.0 < psi(e, a) < 0.25


def test_psi_histogram_consistency():
    e = _normal(5000, seed=5)
    a = _normal(5000, loc=10.2, seed=6)
    edges = np.quantile(e, np.linspace(0, 1, 11))
    edges[0], edges[-1] = -np.inf, np.inf
    e_hist, _ = np.histogram(e, bins=edges)
    a_hist, _ = np.histogram(a, bins=edges)
    assert abs(psi(e, a) - psi_from_histograms(e_hist, a_hist)) < 1e-12


def test_psi_histogram_with_zero_bins_is_finite():
    v = psi_from_histograms([100.0, 50.0, 0.0, 10.0], [80.0, 60.0, 5.0, 5.0])
    assert np.isfinite(v) and v > 0


def test_psi_constant_expected_raises():
    with pytest.raises(ValueError, match="常数"):
        psi([5.0] * 100, [1.0, 2.0, 3.0])


def test_psi_empty_raises():
    with pytest.raises(ValueError, match="不能为空"):
        psi([], [1.0])


def test_psi_level_thresholds():
    assert psi_level(0.05) == "PASS"
    assert psi_level(0.15) == "WARN"
    assert psi_level(0.30) == "ALERT"
