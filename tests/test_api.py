"""FastAPI 控制面单元测试（TestClient，无需真实服务器/DB/Spark）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge
from dq_engine.metadata import DataDictionary
from fastapi.testclient import TestClient

from web.api import app, set_chaos_runner, set_dictionary, set_lineage_graph, set_store
from web.db import QualityStore

VALID_YAML = """
version: 1
table: fact_order
rules:
  - name: order_id_unique
    type: unique
    level: P0
    column: order_id
  - name: amount_range
    type: range
    level: P1
    column: amount
    min: 0.01
"""

DICT_YAML = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "dq_engine"
    / "metadata"
    / "contracts"
    / "dictionary.yaml"
)


@pytest.fixture()
def client():
    set_store(QualityStore())  # 内存模式，隔离测试
    set_lineage_graph(None)
    set_dictionary(None)
    set_chaos_runner(None)
    with TestClient(app) as c:
        yield c


def _chain_graph():
    g = LineageGraph()
    g.add_column_edges(
        [
            ColumnEdge("t1", "a", "t2", "a", "a"),
            ColumnEdge("t2", "a", "t3", "a", "a"),
            ColumnEdge("t3", "a", "t4", "a", "a"),
        ]
    )
    return g


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["store"] == "memory" and not body["lineage_loaded"]


def test_rules_validate_ok(client):
    r = client.post("/api/rules/validate", json={"yaml_text": VALID_YAML})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["table"] == "fact_order" and body["count"] == 2


def test_rules_validate_invalid_rule(client):
    bad = VALID_YAML.replace("type: range", "type: magic")
    r = client.post("/api/rules/validate", json={"yaml_text": bad})
    assert r.status_code == 422
    assert "校验失败" in r.json()["detail"]


def test_rules_validate_yaml_syntax_error(client):
    r = client.post("/api/rules/validate", json={"yaml_text": "::: not yaml"})
    assert r.status_code == 422


def test_blast_radius(client):
    set_lineage_graph(_chain_graph())
    r = client.get("/api/lineage/blast", params={"table": "t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["downstream_tables"] == ["t2", "t3", "t4"] and body["max_depth"] == 3


def test_blast_without_graph_returns_503(client):
    r = client.get("/api/lineage/blast", params={"table": "t1"})
    assert r.status_code == 503


def test_upstream(client):
    set_lineage_graph(_chain_graph())
    r = client.get("/api/lineage/upstream", params={"table": "t4"})
    assert r.json()["upstream"] == ["t3", "t2", "t1"]


def test_report_run_and_history(client):
    r1 = client.post(
        "/api/quality/runs",
        json={"table_name": "fact_order", "verdict": "PASS", "total_rows": 100, "merged": True, "details": {}},
    )
    r2 = client.post(
        "/api/quality/runs",
        json={"table_name": "fact_order", "verdict": "BLOCK", "total_rows": 100, "merged": False, "details": {}},
    )
    assert r1.json()["run_id"] == 1 and r2.json()["run_id"] == 2
    hist = client.get("/api/quality/history").json()["runs"]
    assert len(hist) == 2 and hist[0]["verdict"] == "BLOCK"  # 最新在前


def test_health_summary(client):
    client.post("/api/quality/runs", json={"table_name": "a", "verdict": "PASS"})
    client.post("/api/quality/runs", json={"table_name": "a", "verdict": "BLOCK"})
    client.post("/api/quality/runs", json={"table_name": "b", "verdict": "WARN"})
    s = client.get("/api/quality/health").json()
    assert s["overall_pass_rate"] == round(1 / 3, 4)
    assert s["block_count"] == 1 and s["warn_count"] == 1 and s["table_count"] == 2
    # 质量分：1 BLOCK(40) + 1 WARN(10) 占总 3 条 → 100 - (40+10)/3*... 见 compute_quality_score
    assert s["quality_score"] == round(100 - 40 / 3 - 10 / 3, 1)
    assert s["quality_grade"] == "C"  # 83.3 → C（≥70 <85）
    assert all("quality_score" in t for t in s["tables"])


def test_dictionary_endpoints_without_dictionary_return_503(client):
    assert client.get("/api/dictionary").status_code == 503
    assert client.get("/api/dictionary/sensitive").status_code == 503
    assert client.get("/api/quality/sla").status_code == 503


def test_dictionary_list_and_sensitive(client):
    set_dictionary(DataDictionary.from_yaml(DICT_YAML))
    r = client.get("/api/dictionary")
    assert r.status_code == 200
    body = r.json()
    assert body["tables"] == ["ods.ai_qa_log", "ods.dim_user", "ods.fact_order", "ods.fact_payment"]
    assert body["columns"] == len(body["rows"]) > 0
    cols = {c["column"] for c in body["rows"] if c["table"] == "ods.fact_order"}
    assert {"order_id", "user_id", "amount", "status"}.issubset(cols)

    r2 = client.get("/api/dictionary/sensitive")
    assert r2.status_code == 200
    sensitive = r2.json()["columns"]
    tables_cols = {(c["table"], c["column"]) for c in sensitive}
    assert ("ods.dim_user", "user_id") in tables_cols
    assert all(c["sensitivity"] in ("L3", "L4") for c in sensitive)


def test_dictionary_blast_with_lineage(client):
    set_dictionary(DataDictionary.from_yaml(DICT_YAML))
    set_lineage_graph(_chain_graph())
    r = client.get("/api/dictionary/blast", params={"table": "ods.dim_user", "column": "user_id"})
    assert r.status_code == 200
    assert r.json()["sensitivity"] == "L3" and r.json()["mask"] == "sha2"


def test_dictionary_blast_unknown_column_returns_404(client):
    set_dictionary(DataDictionary.from_yaml(DICT_YAML))
    set_lineage_graph(_chain_graph())
    r = client.get("/api/dictionary/blast", params={"table": "ods.dim_user", "column": "nope"})
    assert r.status_code == 404


def test_sla_breaches(client):
    set_dictionary(DataDictionary.from_yaml(DICT_YAML))
    # 未上报任何门禁 → 全部 4 张带 SLA 的表均 never_checked
    body = client.get("/api/quality/sla").json()
    assert len(body["breaches"]) == 4
    assert all(b["reason"] == "never_checked" for b in body["breaches"])
    # 上报 BLOCK → 该表转为 last_blocked
    client.post("/api/quality/runs", json={"table_name": "ods.fact_order", "verdict": "BLOCK"})
    body = client.get("/api/quality/sla").json()
    by_table = {b["table"]: b for b in body["breaches"]}
    assert by_table["ods.fact_order"]["reason"] == "last_blocked"


def test_chaos_stub_then_runner(client):
    r = client.post("/api/chaos/trigger", json={"fault_type": "duplicate_pk", "ratio": 0.12})
    assert r.status_code == 501
    set_chaos_runner(lambda fault_type, target_table, ratio: {"fault_type": fault_type, "injected": ratio})
    r2 = client.post("/api/chaos/trigger", json={"fault_type": "duplicate_pk", "target_table": "fact_order", "ratio": 0.12})
    assert r2.status_code == 200 and r2.json()["injected"] == 0.12
