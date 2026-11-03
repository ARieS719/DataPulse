"""Golden Dataset 评测框架单元测试。"""

from __future__ import annotations

from pathlib import Path

from eval.golden_rules import evaluate_against_golden, evaluate_rule_generation, load_golden_cases
from eval.rca_eval import evaluate_rca, load_rca_golden
from eval.report import render_eval_report

REPO = Path(__file__).resolve().parents[1]

EXPECTED_YAML = """
version: 1
table: t
rules:
  - name: r1
    type: unique
    level: P0
    column: id
  - name: r2
    type: not_null
    level: P1
    column: c
"""


def test_perfect_match():
    m = evaluate_rule_generation([{"name": "t1", "ddl": "", "expected_yaml": EXPECTED_YAML, "generated_yaml": EXPECTED_YAML}])
    assert m["syntax_valid_rate"] == 1.0 and m["avg_coverage"] == 1.0


def test_missing_rules_reported():
    gen = """
version: 1
table: t
rules:
  - name: r1
    type: unique
    level: P0
    column: id
"""
    m = evaluate_rule_generation([{"name": "t1", "ddl": "", "expected_yaml": EXPECTED_YAML, "generated_yaml": gen}])
    assert m["avg_coverage"] == 0.5
    assert "('not_null', 'c')" in m["per_case"][0]["missing"]


def test_invalid_generated_zero_coverage():
    bad = EXPECTED_YAML.replace("type: unique", "type: magic")
    m = evaluate_rule_generation([{"name": "t1", "ddl": "", "expected_yaml": EXPECTED_YAML, "generated_yaml": bad}])
    assert m["syntax_valid_rate"] == 0.0
    assert m["per_case"][0]["syntax_valid"] is False and m["per_case"][0]["coverage"] == 0.0


def test_syntax_rate_across_cases():
    cases = [
        {"name": "a", "ddl": "", "expected_yaml": EXPECTED_YAML, "generated_yaml": EXPECTED_YAML},
        {"name": "b", "ddl": "", "expected_yaml": EXPECTED_YAML, "generated_yaml": "::: bad"},
    ]
    m = evaluate_rule_generation(cases)
    assert m["syntax_valid_rate"] == 0.5


def test_load_golden_cases():
    cases = load_golden_cases(REPO / "eval" / "golden" / "cases")
    names = {c["name"] for c in cases}
    assert {"01_dim_user", "02_fact_order", "03_fact_payment"} <= names
    assert all("CREATE TABLE" in c["ddl"] for c in cases)  # DDL 文件首行是注释，故用包含判断


def test_evaluate_against_golden_missing_generation():
    m = evaluate_against_golden(REPO / "eval" / "golden" / "cases", {})
    assert m["syntax_valid_rate"] == 0.0  # 未提供生成结果 → 全部语法非法
    assert len(m["per_case"]) >= 3


def test_rca_eval_perfect_with_baseline():
    golden = load_rca_golden(REPO / "eval" / "golden" / "rca_golden.json")
    m = evaluate_rca(golden)
    assert m["scenarios"] == 8 and m["accuracy"] == 1.0  # 基线分类器与 Ground Truth 一致


def test_rca_eval_custom_classifier():
    golden = load_rca_golden(REPO / "eval" / "golden" / "rca_golden.json")

    def always_wrong(signals):
        return "未分类异常（需人工介入）"

    m = evaluate_rca(golden, classify=always_wrong)
    assert m["accuracy"] == 0.0 and len(m["confusion"]) == 8


def test_render_report_contains_sections():
    md = render_eval_report(
        {
            "rule_generation": {"syntax_valid_rate": 0.9, "avg_coverage": 0.8, "per_case": []},
            "rca": {"scenarios": 8, "accuracy": 0.875, "per_case": []},
            "chaos": {"p0_block_rate": 1.0, "recall": 0.965, "false_positive_rate": 0.025},
        }
    )
    assert "语法合法率" in md and "90.0%" in md
    assert "故障分类准确率" in md and "87.5%" in md
    assert "P0 拦截率" in md and "100.0%" in md
