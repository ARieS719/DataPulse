"""M5：质检历史与诊断报告持久化。

PostgreSQL（psycopg3）为主存储；未配置连接时自动降级为进程内存模式，
保证控制面/大盘在无 DB 环境下仍可启动与演示（降级策略是刻意设计：
控制面可用性优先于存储完备性，M6 评测不受影响）。

表结构：
  quality_runs(run_id, ts, table_name, verdict, total_rows, branch, merged, duration_ms, details JSONB)
  rca_reports(report_id, ts, table_name, classification, generated_by, markdown)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

DDL = [
    """CREATE TABLE IF NOT EXISTS quality_runs (
        run_id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        table_name TEXT NOT NULL,
        verdict TEXT NOT NULL,
        total_rows BIGINT,
        branch TEXT,
        merged BOOLEAN,
        duration_ms DOUBLE PRECISION,
        details JSONB
    )""",
    """CREATE TABLE IF NOT EXISTS rca_reports (
        report_id BIGSERIAL PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL DEFAULT now(),
        table_name TEXT NOT NULL,
        classification TEXT NOT NULL,
        generated_by TEXT NOT NULL,
        markdown TEXT NOT NULL
    )""",
]

ConnFactory = Callable[[], Any]


class QualityStore:
    """conn_factory 提供 psycopg 连接则为 PG 模式；None 为内存降级模式。"""

    def __init__(self, conn_factory: ConnFactory | None = None):
        self._conn_factory = conn_factory
        self._memory_runs: list[dict] = []
        self._memory_reports: list[dict] = []

    @property
    def available(self) -> bool:
        return self._conn_factory is not None

    def ensure_schema(self) -> None:
        if not self.available:
            return
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                for ddl in DDL:
                    cur.execute(ddl)
            conn.commit()

    # ------------------------------------------------------------ runs

    def save_run(self, run: dict) -> int:
        """run: {table_name, verdict, total_rows, branch, merged, duration_ms, details}"""
        if not self.available:
            self._memory_runs.insert(0, {**run, "ts": datetime.now(timezone.utc).isoformat()})
            return len(self._memory_runs)
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO quality_runs
                        (table_name, verdict, total_rows, branch, merged, duration_ms, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING run_id
                    """,
                    (
                        run["table_name"],
                        run["verdict"],
                        run.get("total_rows"),
                        run.get("branch"),
                        run.get("merged"),
                        run.get("duration_ms"),
                        json.dumps(run.get("details") or {}, ensure_ascii=False),
                    ),
                )
                run_id = cur.fetchone()[0]
            conn.commit()
            return int(run_id)

    def recent_runs(self, table: str | None = None, limit: int = 50) -> list[dict]:
        if not self.available:
            rows = [r for r in self._memory_runs if table is None or r["table_name"] == table]
            return rows[:limit]
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                if table:
                    cur.execute(
                        "SELECT run_id, ts, table_name, verdict, total_rows, branch, merged, duration_ms, details "
                        "FROM quality_runs WHERE table_name = %s ORDER BY ts DESC LIMIT %s",
                        (table, limit),
                    )
                else:
                    cur.execute(
                        "SELECT run_id, ts, table_name, verdict, total_rows, branch, merged, duration_ms, details "
                        "FROM quality_runs ORDER BY ts DESC LIMIT %s",
                        (limit,),
                    )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # ------------------------------------------------------------ reports

    def save_report(self, report: dict) -> int:
        """report: {table_name, classification, generated_by, markdown}"""
        if not self.available:
            self._memory_reports.insert(0, {**report, "ts": datetime.now(timezone.utc).isoformat()})
            return len(self._memory_reports)
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO rca_reports (table_name, classification, generated_by, markdown) "
                    "VALUES (%s, %s, %s, %s) RETURNING report_id",
                    (report["table_name"], report["classification"], report["generated_by"], report["markdown"]),
                )
                rid = cur.fetchone()[0]
            conn.commit()
            return int(rid)

    def recent_reports(self, table: str | None = None, limit: int = 10) -> list[dict]:
        if not self.available:
            rows = [r for r in self._memory_reports if table is None or r["table_name"] == table]
            return rows[:limit]
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                if table:
                    cur.execute(
                        "SELECT report_id, ts, table_name, classification, generated_by, markdown "
                        "FROM rca_reports WHERE table_name = %s ORDER BY ts DESC LIMIT %s",
                        (table, limit),
                    )
                else:
                    cur.execute(
                        "SELECT report_id, ts, table_name, classification, generated_by, markdown "
                        "FROM rca_reports ORDER BY ts DESC LIMIT %s",
                        (limit,),
                    )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # ------------------------------------------------------------ 健康度

    def health_summary(self, limit: int = 500) -> dict:
        runs = self.recent_runs(limit=limit)
        by_table: dict[str, dict] = {}
        for r in runs:
            t = r["table_name"]
            b = by_table.setdefault(t, {"runs": 0, "passed": 0, "blocks": 0, "warns": 0})
            b["runs"] += 1
            if r["verdict"] == "PASS":
                b["passed"] += 1
            elif r["verdict"] == "BLOCK":
                b["blocks"] += 1
            else:
                b["warns"] += 1
        tables = [
            {"table": t, **b, "pass_rate": round(b["passed"] / b["runs"], 4)} for t, b in by_table.items()
        ]
        for row in tables:
            score = compute_quality_score([r for r in runs if r["table_name"] == row["table"]])
            row["quality_score"] = score["overall"]
            row["quality_grade"] = score["grade"]
        total = len(runs)
        score = compute_quality_score(runs)
        return {
            "overall_pass_rate": round(sum(r["verdict"] == "PASS" for r in runs) / total, 4) if total else 0.0,
            "block_count": sum(r["verdict"] == "BLOCK" for r in runs),
            "warn_count": sum(r["verdict"] == "WARN" for r in runs),
            "table_count": len(tables),
            "tables": tables,
            "quality_score": score["overall"],
            "quality_grade": score["grade"],
        }


