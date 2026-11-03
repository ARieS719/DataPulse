"""M5：Streamlit 数据可观测性大盘。

启动：streamlit run web/dashboard.py

展示：
  * 健康度指标卡（通过率 / P0 熔断 / P1 告警 / 接入表数）
  * 字段级血缘拓扑（graphviz DOT，支持多选标红）
  * 质检历史明细
  * AI-RCA 诊断书全文

数据源（均为可选，缺省优雅降级不 crash）：
  * DQP_PG_DSN：PostgreSQL 连接串（未设置则内存模式，重启清空）
  * DQP_LINEAGE_SQL_DIR：数仓 SQL 脚本目录（未设置则血缘图占位提示）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import streamlit as st
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.scanner import scan_directory

from web.db import QualityStore


@st.cache_resource
def _build_store() -> QualityStore:
    dsn = os.environ.get("DQP_PG_DSN")
    if dsn:
        try:
            import psycopg

            store = QualityStore(conn_factory=lambda: psycopg.connect(dsn))
            store.ensure_schema()
            return store
        except Exception as exc:  # DB 不可用 → 内存降级
            st.sidebar.warning(f"PostgreSQL 连接失败，降级为内存模式：{exc}")
    return QualityStore()


@st.cache_resource
def _build_lineage() -> LineageGraph | None:
    sql_dir = os.environ.get("DQP_LINEAGE_SQL_DIR")
    if not sql_dir or not Path(sql_dir).is_dir():
        return None
    graph, _files, _elapsed = scan_directory(Path(sql_dir))
    return graph


def main() -> None:
    st.set_page_config(page_title="DataPulse 大盘", layout="wide")
    st.title("DataPulse · 数据可观测性大盘")

    store = _build_store()

    # 1) 健康度指标
    summary = store.health_summary()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("质检通过率", f"{summary['overall_pass_rate'] * 100:.1f}%")
    c2.metric("P0 熔断次数", summary["block_count"])
    c3.metric("P1 告警次数", summary["warn_count"])
    c4.metric("已接入表数", summary["table_count"])

    # 2) 血缘拓扑（标红 = 熔断爆炸半径可视化）
    st.subheader("字段级血缘拓扑")
    graph = _build_lineage()
    if graph is not None and graph.stats()["table_nodes"]:
        nodes = sorted(graph.to_networkx("table").nodes)
        highlight = st.multiselect("标红表（模拟熔断爆炸半径）", nodes)
        st.graphviz_chart(graph.to_dot(highlight=highlight))
        if highlight:
            for t in highlight:
                report = graph.blast_radius(t)
                st.caption(f"{t} 下游波及：{' → '.join(report.downstream_tables) or '（无下游）'}")
    else:
        st.info("血缘图未加载：设置环境变量 DQP_LINEAGE_SQL_DIR 指向 SQL 脚本目录后刷新。")

    # 3) 质检历史
    st.subheader("质检历史")
    runs = store.recent_runs(limit=50)
    if runs:
        st.dataframe(runs, use_container_width=True, hide_index=True)
    else:
        st.info("暂无质检记录：M3 门禁跑通后此处展示每次审计结果。")

    # 4) AI-RCA 诊断书
    st.subheader("AI-RCA 诊断书")
    reports = store.recent_reports(limit=10)
    if reports:
        sel = st.selectbox(
            "选择诊断书",
            range(len(reports)),
            format_func=lambda i: f"{reports[i]['table_name']} · {reports[i]['ts']}",
        )
        st.markdown(reports[sel]["markdown"])
    else:
        st.info("暂无诊断书：熔断触发后 AI-RCA 报告自动入库展示。")


if __name__ == "__main__":
    main()
