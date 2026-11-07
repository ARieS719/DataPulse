"""以 PostgreSQL + 血缘图 + 数据字典全接线模式启动 FastAPI 控制面（M5/M6 联调入口）。

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
from dq_engine.metadata import DataDictionary  # noqa: E402

from web.api import set_dictionary, set_lineage_graph, set_store  # noqa: E402
from web.db import QualityStore  # noqa: E402

DSN = "postgresql://datapulse:datapulse@127.0.0.1:5432/datapulse"

_store = QualityStore(conn_factory=lambda: psycopg.connect(DSN))
_store.ensure_schema()
set_store(_store)

# 血缘图：扫描 sql/ 目录（6 个分层脚本），供 /api/lineage/* 与字典影响面端点使用
_graph, _files, _elapsed_ms = scan_directory(_REPO / "sql")
set_lineage_graph(_graph)
print(f"run_api_pg: PG 已接线（{len(_files)} 个 SQL 脚本 → {_graph.stats()['table_nodes']} 张表血缘，{_elapsed_ms}ms）")

# 数据字典：字段口径/分级/脱敏/表级 SLA，供 /api/dictionary/* 与 /api/quality/sla 使用
_dictionary = DataDictionary.from_yaml(_REPO / "core" / "dq_engine" / "metadata" / "contracts" / "dictionary.yaml")
set_dictionary(_dictionary)
print(
    "run_api_pg: 数据字典已加载（"
    f"{len(_dictionary.tables())} 张表 / {len(_dictionary.flat_rows())} 个字段 / "
    f"{len(_dictionary.sensitive_columns())} 个 L3+ 敏感字段）"
)
