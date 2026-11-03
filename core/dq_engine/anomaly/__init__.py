"""anomaly：3σ 滑动水位线（zscore / median-MAD）与 PSI 分布漂移检测（M3）。

已实现：
  stats.py   zscore_detect / mad_detect / psi / psi_from_histograms / psi_level

M3 待接入：Redis 30 天水位线拉取、Spark 直方图计算与熔断决策编排。
"""

from dq_engine.anomaly.stats import (
    PSI_ALERT_THRESHOLD,
    PSI_WARN_THRESHOLD,
    AnomalyResult,
    mad_detect,
    psi,
    psi_from_histograms,
    psi_level,
    zscore_detect,
)

__all__ = [
    "AnomalyResult",
    "PSI_ALERT_THRESHOLD",
    "PSI_WARN_THRESHOLD",
    "mad_detect",
    "psi",
    "psi_from_histograms",
    "psi_level",
    "zscore_detect",
]
