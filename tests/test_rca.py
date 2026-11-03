"""AI-RCA 单元测试：规则化基线分类 / 证据注入提示词 / 报告生成与 LLM 回退。"""

from __future__ import annotations

from dq_engine.ai.rca import (
    FIX_SUGGESTIONS,
    RcaContext,
    build_report,
    classify_by_signals,
    prompt_rca,
)


def _ctx(signals=None):
    return RcaContext(
        table="dwd.fact_order",
        signals=signals or [],
        failed_rules=[{"name": "order_id_unique", "type": "unique", "level": "P0"}],
        lineage_path=["dws_trade_daily", "ads_gmv_daily"],
        git_changes=["etl_dwd.py: 修改金额字段强转逻辑（3 小时前）"],
        history_cases=["案例#12：CDC 重复消费导致主键冲突，恢复方案为消费位点回拨"],
    )


def test_classify_unique_violation():
    assert classify_by_signals(["unique_violation"]) == "CDC 消息重复消费 / 主键生成冲突"


def test_classify_fk_orphan():
    assert classify_by_signals(["fk_orphan"]) == "多源同步延迟（外键孤儿）"


def test_classify_sum_align():
    assert classify_by_signals(["sum_align_mismatch"]) == "金额精度截断 / 漏同步"


def test_classify_volume_dip():
    assert classify_by_signals(["volume_dip"]) == "上游渠道断流（隐性暴跌）"


def test_classify_psi_and_semantic():
    assert classify_by_signals(["psi_shift"]) == "客群特征分布偏移"
    assert classify_by_signals(["semantic_pollution"]) == "非结构化语料污染（模型退化）"


def test_classify_unknown_and_priority():
    assert classify_by_signals([]) == "未分类异常（需人工介入）"
    # 优先级：unique 在 semantic 之前
    assert classify_by_signals(["semantic_pollution", "unique_violation"]) == "CDC 消息重复消费 / 主键生成冲突"


def test_prompt_contains_all_evidence():
    ctx = _ctx(["unique_violation"])
    p = prompt_rca(ctx)
    assert "dwd.fact_order" in p
    assert "dws_trade_daily" in p and "ads_gmv_daily" in p
    assert "etl_dwd.py" in p
    assert "案例#12" in p
    assert "classification" in p and "confidence" in p


def test_fallback_report_structure():
    report = build_report(_ctx(["unique_violation"]))
    assert report.generated_by == "rule-baseline"
    assert report.classification == "CDC 消息重复消费 / 主键生成冲突"
    assert report.fix_suggestions == FIX_SUGGESTIONS[report.classification]
    assert any("dws_trade_daily" in e for e in report.evidence)
    md = report.to_markdown()
    assert "诊断书" in md and "修复建议" in md and "证据" in md


def test_llm_report_used_when_available():
    fake_llm = lambda prompt: {  # noqa: E731
        "classification": "上游渠道断流（隐性暴跌）",
        "root_cause": "上游接口限流",
        "fix_suggestions": [],
        "confidence": 0.92,
    }
    report = build_report(_ctx(["volume_dip"]), llm_call=fake_llm)
    assert report.generated_by == "llm"
    assert report.classification == "上游渠道断流（隐性暴跌）"
    assert report.fix_suggestions  # 空建议回退专家模板


def test_llm_failure_falls_back_to_baseline():
    def broken_llm(_prompt):
        raise RuntimeError("API 超时")

    report = build_report(_ctx(["fk_orphan"]), llm_call=broken_llm)
    assert report.generated_by == "rule-baseline"
    assert report.classification == "多源同步延迟（外键孤儿）"
