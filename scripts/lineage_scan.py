"""M4：血缘扫描 CLI —— 扫描 SQL 目录，输出统计 / 爆炸半径 / DOT 留痕。

用法：
  python scripts/lineage_scan.py --sql-dir sql --table fact_order
输出：
  docs/benchmarks/lineage_stats.json     （含 <500ms 计时证据）
  docs/benchmarks/lineage_graph.dot      （大盘与可视化备用）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "core"))

from dq_engine.lineage.scanner import scan_directory  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="DataPulse 血缘扫描器")
    ap.add_argument("--sql-dir", default="sql")
    ap.add_argument("--table", default=None, help="输出该表的爆炸半径（下游影响面）")
    ap.add_argument("--out", default="docs/benchmarks")
    args = ap.parse_args()

    graph, files, elapsed = scan_directory(Path(args.sql_dir))

    print(f"扫描 {len(files)} 个脚本，耗时 {elapsed * 1000:.1f} ms")
    print(f"图规模：{graph.stats()}")

    result = {
        "sql_dir": args.sql_dir,
        "files": len(files),
        "elapsed_ms": round(elapsed * 1000, 1),
        "stats": graph.stats(),
        "per_file": [{"path": f.path, "target": f.target_table, "edges": len(f.edges), "error": f.error} for f in files],
    }
    if args.table:
        blast = graph.blast_radius(args.table)
        result["blast"] = blast.to_dict()
        print(f"爆炸半径（{args.table}）：{' → '.join(blast.downstream_tables) or '（无下游）'}")

    out = REPO_ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "lineage_stats.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "lineage_graph.dot").write_text(graph.to_dot(), encoding="utf-8")
    print(f"留痕：{out / 'lineage_stats.json'} + {out / 'lineage_graph.dot'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
