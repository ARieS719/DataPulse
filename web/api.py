"""M5：FastAPI 控制面。

端点：
  GET  /health                存活 + 存储模式 + 血缘加载状态
  POST /api/rules/validate    YAML 规则契约校验（pydantic，非法规则 422）
  GET  /api/lineage/blast     血缘爆炸半径（BFS）
  GET  /api/lineage/upstream  血缘上游追溯
  POST /api/quality/runs      质检结果上报（M3 门禁执行器调用）
  GET  /api/quality/history   质检历史
  GET  /api/quality/health    健康度汇总（通过率/熔断/告警/表数 + 质量分/分级）
  GET  /api/quality/sla       超 SLA 清单（结合数据字典表级 sla_hours）
  GET  /api/dictionary        数据字典（口径/类型/分级/脱敏/关联规则/SLA）
  GET  /api/dictionary/sensitive  敏感字段清单（L1-L4 分级）
  GET  /api/dictionary/blast  敏感字段下游影响面（字典 × 血缘）
  POST /api/chaos/trigger     混沌故障触发（M6 接入 runner）

启动：uvicorn web.api:app --host 127.0.0.1 --port 8000
生产接线：set_store / set_lineage_graph / set_dictionary
"""

from __future__ import annotations

import sys
from pathlib import Path

# 未安装 editable 包时保证 dq_engine（core/）可导入
_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import yaml
from dq_engine.lineage.graph import LineageGraph
from dq_engine.metadata import DataDictionary
from dq_engine.rules.schema import Ruleset
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from web.db import QualityStore, sla_breaches

app = FastAPI(title="DataPulse Control Plane", version="0.1.0")

_store = QualityStore()  # 生产：QualityStore(conn_factory=lambda: psycopg.connect(dsn))
_lineage_graph: LineageGraph | None = None
_dictionary: DataDictionary | None = None
_chaos_runner = None


def get_store() -> QualityStore:
    return _store


def set_store(store: QualityStore) -> None:
    global _store
    _store = store


def set_lineage_graph(graph: LineageGraph | None) -> None:
    global _lineage_graph
    _lineage_graph = graph


def set_dictionary(dictionary: DataDictionary | None) -> None:
    global _dictionary
    _dictionary = dictionary


def set_chaos_runner(runner) -> None:
    global _chaos_runner
    _chaos_runner = runner


class RulesetText(BaseModel):
    yaml_text: str


class QualityRunReport(BaseModel):
    table_name: str
    verdict: str
    total_rows: int | None = None
    branch: str | None = None
    merged: bool | None = None
    duration_ms: float | None = None
    details: dict = Field(default_factory=dict)


class ChaosRequest(BaseModel):
    fault_type: str
    target_table: str | None = None
    ratio: float = Field(default=0.05, gt=0.0, le=1.0)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "store": "postgres" if _store.available else "memory",
        "lineage_loaded": _lineage_graph is not None,
        "chaos_ready": _chaos_runner is not None,
    }


@app.post("/api/rules/validate")
def validate_ruleset(body: RulesetText):
    try:
        rs = Ruleset.model_validate(yaml.safe_load(body.yaml_text))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"规则契约校验失败: {exc}") from exc
    return {"ok": True, "table": rs.table, "count": len(rs.rules), "types": [r.type for r in rs.rules]}


@app.get("/api/lineage/blast")
def blast(table: str = Query(...)):
    if _lineage_graph is None:
        raise HTTPException(status_code=503, detail="血缘图未加载：先扫描 SQL 脚本目录并 set_lineage_graph")
    return _lineage_graph.blast_radius(table).to_dict()


@app.get("/api/lineage/upstream")
def upstream(table: str = Query(...)):
    if _lineage_graph is None:
        raise HTTPException(status_code=503, detail="血缘图未加载：先扫描 SQL 脚本目录并 set_lineage_graph")
    return {"table": table, "upstream": _lineage_graph.table_upstream(table)}


@app.post("/api/quality/runs")
def report_run(body: QualityRunReport, store: QualityStore = Depends(get_store)):
    run_id = store.save_run(body.model_dump())
    return {"run_id": run_id}


@app.get("/api/quality/history")
def history(
    table: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    store: QualityStore = Depends(get_store),
):
    return {"runs": store.recent_runs(table=table, limit=limit)}


@app.get("/api/quality/health")
def health_summary(store: QualityStore = Depends(get_store)):
    return store.health_summary()


@app.get("/api/quality/sla")
def sla_status(store: QualityStore = Depends(get_store)):
    """超 SLA 清单：结合数据字典的表级 sla_hours 与门禁历史。"""
    if _dictionary is None:
        raise HTTPException(status_code=503, detail="数据字典未加载：先 set_dictionary")
    return {"breaches": sla_breaches(store.recent_runs(), _dictionary.sla_map())}


@app.get("/api/dictionary")
def dictionary_list(table: str | None = Query(None)):
    """数据字典：字段口径/类型/敏感分级/脱敏/关联规则/表级 SLA。"""
    if _dictionary is None:
        raise HTTPException(status_code=503, detail="数据字典未加载：先 set_dictionary")
    rows = [r for r in _dictionary.flat_rows() if table is None or r["table"] == table]
    return {"tables": _dictionary.tables(), "columns": len(rows), "rows": rows}


@app.get("/api/dictionary/sensitive")
def dictionary_sensitive(min_level: str = Query("L3")):
    if _dictionary is None:
        raise HTTPException(status_code=503, detail="数据字典未加载：先 set_dictionary")
    return {"min_level": min_level, "columns": _dictionary.sensitive_columns(min_level=min_level)}


@app.get("/api/dictionary/blast")
def dictionary_blast(table: str = Query(...), column: str = Query(...)):
    """敏感字段所在表的下游影响面（数据字典 × 血缘联动）。"""
    if _dictionary is None:
        raise HTTPException(status_code=503, detail="数据字典未加载：先 set_dictionary")
    if _lineage_graph is None:
        raise HTTPException(status_code=503, detail="血缘图未加载：先扫描 SQL 脚本目录并 set_lineage_graph")
    try:
        return _dictionary.downstream_impact(table, column, lineage_graph=_lineage_graph)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"字典中不存在该字段: {exc}") from exc


@app.post("/api/chaos/trigger")
def chaos_trigger(body: ChaosRequest):
    if _chaos_runner is None:
        raise HTTPException(status_code=501, detail="混沌注入器将在 M6 接入（或先调用 set_chaos_runner 注册）")
    return _chaos_runner(body.fault_type, body.target_table, body.ratio)
