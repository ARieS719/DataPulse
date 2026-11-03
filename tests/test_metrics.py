"""S3 指标口径层单元测试：字典注册 / YAML 往返 / SQLGlot 口径 diff / 指标-血缘联动。"""

from __future__ import annotations

from pathlib import Path

import pytest
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge
from dq_engine.metrics import MetricDef, MetricRegistry, diff_calibers, metric_downstream_tables

REPO = Path(__file__).resolve().parents[1]


def _registry() -> MetricRegistry:
    r = MetricRegistry()
    r.register(MetricDef(name="gmv", owner="a", caliber_sql="SELECT SUM(pay_amount) FROM fact_payment"))
    return r


def test_register_and_get():
    r = _registry()
    assert r.get("gmv").owner == "a"
    assert r.get("nope") is None
    assert [m.name for m in r.list()] == ["gmv"]


def test_duplicate_register_raises():
    r = _registry()
    with pytest.raises(ValueError, match="重复"):
        r.register(MetricDef(name="gmv", owner="b", caliber_sql="SELECT 1"))


def test_load_yaml():
    r = MetricRegistry()
    n = r.load_yaml(REPO / "core" / "dq_engine" / "metrics" / "examples" / "metrics.yaml")
    assert n == 3
    assert r.get("gmv") is not None and r.get("qa_cnt").owner == "算法数据组"


def test_yaml_roundtrip(tmp_path):
    r = _registry()
    p = tmp_path / "out.yaml"
    r.dump_yaml(p)
    r2 = MetricRegistry()
    r2.load_yaml(p)
    assert r2.get("gmv").caliber_sql == r.get("gmv").caliber_sql


# ---------------------------------------------------------------- 口径 diff


def test_diff_identical():
    d = diff_calibers("SELECT SUM(a) FROM t WHERE x = 1", "SELECT SUM(a) FROM t WHERE x = 1")
    assert d.same and d.detail == "口径完全一致"


def test_diff_different_filter():
    d = diff_calibers("SELECT SUM(a) FROM t WHERE x = 1", "SELECT SUM(a) FROM t WHERE x = 2")
    assert not d.same
    assert d.filters_diff["same"] is False
    assert "过滤差异" in d.detail


def test_diff_different_table():
    d = diff_calibers("SELECT SUM(a) FROM t1", "SELECT SUM(a) FROM t2")
    assert not d.same
    assert d.tables_diff == {"only_left": ["t1"], "only_right": ["t2"], "common": []}


def test_diff_different_agg():
    d = diff_calibers("SELECT SUM(a) FROM t", "SELECT AVG(a) FROM t")
    assert not d.same
    assert d.aggs_diff["only_left"] == ["Sum"] and d.aggs_diff["only_right"] == ["Avg"]


def test_diff_different_column():
    d = diff_calibers("SELECT SUM(a) FROM t", "SELECT SUM(b) FROM t")
    assert not d.same
    assert d.columns_diff["only_left"] == ["a"] and d.columns_diff["only_right"] == ["b"]


def test_diff_no_where_vs_where():
    d = diff_calibers("SELECT SUM(a) FROM t", "SELECT SUM(a) FROM t WHERE status = '成功'")
    assert not d.same and d.filters_diff["same"] is False


# ---------------------------------------------------------------- 指标-血缘联动


def test_metric_downstream_tables():
    graph = LineageGraph()
    graph.add_column_edges(
        [
            ColumnEdge("fact_payment", "pay_amount", "dws_pay_daily", "gmv", "SUM"),
            ColumnEdge("dws_pay_daily", "gmv", "ads_gmv_daily", "gmv", "SUM"),
            ColumnEdge("fact_order", "amount", "dws_trade_daily", "order_amount", "SUM"),
        ]
    )
    metric = MetricDef(name="gmv", owner="a", caliber_sql="SELECT 1", tables=["fact_payment"])
    assert metric_downstream_tables(metric, graph) == ["dws_pay_daily", "ads_gmv_daily"]
