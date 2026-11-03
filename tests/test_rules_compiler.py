"""规则引擎单元测试：schema 校验 + 编译器 + 判定器 + SQLite 端到端语义验证。

端到端测试用 SQLite 真实执行编译器生成的合并扫描 SQL——
在不依赖 Spark 的前提下证明"一次扫描、全量校验"语义正确
（Spark 执行器在 M3 接入，生成的 SQL 面向 Spark/SQLite 双方言兼容）。
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml
from dq_engine.rules.engine import compile_ruleset, evaluate_result, load_ruleset, run_ruleset
from dq_engine.rules.schema import Ruleset
from pydantic import ValidationError

# ---------------------------------------------------------------- helpers


def _ruleset(text: str) -> Ruleset:
    return Ruleset.model_validate(yaml.safe_load(text))


def _exec(conn: sqlite3.Connection, sql: str) -> dict:
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone(), strict=False))


def _conn_with_data() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE dim_user (user_id TEXT, age INT, gender TEXT, city_tier INT)")
    c.executemany(
        "INSERT INTO dim_user VALUES (?, ?, ?, ?)",
        [("U001", 25, "M", 1), ("U002", 30, "F", 2), ("U003", None, "M", 3), ("U004", 22, "X", 4)],
    )
    c.execute("CREATE TABLE fact_order (order_id TEXT, user_id TEXT, amount REAL, status TEXT)")
    c.executemany(
        "INSERT INTO fact_order VALUES (?, ?, ?, ?)",
        [
            ("O001", "U001", 100.0, "已支付"),
            ("O002", "U002", 200.0, "已支付"),
            ("O003", "U003", 50.0, "已支付"),
            ("O004", "U999", 30.0, "已支付"),  # U999 为孤儿外键
            ("O005", "U001", 150.0, "已退款"),
        ],
    )
    c.execute("CREATE TABLE fact_payment (payment_id TEXT, order_id TEXT, pay_amount REAL)")
    c.executemany(
        "INSERT INTO fact_payment VALUES (?, ?, ?)",
        [("P001", "O001", 90.0), ("P002", "O002", 200.0), ("P003", "O003", 50.0), ("P004", "O004", 30.0)],
    )
    return c


# ---------------------------------------------------------------- schema 校验


def test_load_valid_yaml_from_file(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        """
version: 1
table: fact_order
rules:
  - name: order_id_unique
    type: unique
    level: P0
    column: order_id
""",
        encoding="utf-8",
    )
    rs = load_ruleset(p)
    assert rs.table == "fact_order"
    assert len(rs.rules) == 1
    assert rs.rules[0].type == "unique"


def test_reject_unknown_rule_type():
    with pytest.raises(ValidationError):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: magic
    level: P0
"""
        )


def test_reject_sql_injection_identifier():
    with pytest.raises(ValidationError, match="非法标识符"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: unique
    level: P0
    column: "x; DROP TABLE users"
"""
        )


def test_reject_missing_level():
    with pytest.raises(ValidationError):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: unique
    column: c
"""
        )


def test_reject_duplicate_rule_names():
    with pytest.raises(ValidationError, match="唯一"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: unique
    level: P0
    column: a
  - name: r1
    type: unique
    level: P0
    column: b
"""
        )


def test_reject_range_without_bounds():
    with pytest.raises(ValidationError, match="min 或 max"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: range
    level: P1
    column: c
"""
        )


def test_reject_empty_values():
    with pytest.raises(ValidationError, match="至少"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: accepted_values
    level: P1
    column: c
    values: []
"""
        )


def test_reject_bad_timestamp():
    with pytest.raises(ValidationError, match="时间格式"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: timeliness
    level: P1
    column: ts
    expect_not_before: "2025/01/01"
"""
        )


def test_reject_empty_ruleset():
    with pytest.raises(ValidationError):
        _ruleset("version: 1\ntable: t\nrules: []\n")


def test_accept_all_eight_rule_types():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: r1
    type: not_null
    level: P1
    column: amount
  - name: r2
    type: unique
    level: P0
    column: order_id
  - name: r3
    type: accepted_values
    level: P0
    column: status
    values: [已支付, 已退款]
  - name: r4
    type: range
    level: P1
    column: amount
    min: 0
  - name: r5
    type: row_count
    level: P1
    min_rows: 1
  - name: r6
    type: timeliness
    level: P1
    column: order_time
    expect_not_before: "2025-01-01 00:00:00"
  - name: r7
    type: fk_integrity
    level: P0
    fk_column: user_id
    ref_table: dim_user
    ref_column: user_id
  - name: r8
    type: sum_align
    level: P0
    agg_column: amount
    ref_table: fact_payment
    ref_agg_column: pay_amount