# ------------------------------------------------------------ 质量分与 SLA（纯函数，可单测）


def _grade(score: float) -> str:
    if score >= 95:
        return "A"
    if score >= 85:
        return "B"
    if score >= 70:
        return "C"
    return "D"


def compute_quality_score(runs: list[dict]) -> dict:
    """质量分（100 分制）：BLOCK 权重 40、WARN 权重 10，按占比扣分。

    分级：A ≥95 / B ≥85 / C ≥70 / D <70。
    """
    total = len(runs)
    if not total:
        return {"overall": 100.0, "grade": "A", "blocks": 0, "warns": 0, "passes": 0}
    blocks = sum(r["verdict"] == "BLOCK" for r in runs)
    warns = sum(r["verdict"] == "WARN" for r in runs)
    penalty = 40 * (blocks / total) + 10 * (warns / total)
    score = round(max(0.0, 100 - penalty), 1)
    return {"overall": score, "grade": _grade(score), "blocks": blocks, "warns": warns, "passes": total - blocks - warns}


def sla_breaches(runs: list[dict], sla_map: dict, now=None) -> list[dict]:
    """超 SLA 清单：最近一次门禁距 now 超过 sla_hours，或最近一次 verdict=BLOCK。

    runs：recent_runs() 输出（按 ts 降序）；sla_map：{table: hours}。
    """
    from datetime import datetime, timezone

    latest: dict[str, dict] = {}
    for r in runs:
        latest.setdefault(r["table_name"], r)
    now = now or datetime.now(timezone.utc)
    breaches = []
    for table, hours in sla_map.items():
        last = latest.get(table)
        if last is None:
            breaches.append({"table": table, "sla_hours": hours, "reason": "never_checked", "last_verdict": None})
            continue
        last_verdict = last["verdict"]
        ts = last["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if last_verdict == "BLOCK":
            breaches.append({"table": table, "sla_hours": hours, "reason": "last_blocked", "last_verdict": last_verdict})
        elif (now - ts).total_seconds() / 3600 > hours:
            breaches.append({"table": table, "sla_hours": hours, "reason": "stale", "last_verdict": last_verdict})
    return breaches
