"""M4：AI-RCA 根因诊断 —— 证据组装 + 规则化基线分类 + LLM 提示词构建。

架构（面试要点）：
  * 规则化基线（classify_by_signals）：把"熔断信号 → 故障类别"的专家经验固化
    为确定性映射，它既是无 API 时的降级方案，也是 Golden Dataset 评测的
    对照基线——AI 的增益必须相对这个基线可量化（M6 评测报告）。
  * LLM 增强（prompt_rca + build_report）：注入异常摘要、血缘爆炸半径、
    Git 变更记录与历史案例（RAG），要求结构化输出诊断书。

LLM 调用由执行器注入（可替换 DeepSeek/Qwen 任意在线 API），本模块零 I/O。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# 熔断信号 → 故障类别（覆盖立项规格书 §四.2 的混沌故障分类）
SIGNAL_CLASSIFICATION = {
    "unique_violation": "CDC 消息重复消费 / 主键生成冲突",
    "fk_orphan": "多源同步延迟（外键孤儿）",
    "sum_align_mismatch": "金额精度截断 / 漏同步",
    "timestamp_format": "跨时区时间戳错位 / 精度截断",
    "schema_drift": "上游 Schema 漂移",
    "volume_dip": "上游渠道断流（隐性暴跌）",
    "psi_shift": "客群特征分布偏移",
    "semantic_pollution": "非结构化语料污染（模型退化）",
    "unknown": "未分类异常（需人工介入）",
}


@dataclass
class RcaContext:
    table: str
    signals: list[str]  # 归一化信号键，见 SIGNAL_CLASSIFICATION
    failed_rules: list[dict]
    lineage_path: list[str]
    git_changes: list[str]
    history_cases: list[str]


@dataclass
class RcaReport:
    table: str
    classification: str
    root_cause_hypothesis: str
    fix_suggestions: list[str]
    evidence: list[str]
    generated_by: str  # "rule-baseline" / "llm"

    def to_markdown(self) -> str:
        lines = [
            f"# AI-RCA 诊断书：{self.table}",
            "",
            f"- 故障分类：**{self.classification}**",
            f"- 根因假设：{self.root_cause_hypothesis}",
            f"- 生成方式：{self.generated_by}",
            "",
            "## 修复建议",
            *[f"- {s}" for s in self.fix_suggestions],
            "",
            "## 证据",
            *[f"- {e}" for e in self.evidence],
        ]
        return "\n".join(lines)


FIX_SUGGESTIONS = {
    "CDC 消息重复消费 / 主键生成冲突": [
        "核对上游 CDC offset 与消费位点，排查重复投递窗口",
        "下游写入前增加幂等键（如 order_id + event_time）去重",
        "检查主键生成规则是否有并发冲突",
    ],
    "多源同步延迟（外键孤儿）": [
        "核对各源任务的同步水位与调度依赖，孤儿侧等待主表就绪",
        "为跨源任务补 Sensor 依赖（Airflow ExternalTaskSensor）",
        "孤儿流水进入暂存区，主表到达后自动重放对账",
    ],
    "金额精度截断 / 漏同步": [
        "排查 ETL 中 float→int 的隐式强转，统一 DECIMAL 精度口径",
        "核对增量同步窗口是否漏抓边界数据",
    ],
    "跨时区时间戳错位 / 精度截断": [
        "统一存储 UTC、展示层转本地时区，禁止混写",
        "禁止毫秒截断为秒：时间戳统一 TIMESTAMP(3) 以上精度",
    ],
    "上游 Schema 漂移": [
        "核对上游发布变更记录与 schema 版本",
        "解析器改为容错模式（缺键给默认值 + 告警）",
    ],
    "上游渠道断流（隐性暴跌）": [
        "核对上游接口限流与数据延迟监控",
        "恢复断流渠道的增量数据并重放",
    ],
    "客群特征分布偏移": [
        "核对当日客群来源渠道与活动投放变化",
        "确认特征口径未变更后评估是否需要重建水位线",
    ],
    "非结构化语料污染（模型退化）": [
        "核查模型版本发布记录与提示词变更",
        "对污染批次回滚模型并重新质检",
    ],
    "未分类异常（需人工介入）": ["人工查看失败规则详情与血缘路径，升级处理"],
}


def classify_by_signals(signals: list[str]) -> str:
    """规则化基线：按优先级最高的已知信号分类（顺序即优先级）。"""
    for key in (
        "unique_violation",
        "fk_orphan",
        "sum_align_mismatch",
        "timestamp_format",
        "schema_drift",
        "volume_dip",
        "psi_shift",
        "semantic_pollution",
    ):
        if key in signals:
            return SIGNAL_CLASSIFICATION[key]
    return SIGNAL_CLASSIFICATION["unknown"]


def prompt_rca(ctx: RcaContext) -> str:
    """构建 LLM 诊断提示词（结构化输出要求 + 全部证据注入）。"""
    return "\n".join(
        [
            "你是资深数据质量工程师。请基于以下证据输出结构化根因诊断（JSON）：",
            '字段：{"classification": str, "root_cause": str, "fix_suggestions": [str], "confidence": float}',
            f"熔断表：{ctx.table}",
            f"失败规则：{ctx.failed_rules}",
            f"血缘下游影响：{ctx.lineage_path}",
            f"最近 ETL 变更：{ctx.git_changes}",
            f"历史相似案例：{ctx.history_cases}",
            "要求：classification 从已知故障类别中选择；fix_suggestions 给出可执行步骤。",
        ]
    )


def build_report(
    ctx: RcaContext,
    llm_call: Callable[[str], dict | None] | None = None,
) -> RcaReport:
    """生成诊断书：优先 LLM（结构化 JSON），失败/缺省时回退规则化基线。"""
    classification = classify_by_signals(ctx.signals)
    if llm_call is not None:
        try:
            parsed = llm_call(prompt_rca(ctx))
            if parsed and isinstance(parsed, dict) and parsed.get("classification"):
                return RcaReport(
                    table=ctx.table,
                    classification=parsed["classification"],
                    root_cause_hypothesis=parsed.get("root_cause", ""),
                    fix_suggestions=parsed.get("fix_suggestions", []) or FIX_SUGGESTIONS.get(classification, []),
                    evidence=[f"血缘影响：{t}" for t in ctx.lineage_path] + ctx.git_changes,
                    generated_by="llm",
                )
        except Exception:
            pass  # LLM 失败回退基线，保证诊断链路可用（熔断场景的韧性设计）

    return RcaReport(
        table=ctx.table,
        classification=classification,
        root_cause_hypothesis=classification,
        fix_suggestions=FIX_SUGGESTIONS.get(classification, []),
        evidence=[f"血缘影响：{t}" for t in ctx.lineage_path] + ctx.git_changes,
        generated_by="rule-baseline",
    )
