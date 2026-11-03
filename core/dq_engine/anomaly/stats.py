"""M3 动态可观测性算法：滑动窗口 3σ 水位线与 PSI 分布漂移（纯 numpy，可单测）。

工业背景：
  * 3σ：静态规则拦不住"上游渠道断流导致订单量隐性暴跌"——
    以过去 N 天同期指标为水位线，当日值偏离窗口均值 k 个标准差即告警；
  * PSI（群体稳定性指标）：监控客群特征分布漂移，>0.25 视为强漂移
    （风控领域通行经验阈值：0.1 提示 / 0.25 强漂移）；
  * 稳健性：窗口历史若被异常日污染，均值/标准差会被拉偏导致漏报 →
    提供 median-MAD 稳健估计变体（0.6745 为 MAD→正态标准差换算常数）。

全部函数纯数学、无 I/O：Redis 水位线拉取与 Spark 直方图计算由执行器负责，
本模块只承担"可被 40+ 单测锁死"的判定核心（对应立项规格书 §四.1）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

PSI_WARN_THRESHOLD = 0.1
PSI_ALERT_THRESHOLD = 0.25
EPS = 1e-9


@dataclass(frozen=True)
class AnomalyResult:
    current: float
    window_size: int
    method: str  # zscore / mad / relative
    mean: float | None
    std: float | None
    z: float | None
    threshold: float
    flagged: bool
    direction: str | None  # "high" / "low" / None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- PSI


def psi_from_histograms(expected_hist: Sequence[float], actual_hist: Sequence[float]) -> float:
    """由两个分布直方图计算 PSI = Σ((A% - E%) · ln(A% / E%))。

    零频箱以极小值 EPS 替代，避免 ln(0) 与除零（业界通行做法，不改变量纲）。
    """
    e = np.asarray(expected_hist, dtype=float)
    a = np.asarray(actual_hist, dtype=float)
    if e.sum() <= 0 or a.sum() <= 0:
        raise ValueError("直方图不能全为 0")
    e_pct = e / e.sum()
    a_pct = a / a.sum()
    e_pct = np.where(e_pct == 0, EPS, e_pct)
    a_pct = np.where(a_pct == 0, EPS, a_pct)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


def psi(expected: Sequence[float], actual: Sequence[float], bins: int = 10) -> float:
    """连续分布 PSI：以期望分布的分位数做 bins 个等频箱，再按直方图公式计算。"""
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    if e.size == 0 or a.size == 0:
        raise ValueError("expected/actual 不能为空")
    if np.ptp(e) < EPS:
        raise ValueError("期望分布为常数，无法按分位数分箱（请改用均值偏移检测）")
    edges = np.quantile(e, np.linspace(0.0, 1.0, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e_hist, _ = np.histogram(e, bins=edges)
    a_hist, _ = np.histogram(a, bins=edges)
    return psi_from_histograms(e_hist, a_hist)


def psi_level(value: float) -> str:
    """PSI 分级：<=0.1 PASS；0.1~0.25 WARN；>0.25 ALERT（P0）。"""
    if value > PSI_ALERT_THRESHOLD:
        return "ALERT"
    if value > PSI_WARN_THRESHOLD:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------- 3σ / MAD


def _relative(history: np.ndarray, current: float, threshold: float) -> AnomalyResult:
    """窗口样本不足时的降级策略：相对变化率判定（默认 ±50% 告警）。"""
    if history.size == 0:
        return AnomalyResult(current, 0, "relative", None, None, None, threshold, False, None)
    mean = float(history.mean())
    if abs(mean) < EPS:
        flagged = abs(current) > EPS
        return AnomalyResult(
            current, history.size, "relative", mean, None, None, threshold, flagged,
            "high" if flagged and current > 0 else "low" if flagged else None,
        )
    change = (current - mean) / abs(mean)
    flagged = abs(change) > threshold
    return AnomalyResult(
        current, history.size, "relative", mean, None, round(change, 6), threshold, flagged,
        ("high" if change > 0 else "low") if flagged else None,
    )


def zscore_detect(
    history: Sequence[float],
    current: float,
    k: float = 3.0,
    min_window: int = 3,
    relative_threshold: float = 0.5,
) -> AnomalyResult:
    """滑动窗口 3σ 检测：|z| > k 即异常（z = (current - mean) / std, ddof=1）。"""
    h = np.asarray(history, dtype=float)
    if h.size < min_window:
        return _relative(h, current, relative_threshold)
    mean = float(h.mean())
    std = float(h.std(ddof=1))
    if std < EPS:
        flagged = abs(current - mean) > EPS
        return AnomalyResult(
            current, h.size, "zscore", mean, 0.0, None, k, flagged,
            ("high" if current > mean else "low") if flagged else None,
        )
    z = (current - mean) / std
    flagged = abs(z) > k
    return AnomalyResult(
        current, h.size, "zscore", mean, std, round(z, 6), k, flagged,
        ("high" if z > 0 else "low") if flagged else None,
    )


def mad_detect(
    history: Sequence[float],
    current: float,
    k: float = 3.0,
    min_window: int = 5,
    relative_threshold: float = 0.5,
) -> AnomalyResult:
    """median-MAD 稳健 3σ 变体：历史窗口被异常日污染时仍能检出当日异常。

    MAD = median(|x - median(x)|)；稳健 z = 0.6745·(current - median) / MAD。
    """
    h = np.asarray(history, dtype=float)
    if h.size < min_window:
        return _relative(h, current, relative_threshold)
    med = float(np.median(h))
    mad = float(np.median(np.abs(h - med)))
    if mad < EPS:
        flagged = abs(current - med) > EPS
        return AnomalyResult(
            current, h.size, "mad", med, 0.0, None, k, flagged,
            ("high" if current > med else "low") if flagged else None,
        )
    z = 0.6745 * (current - med) / mad
    flagged = abs(z) > k
    return AnomalyResult(
        current, h.size, "mad", med, mad, round(z, 6), k, flagged,
        ("high" if z > 0 else "low") if flagged else None,
    )
