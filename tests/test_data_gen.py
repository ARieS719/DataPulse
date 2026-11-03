"""数据生成器单元测试（零依赖，small 规模，秒级跑完）。

覆盖生成器的核心质量属性：
  * 确定性（同 seed 两次生成完全一致）
  * 主键唯一性 / 外键完整性 / 跨表金额一致性
  * 枚举值域 / 数值域 / 时间域 / 空值率边界
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta

import pytest

from foundation.data_gen import generate as gen

BASE = datetime.strptime("2025-01-01", "%Y-%m-%d")
SPAN_DAYS = 90


def _gen_tables(tmp_path, seed: int = 42) -> dict:
    cfg = gen.SCALES["small"]
    counts = {
        "dim_user": gen.gen_users(gen._rng(seed, "users"), cfg["users"], BASE, tmp_path),
        "fact_order": gen.gen_orders(gen._rng(seed, "orders"), cfg["users"], cfg["orders"], BASE, SPAN_DAYS, tmp_path),
    }
    counts["fact_payment"] = gen.gen_payments(gen._rng(seed, "payments"), tmp_path / "fact_order.csv", tmp_path)
    counts["ai_qa_log"] = gen.gen_qa(gen._rng(seed, "qa"), cfg["users"], cfg["qa"], BASE, SPAN_DAYS, tmp_path)
    return counts


def _rows(path):
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    d = tmp_path_factory.mktemp("gen")
    counts = _gen_tables(d)
    return {
        "counts": counts,
        "users": _rows(d / "dim_user.csv"),
        "orders": _rows(d / "fact_order.csv"),
        "payments": _rows(d / "fact_payment.csv"),
        "qa": _rows(d / "ai_qa_log.csv"),
    }


def test_deterministic_two_runs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    _gen_tables(a, seed=7)
    _gen_tables(b, seed=7)
    for name in ("dim_user", "fact_order", "fact_payment", "ai_qa_log"):
        assert gen.sha256_file(a / f"{name}.csv") == gen.sha256_file(b / f"{name}.csv"), name


def test_row_counts(tables):
    assert tables["counts"]["dim_user"] == 1000
    assert tables["counts"]["fact_order"] == 10000
    assert tables["counts"]["ai_qa_log"] == 5000
    paid = sum(1 for r in tables["orders"] if r["status"] == "已支付")
    assert tables["counts"]["fact_payment"] == paid
    assert len(tables["payments"]) == paid


def test_order_pk_unique(tables):
    ids = [r["order_id"] for r in tables["orders"]]
    assert len(ids) == len(set(ids))


def test_payment_qa_pk_unique(tables):
    pids = [r["payment_id"] for r in tables["payments"]]
    qids = [r["qa_id"] for r in tables["qa"]]
    assert len(pids) == len(set(pids))
    assert len(qids) == len(set(qids))


def test_payment_fk_and_amount_consistency(tables):
    orders = {r["order_id"]: r for r in tables["orders"]}
    for p in tables["payments"]:
        o = orders[p["order_id"]]
        assert p["user_id"] == o["user_id"]
        assert abs(float(p["pay_amount"]) - (float(o["amount"]) - float(o["coupon_discount"]))) < 0.001


def test_order_user_fk(tables):
    user_ids = {r["user_id"] for r in tables["users"]}
    for o in tables["orders"]:
        assert o["user_id"] in user_ids


def test_enum_domains(tables):
    assert {r["status"] for r in tables["orders"]} <= set(gen.ORDER_STATUS)
    assert {r["channel"] for r in tables["orders"]} <= set(gen.CHANNELS)
    assert {r["pay_channel"] for r in tables["payments"]} <= set(gen.PAY_CHANNELS)
    assert {r["pay_status"] for r in tables["payments"]} <= set(gen.PAY_STATUS)
    assert {r["intent"] for r in tables["qa"]} <= set(gen.QA_INTENTS)


def test_age_domain_and_null_rate(tables):
    ages = [r["age"] for r in tables["users"]]
    nulls = sum(1 for a in ages if a == "")
    filled = [int(a) for a in ages if a != ""]
    assert all(18 <= a <= 65 for a in filled)
    assert nulls / len(ages) < 0.05  # 期望约 2%


def test_amount_positive(tables):
    assert all(float(r["amount"]) > 0 for r in tables["orders"])
    assert all(float(r["pay_amount"]) >= 0 for r in tables["payments"])


def test_timestamp_domains(tables):
    lo, hi = BASE, BASE.replace(year=2025, month=4, day=1)
    for r in tables["orders"]:
        ts = datetime.strptime(r["order_time"], "%Y-%m-%d %H:%M:%S")
        assert lo <= ts < hi
    for r in tables["payments"]:
        ts = datetime.strptime(r["pay_time"], "%Y-%m-%d %H:%M:%S")
        assert lo <= ts < hi + timedelta(days=1)


def test_pay_time_after_order_time(tables):
    orders = {r["order_id"]: datetime.strptime(r["order_time"], "%Y-%m-%d %H:%M:%S") for r in tables["orders"]}
    for p in tables["payments"]:
        pay_ts = datetime.strptime(p["pay_time"], "%Y-%m-%d %H:%M:%S")
        assert pay_ts >= orders[p["order_id"]]


def test_qa_latency_and_session(tables):
    for r in tables["qa"]:
        assert 180 <= int(r["latency_ms"]) <= 2500
        assert 1 <= int(r["session_len"]) <= 8
