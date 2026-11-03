"""混沌注入器单元测试：8 类故障的行为契约（构造小型 CSV 夹具验证）。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from chaos.injector import CANONICAL_FAULTS, DEFAULT_RATIOS, GIBBERISH, OFF_TOPIC, inject, inject_all


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _make_source(tmp_path) -> Path:
    src = tmp_path / "raw"
    src.mkdir()
    users = [
        {
            "user_id": f"U{i:03d}",
            "register_time": "2025-01-01 10:00:00",
            "age": str(20 + i % 40),
            "gender": "M" if i % 2 else "F",
            "city_tier": str(1 + i % 5),
            "channel_source": "抖音",
            "is_vip": "0",
        }
        for i in range(20)
    ]
    orders = [
        {
            "order_id": f"O{i:011d}",
            "user_id": f"U{i:03d}",
            "order_time": f"2025-01-{1 + i % 28:02d} 10:00:00",
            "channel": ["抖音", "淘宝", "京东"][i % 3],
            "category": "服饰",
            "amount": "123.45",
            "coupon_discount": "10.20",
            "status": "已支付",
        }
        for i in range(20)
    ]
    payments = [
        {
            "payment_id": f"P{i:011d}",
            "order_id": f"O{i:011d}",
            "user_id": f"U{i:03d}",
            "pay_time": f"2025-01-{1 + i % 28:02d} 10:05:00",
            "pay_channel": "支付宝",
            "pay_amount": "113.25",
            "currency": "CNY",
            "pay_status": "成功",
        }
        for i in range(15)
    ]
    qa = [
        {
            "qa_id": f"Q{i:011d}",
            "user_id": f"U{i:03d}",
            "session_time": "2025-01-02 10:00:00",
            "intent": "商品咨询",
            "question": "这件衣服有 XL 码吗？",
            "answer": "亲，这款有 XL 码，库存充足哦。",
            "model_version": "v1.0",
            "latency_ms": "500",
            "session_len": "1",
        }
        for i in range(20)
    ]
    _write_csv(src / "dim_user.csv", users)
    _write_csv(src / "fact_order.csv", orders)
    _write_csv(src / "fact_payment.csv", payments)
    _write_csv(src / "ai_qa_log.csv", qa)
    return src


def _rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_duplicate_pk(tmp_path):
    src = _make_source(tmp_path)
    inject("duplicate_pk", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "duplicate_pk" / "fact_order.csv")
    assert len(rows) == 20 + int(20 * DEFAULT_RATIOS["duplicate_pk"])  # 20 + 2
    assert len({r["order_id"] for r in rows}) == 20  # 主键重复


def test_orphan_fk(tmp_path):
    src = _make_source(tmp_path)
    inject("orphan_fk", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "orphan_fk" / "fact_payment.csv")
    orphans = [r for r in rows if r["order_id"].startswith("ORPHAN")]
    assert len(orphans) >= 1
    assert all(r["order_id"].startswith("ORPHAN") or r["order_id"].startswith("O") for r in rows)


def test_amount_truncate(tmp_path):
    src = _make_source(tmp_path)
    inject("amount_truncate", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "amount_truncate" / "fact_order.csv")
    truncated = [r for r in rows if "." not in r["amount"]]
    assert len(truncated) == int(20 * DEFAULT_RATIOS["amount_truncate"])  # 12 行被强转整型
    assert sum(float(r["amount"]) for r in rows) < 20 * 123.45
    # coupon 不动：截断只作用于 amount（应付金额 = amount - coupon 与支付对齐的检测语义）
    assert all("." in r["coupon_discount"] for r in rows)


def test_timestamp_mix(tmp_path):
    src = _make_source(tmp_path)
    inject("timestamp_mix", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "timestamp_mix" / "fact_order.csv")
    mixed = [r for r in rows if "T" in r["order_time"] and r["order_time"].endswith("Z")]
    assert len(mixed) == int(20 * DEFAULT_RATIOS["timestamp_mix"])  # 6 行


def test_schema_drift(tmp_path):
    src = _make_source(tmp_path)
    inject("schema_drift", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "schema_drift" / "ai_qa_log.csv")
    drifted = [r for r in rows if r["latency_ms"] == "N/A"]
    assert len(drifted) == int(20 * DEFAULT_RATIOS["schema_drift"])  # 6 行
    assert all(r["answer"] == "" for r in drifted)


def test_channel_drop(tmp_path):
    src = _make_source(tmp_path)
    before = sum(1 for r in _rows(src / "fact_order.csv") if r["channel"] == "抖音")
    inject("channel_drop", src, tmp_path / "chaos", seed=42, channel="抖音")
    rows = _rows(tmp_path / "chaos" / "channel_drop" / "fact_order.csv")
    after = sum(1 for r in rows if r["channel"] == "抖音")
    assert after == before - int(before * DEFAULT_RATIOS["channel_drop"])
    assert {r["channel"] for r in rows} <= {"抖音", "淘宝", "京东"}  # 格式全部合法


def test_psi_shift(tmp_path):
    src = _make_source(tmp_path)
    before_young = sum(1 for r in _rows(src / "dim_user.csv") if 18 <= int(r["age"]) <= 34)
    before_old = sum(1 for r in _rows(src / "dim_user.csv") if 55 <= int(r["age"]) <= 65)
    inject("psi_shift", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "psi_shift" / "dim_user.csv")
    after_old = sum(1 for r in rows if 55 <= int(r["age"]) <= 65)
    assert after_old == before_old + int(before_young * DEFAULT_RATIOS["psi_shift"])


def test_semantic_pollution(tmp_path):
    src = _make_source(tmp_path)
    inject("semantic_pollution", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "semantic_pollution" / "ai_qa_log.csv")
    polluted = [
        r
        for r in rows
        if r["answer"] in GIBBERISH + OFF_TOPIC or r["answer"] == r["question"] * 3
    ]
    assert len(polluted) == int(20 * DEFAULT_RATIOS["semantic_pollution"])  # 1 行


def test_inject_deterministic(tmp_path):
    src = _make_source(tmp_path)
    inject("duplicate_pk", src, tmp_path / "a", seed=7)
    inject("duplicate_pk", src, tmp_path / "b", seed=7)
    for name in ("fact_order.csv", "inject_report.json"):
        assert (tmp_path / "a" / "duplicate_pk" / name).read_bytes() == (
            tmp_path / "b" / "duplicate_pk" / name
        ).read_bytes()


def test_unknown_fault_raises(tmp_path):
    src = _make_source(tmp_path)
    with pytest.raises(ValueError, match="未知故障"):
        inject("earthquake", src, tmp_path / "chaos")


def test_data_skew(tmp_path):
    src = _make_source(tmp_path)
    inject("data_skew", src, tmp_path / "chaos", seed=42)
    rows = _rows(tmp_path / "chaos" / "data_skew" / "fact_order.csv")
    douyin = sum(1 for r in rows if r["channel"] == "抖音")
    assert douyin >= int(20 * DEFAULT_RATIOS["data_skew"])  # 16+ 行集中到热点渠道
    assert {r["channel"] for r in rows} <= {"抖音", "淘宝", "京东"}  # 数据仍合法


def test_inject_all(tmp_path):
    src = _make_source(tmp_path)
    reports = inject_all(src, tmp_path / "chaos", seed=42)
    assert set(reports) == set(CANONICAL_FAULTS)  # 8 类规范故障，data_skew 不参与矩阵
    for fault in CANONICAL_FAULTS:
        assert (tmp_path / "chaos" / fault / "inject_report.json").exists()
