"""ai：规则 Copilot、语义分层抽样质检与 AI-RCA（M2/M3/M4）。

已实现（纯 Python）：
  rules_copilot.py   DDL 解析 + CSV 元数据探查 + 结构化提示词 + 生成校验（M2）
  sampling.py        2% 分层抽样（M3）
  rca.py             AI-RCA：规则化基线分类 + LLM 提示词 + 回退韧性（M4）

待接入（执行器注入在线 API）：
  semantic_check.py  LLM 语义质检评估（M3 联调）
"""

from dq_engine.ai.rca import RcaContext, RcaReport, build_report, classify_by_signals, prompt_rca
from dq_engine.ai.rules_copilot import (
    ColumnMeta,
    TableProfile,
    build_copilot_prompt,
    parse_ddl,
    profile_csv,
    validate_generated,
)
from dq_engine.ai.sampling import sample_stats, stratified_sample

__all__ = [
    "ColumnMeta",
    "RcaContext",
    "RcaReport",
    "TableProfile",
    "build_copilot_prompt",
    "build_report",
    "classify_by_signals",
    "parse_ddl",
    "profile_csv",
    "prompt_rca",
    "sample_stats",
    "stratified_sample",
    "validate_generated",
]
