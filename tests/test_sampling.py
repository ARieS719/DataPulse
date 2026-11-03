"""AI 语义分层抽样单元测试：分层比例 / 保底抽样 / 确定性 / 边界。"""

from __future__ import annotations

import pytest
from dq_engine.ai.sampling import sample_stats, stratified_sample


def _make_rows(n: int = 1000):
    rows = []
    strata = [("商品咨询", "v1.0"), ("物流查询", "v1.0"), ("闲聊", "v1.1")]
    weights = [600, 300, 100]
    for i in range(n):
        if i < weights[0]:
            intent, ver = strata[0]
        elif i < weights[0] + weights[1]:
            intent, ver = strata[1]
        else:
            intent, ver = strata[2]
        rows.append({"qa_id": f"Q{i:04d}", "intent": intent, "model_version": ver, "answer": f"answer-{i}"})
    return rows


def test_stratified_proportions():
    rows = _make_rows()
    picked = stratified_sample(rows, key_fields=["intent", "model_version"], rate=0.02, seed=42)
    assert len(picked) == 20  # 600*2% + 300*2% + 100*2%
    by = {}
    for r in picked:
        key = (r["intent"], r["model_version"])
        by[key] = by.get(key, 0) + 1
    assert by[("商品咨询", "v1.0")] == 12
    assert by[("物流查询", "v1.0")] == 6
    assert by[("闲聊", "v1.1")] == 2


def test_min_stratum_guarantee():
    rows = [{"qa_id": f"Q{i:02d}", "intent": "商品咨询", "model_version": "v1.0"} for i in range(100)]
    rows.append({"qa_id": "Q_lone", "intent": "售后处理", "model_version": "v2.0"})  # 仅 1 条的长尾层
    picked = stratified_sample(rows, key_fields=["intent", "model_version"], rate=0.01, seed=1)
    keys = {(r["intent"], r["model_version"]) for r in picked}
    assert ("售后处理", "v2.0") in keys  # 0.01*1≈0，但保底 1 条


def test_deterministic_same_seed():
    rows = _make_rows()
    a = stratified_sample(rows, key_fields=["intent", "model_version"], seed=7)
    b = stratified_sample(rows, key_fields=["intent", "model_version"], seed=7)
    assert [r["qa_id"] for r in a] == [r["qa_id"] for r in b]


def test_different_seed_still_valid_sample():
    rows = _make_rows()
    a = stratified_sample(rows, key_fields=["intent", "model_version"], seed=1)
    b = stratified_sample(rows, key_fields=["intent", "model_version"], seed=2)
    ids = {r["qa_id"] for r in rows}
    assert all(r["qa_id"] in ids for r in a)
    assert all(r["qa_id"] in ids for r in b)
    assert len(a) == len(b) == 20


def test_rate_zero_and_one():
    rows = _make_rows()
    assert stratified_sample(rows, key_fields=["intent", "model_version"], rate=0.0) == []
    assert len(stratified_sample(rows, key_fields=["intent", "model_version"], rate=1.0)) == 1000


def test_invalid_rate_rejected():
    with pytest.raises(ValueError, match="rate"):
        stratified_sample(_make_rows(), key_fields=["intent"], rate=1.5)


def test_sample_stats_coverage():
    rows = _make_rows()
    picked = stratified_sample(rows, key_fields=["intent", "model_version"], rate=0.02)
    stats = sample_stats(rows, picked, key_fields=["intent", "model_version"])
    assert stats["total"] == 1000 and stats["sampled"] == 20
    assert stats["strata_total"] == 3 and stats["strata_covered"] == 3
    assert abs(stats["rate"] - 0.02) < 1e-9
