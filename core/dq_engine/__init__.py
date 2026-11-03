"""DataPulse 质检引擎核心包（★ 项目主线）。

模块规划（随里程碑逐步填充）：
  rules/     YAML 规则契约 → Spark SQL 单次扫描编译器（M2）
  anomaly/   3σ 滑动水位线、PSI 分布漂移检测（M3）
  lineage/   SQLGlot 字段级血缘解析与 NetworkX 血缘图（M4）
  ai/        AI 规则 Copilot、语义质检、AI-RCA（M2/M3/M4）
"""

__version__ = "0.1.0"