"""
    )
    assert len(rs.rules) == 8
    assert rs.rules[4].type == "row_count"


# ---------------------------------------------------------------- 编译器


def test_compile_merges_six_rules_into_one_scan():
    rs = _ruleset(
        """
version: 1
table: dim_user
rules:
  - name: r1
    type: not_null
    level: P1
    column: age
  - name: r2
    type: unique
    level: P0
    column: user_id
  - name: r3
    type: accepted_values
    level: P1
    column: gender
    values: [M, F]
  - name: r4
    type: range
    level: P1
    column: city_tier
    min: 1
    max: 5
  - name: r5
    type: row_count
    level: P1
    min_rows: 1
  - name: r6
    type: timeliness
    level: P1
    column: reg_ts
    expect_not_before: "2025-01-01"
"""
    )
    cq = compile_ruleset(rs)
    assert cq.sql.count("SELECT") == 1
    assert cq.sql.count("FROM ") == 1
    assert "__total_rows" in cq.sql and "__m5" in cq.sql
    assert len(cq.metrics) == 6


def test_compile_literal_escaping():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: r1
    type: accepted_values
    level: P1
    column: c
    values: ["a'b", 3]
"""
    )
    cq = compile_ruleset(rs)
    assert "a''b" in cq.sql and ", 3" in cq.sql


def test_compile_partition_filter():
    rs = _ruleset(
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
    cq = compile_ruleset(rs, partition_filter="ds = '2025-01-02'")
    assert cq.sql.rstrip().endswith("WHERE ds = '2025-01-02'")


def test_compile_fk_subquery_shape():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: r1
    type: fk_integrity
    level: P0
    fk_column: user_id
    ref_table: dim_user
    ref_column: user_id
"""
    )
    cq = compile_ruleset(rs, table_mapper=lambda t: f"cat.dwd.{t}")
    assert "LEFT JOIN (SELECT DISTINCT" in cq.sql
    assert "NULLIF(COUNT(*), 0)" in cq.sql
    assert "cat.dwd.dim_user" in cq.sql and "FROM cat.dwd.fact_order" in cq.sql


def test_compile_sum_align_subquery_shape():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: r1
    type: sum_align
    level: P0
    agg_column: amount
    ref_table: fact_payment
    ref_agg_column: pay_amount
"""
    )
    cq = compile_ruleset(rs)
    assert "CROSS JOIN" in cq.sql and "ABS(a.__s - b.__s)" in cq.sql


def test_compile_sum_align_agg_expr():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: r1
    type: sum_align
    level: P0
    agg_expr: "amount - coupon_discount"
    agg_filter: "status = '已支付'"
    ref_table: fact_payment
    ref_agg_column: pay_amount
"""
    )
    cq = compile_ruleset(rs)
    assert "SUM(amount - coupon_discount)" in cq.sql
    assert "WHERE status = '已支付'" in cq.sql


def test_sum_align_agg_expr_rejects_injection():
    with pytest.raises(ValidationError, match="agg_expr"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: sum_align
    level: P0
    agg_expr: "amount; DROP TABLE t"
    ref_table: p
    ref_agg_column: x
"""
        )


def test_sum_align_requires_exactly_one_agg():
    with pytest.raises(ValidationError, match="agg_column 或 agg_expr"):
        _ruleset(
            """
version: 1
table: t
rules:
  - name: r1
    type: sum_align
    level: P0
    ref_table: p
    ref_agg_column: x
"""
        )


# ---------------------------------------------------------------- 端到端（SQLite 真实执行）


def test_e2e_dirty_table_triggers_warn_not_block():
    rs = _ruleset(
        """
version: 1
table: dim_user
rules:
  - name: user_id_unique
    type: unique
    level: P0
    column: user_id
  - name: age_fill
    type: not_null
    level: P1
    column: age
    min_fill_ratio: 0.75
  - name: gender_enum
    type: accepted_values
    level: P1
    column: gender
    values: [M, F]
    min_match_ratio: 0.8
  - name: city_range
    type: range
    level: P1
    column: city_tier
    min: 1
    max: 5
"""
    )
    conn = _conn_with_data()
    cq = compile_ruleset(rs)
    row = _exec(conn, cq.sql)
    outcome = evaluate_result(cq, row)

    assert outcome.total_rows == 4
    assert outcome.verdict == "WARN"          # 仅 P1 失败 → 告警放行
    assert not outcome.p0_failed
    assert [o.rule_name for o in outcome.p1_failed] == ["gender_enum"]
    by_name = {o.rule_name: o for o in outcome.outcomes}
    assert by_name["user_id_unique"].passed
    assert by_name["age_fill"].passed          # 3/4 = 0.75 恰好达标
    assert abs(by_name["age_fill"].metric - 0.75) < 1e-9


