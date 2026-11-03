"""FastAPI 控制面单元测试（TestClient，无需真实服务器/DB/Spark）。"""

from __future__ import annotations

import pytest
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge
from fastapi.testclient import TestClient

from web.api import app, set_chaos_runner, set_lineage_graph, set_store
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


@pytest.fixture()
def client():
    set_store(QualityStore())  # 内存模式，隔离测试
    set_lineage_graph(None)
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


def test_chaos_stub_then_runner(client):
    r = client.post("/api/chaos/trigger", json={"fault_type": "duplicate_pk", "ratio": 0.12})
    assert r.status_code == 501
    set_chaos_runner(lambda fault_type, target_table, ratio: {"fault_type": fault_type, "injected": ratio})
    r2 = client.post("/api/chaos/trigger", json={"fault_type": "duplicate_pk", "target_table": "fact_order", "ratio": 0.12})
    assert r2.status_code == 200 and r2.json()["injected"] == 0.12
