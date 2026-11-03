"""AI 规则 Copilot 单元测试：DDL 解析 / CSV 探查 / 提示词组装 / 生成校验。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from dq_engine.ai.rules_copilot import build_copilot_prompt, parse_ddl, profile_csv, validate_generated
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[1]

ORDER_DDL = (REPO / "eval" / "golden" / "cases" / "02_fact_order.ddl").read_text(encoding="utf-8")


def test_parse_ddl_columns():
    profile = parse_ddl(ORDER_DDL)
    assert profile.table == "fact_order"
    assert [c.name for c in profile.columns] == [
        "order_id", "user_id", "order_time", "channel", "category", "amount", "coupon_discount", "status",
    ]
    by_name = {c.name: c for c in profile.columns}
    assert by_name["order_id"].type == "STRING"
    assert by_name["order_id"].comment == "订单主键"
    assert by_name["amount"].type == "DECIMAL(12,2)"
    assert by_name["status"].comment == "订单状态"


def test_parse_ddl_invalid_raises():
    with pytest.raises(ValueError, match="CREATE TABLE"):
        parse_ddl("SELECT 1")


def test_profile_csv_stats(tmp_path):
    p = tmp_path / "t.csv"
    rows = [
        {"id": "1", "age": "20", "status": "已支付"},
        {"id": "2", "age": "25", "status": "已支付"},
        {"id": "3", "age": "", "status": "已退款"},
        {"id": "4", "age": "30", "status": ""},
    ]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "age", "status"])
        w.writeheader()
        w.writerows(rows)

    profile = profile_csv(p, sample_size=2)
    assert profile.table == "t"
    assert len(profile.sample_rows) == 2
    assert profile.column_stats["age"]["null_rate"] == 0.25
    assert profile.column_stats["age"]["numeric_min"] == 20.0 and profile.column_stats["age"]["numeric_max"] == 30.0
    assert profile.column_stats["status"]["top_values"][0] == {"value": "已支付", "count": 2}
    assert profile.column_stats["status"]["null_rate"] == 0.25


def test_prompt_contains_all_context():
    profile = parse_ddl(ORDER_DDL)
    prompt = build_copilot_prompt(profile, rag_docs=["订单状态仅允许：已支付/待支付/已取消/已退款"])
    assert "fact_order" in prompt
    assert "order_id" in prompt and "DECIMAL(12,2)" in prompt
    assert "仅输出 YAML" in prompt
    assert "已支付/待支付/已取消/已退款" in prompt
    assert "P0" in prompt and "unique" in prompt


def test_prompt_without_rag_docs():
    profile = parse_ddl(ORDER_DDL)
    prompt = build_copilot_prompt(profile)
    assert "业务口径约束" not in prompt


def test_validate_generated_ok():
    rs = validate_generated(
        """
version: 1
table: t
rules:
  - name: r1
    type: unique
    level: P0
    column: id
"""
    )
    assert rs.table == "t" and len(rs.rules) == 1


def test_validate_generated_rejects_bad():
    with pytest.raises(ValidationError):
        validate_generated(
            """
version: 1
table: t
rules:
  - name: r1
    type: magic
    level: P0
"""
        )