def test_e2e_duplicate_pk_triggers_block():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: pk_unique
    type: unique
    level: P0
    column: id
"""
    )
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id TEXT)")
    c.executemany("INSERT INTO t VALUES (?)", [("A",), ("B",), ("A",)])
    outcome = run_ruleset(rs, _exec(c, compile_ruleset(rs).sql))
    assert outcome.verdict == "BLOCK"
    assert outcome.p0_failed[0].metric == 1.0  # 1 行重复


def test_e2e_fk_orphan_ratio():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: user_fk
    type: fk_integrity
    level: P0
    fk_column: user_id
    ref_table: dim_user
    ref_column: user_id
    max_orphan_ratio: 0.1
"""
    )
    conn = _conn_with_data()
    outcome = run_ruleset(rs, _exec(conn, compile_ruleset(rs).sql))
    assert outcome.verdict == "BLOCK"          # 1/5 = 0.2 > 0.1
    assert abs(outcome.outcomes[0].metric - 0.2) < 1e-9


def test_e2e_sum_align_mismatch_and_match():
    rs = _ruleset(
        """
version: 1
table: fact_order
rules:
  - name: amount_align
    type: sum_align
    level: P0
    agg_column: amount
    ref_table: fact_payment
    ref_agg_column: pay_amount
    max_diff_ratio: 0.01
"""
    )
    conn = _conn_with_data()  # 订单合计 530 vs 支付合计 370 → 160/900 ≈ 0.1778
    outcome = run_ruleset(rs, _exec(conn, compile_ruleset(rs).sql))
    assert outcome.verdict == "BLOCK"
    assert abs(outcome.outcomes[0].metric - 160 / 900) < 1e-6

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fact_order (amount REAL)")
    c.execute("INSERT INTO fact_order VALUES (100.0)")
    c.execute("CREATE TABLE fact_payment (pay_amount REAL)")
    c.execute("INSERT INTO fact_payment VALUES (100.0)")
    outcome2 = run_ruleset(rs, _exec(c, compile_ruleset(rs).sql))
    assert outcome2.verdict == "PASS"
    assert outcome2.outcomes[0].metric == 0.0


def test_e2e_timeliness():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: fresh_enough
    type: timeliness
    level: P1
    column: ts
    expect_not_before: "2025-01-01 00:00:00"
"""
    )
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (ts TEXT)")
    c.execute("INSERT INTO t VALUES ('2025-01-02 00:00:00')")
    outcome = run_ruleset(rs, _exec(c, compile_ruleset(rs).sql))
    assert outcome.verdict == "PASS"

    rs2 = _ruleset(
        """
version: 1
table: t
rules:
  - name: fresh_enough
    type: timeliness
    level: P1
    column: ts
    expect_not_before: "2025-02-01 00:00:00"
"""
    )
    outcome2 = run_ruleset(rs2, _exec(c, compile_ruleset(rs2).sql))
    assert outcome2.verdict == "WARN"


def test_e2e_row_count_bounds():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: cnt
    type: row_count
    level: P1
    min_rows: 10
    max_rows: 100
"""
    )
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id INT)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(5)])
    outcome = run_ruleset(rs, _exec(c, compile_ruleset(rs).sql))
    assert outcome.verdict == "WARN"
    assert "当前 5 行" in outcome.outcomes[0].detail


def test_e2e_empty_table_policy():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: col_fill
    type: not_null
    level: P1
    column: c
  - name: must_have_rows
    type: row_count
    level: P0
    min_rows: 1
"""
    )
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (c INT)")
    outcome = run_ruleset(rs, _exec(c, compile_ruleset(rs).sql))
    by_name = {o.rule_name: o for o in outcome.outcomes}
    assert by_name["col_fill"].passed and "空表跳过" in by_name["col_fill"].detail
    assert not by_name["must_have_rows"].passed
    assert outcome.verdict == "BLOCK"


def test_verdict_priority_block_over_warn():
    rs = _ruleset(
        """
version: 1
table: t
rules:
  - name: p1_rule
    type: unique
    level: P1
    column: a
  - name: p0_rule
    type: unique
    level: P0
    column: b
"""
    )
    row = {"__total_rows": 10, "__m0": 2, "__m1": 1}
    outcome = evaluate_result(compile_ruleset(rs), row)
    assert outcome.verdict == "BLOCK"
    assert len(outcome.p0_failed) == 1 and len(outcome.p1_failed) == 1
    assert "p0_failed" in outcome.to_dict() and outcome.to_dict()["total_rows"] == 10
