"""以 PostgreSQL + 血缘图全接线模式启动 FastAPI 控制面（M5 联调入口）。

用法：uvicorn scripts.run_api_pg:app --host 127.0.0.1 --port 8000
要求：docker compose --profile core up -d（postgres:5432，datapulse/datapulse）
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import psycopg  # noqa: E402
from dq_engine.lineage.scanner import scan_directory  # noqa: E402

from web.api import set_lineage_graph, set_store  # noqa: E402
from web.db import QualityStore  # noqa: E402

DSN = "postgresql://datapulse:datapulse@127.0.0.1:5432/datapulse"

_store = QualityStore(conn_factory=lambda: psycopg.connect(DSN))
_store.ensure_schema()
set_store(_store)

# 血缘图：扫描 sql/ 目录（6 个分层脚本），供 /api/lineage/* 端点使用
_graph, _files, _elapsed_ms = scan_directory(_REPO / "sql")
set_lineage_graph(_graph)
print(f"run_api_pg: PG 已接线（{len(_files)} 个 SQL 脚本 → {_graph.stats()['table_nodes']} 张表血缘，{_elapsed_ms}ms）")
