"""数据字典与敏感分级模块测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge
from dq_engine.metadata import (
    SENSITIVITY_LABELS,
    DataDictionary,
    DictionaryContract,
)
from pydantic import ValidationError

DICT_YAML = Path(__file__).resolve().parents[1] / "core" / "dq_engine" / "metadata" / "contracts" / "dictionary.yaml"


@pytest.fixture(scope="module")
def dd() -> DataDictionary:
    return DataDictionary.from_yaml(DICT_YAML)


def test_contract_loads_all_tables(dd):
    assert dd.tables() == [
        "ods.ai_qa_log",
        "ods.dim_user",
        "ods.fact_order",
        "ods.fact_payment",
    ]


def test_column_lookup_with_definition(dd):
    entry = dd.column("ods.fact_order", "amount")
    assert entry is not None
    assert entry.dtype == "decimal(12,2)"
    assert entry.sensitivity == "L2"
    assert "amount_range" in entry.rules and "pay_amount_align" in entry.rules


def test_sensitive_columns_default_l3_and_above(dd):
    cols = dd.sensitive_columns()  # 默认 min_level=L3
    tables_cols = {(c["table"], c["column"]) for c in cols}
    assert ("ods.dim_user", "user_id") in tables_cols
    assert ("ods.fact_order", "user_id") in tables_cols
    # L1/L2 字段不应出现在默认敏感清单
    assert ("ods.fact_order", "order_id") not in tables_cols
    assert ("ods.fact_order", "amount") not in tables_cols
    assert all(c["label"] == SENSITIVITY_LABELS[c["sensitivity"]] for c in cols)


def test_sensitive_columns_custom_floor(dd):
    cols = dd.sensitive_columns(min_level="L2")
    tables_cols = {(c["table"], c["column"]) for c in cols}
    assert ("ods.fact_order", "amount") in tables_cols


def test_masked_summary(dd):
    summary = dd.masked_summary()
    assert summary["sensitive_total"] == len(dd.sensitive_columns())
    assert summary["masked_in_dict"] > 0
    assert "ods.dim_user.user_id" in summary["implemented"]


def test_sla_map(dd):
    sla = dd.sla_map()
    assert sla["ods.fact_order"] == 12
    assert sla["ods.ai_qa_log"] == 6


def test_downstream_impact_with_lineage(dd):
    graph = LineageGraph()
    graph.add_column_edges(
        [
            ColumnEdge("ods.fact_order", "amount", "dwd.fact_order", "amount", "amount"),
            ColumnEdge("dwd.fact_order", "amount", "dws_trade_daily", "trade_amount", "SUM(amount)"),
            ColumnEdge("dws_trade_daily", "trade_amount", "ads_gmv_daily", "gmv", "SUM(trade_amount)"),
        ]
    )
    report = dd.downstream_impact("ods.fact_order", "user_id", lineage_graph=graph)
    assert report["sensitivity"] == "L3"
    assert report["downstream_tables"] == ["dwd.fact_order", "dws_trade_daily", "ads_gmv_daily"]
    assert report["max_depth"] == 3


def test_downstream_impact_without_lineage(dd):
    report = dd.downstream_impact("ods.dim_user", "user_id")
    assert report["downstream"] is None


def test_downstream_impact_unknown_column(dd):
    with pytest.raises(KeyError):
        dd.downstream_impact("ods.dim_user", "no_such_col")


def test_flat_rows_shape(dd):
    rows = dd.flat_rows()
    assert rows, "字典展平不应为空"
    first = rows[0]
    for key in ("table", "column", "dtype", "definition", "sensitivity", "label", "mask", "rules", "sla_hours"):
        assert key in first
    assert all(r["sensitivity"] in SENSITIVITY_LABELS for r in rows)


def test_invalid_sensitivity_rejected():
    with pytest.raises(ValidationError):
        DictionaryContract.model_validate(
            {
                "version": 1,
                "tables": {
                    "t": {
                        "columns": {"c": {"dtype": "string", "definition": "x", "sensitivity": "L9"}},
                    }
                },
            }
        )
