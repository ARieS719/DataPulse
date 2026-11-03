"""DataPulse 批式流水线手动执行器（M3 引入 Airflow DAG 前的过渡编排）。

用法：
  python scripts/run_pipeline.py --scale small --stages all
  python scripts/run_pipeline.py --scale medium --stages ods,dwd,dws,ads   # 复用已生成的数据

每阶段耗时追加记录到 docs/benchmarks/pipeline_runs.md
（这是 S2 支线 Spark 调优对比实验的证据来源）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from foundation.etl.etl_ads import build_ads  # noqa: E402
from foundation.etl.etl_dwd import build_dwd  # noqa: E402
from foundation.etl.etl_dws import build_dws  # noqa: E402
from foundation.etl.etl_ods import load_ods  # noqa: E402
from foundation.etl.spark import build_spark  # noqa: E402

STAGES = ("gen", "ods", "dwd", "dws", "ads")


def stage_gen(scale: str, out: str) -> None:
    script = REPO_ROOT / "foundation" / "data_gen" / "generate.py"
    result = subprocess.run([sys.executable, str(script), "--scale", scale, "--out", out], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"数据生成失败（退出码 {result.returncode}）")


def main() -> int:
    ap = argparse.ArgumentParser(description="DataPulse 批式流水线执行器")
    ap.add_argument("--scale", choices=["small", "medium", "large"], default="small")
    ap.add_argument("--stages", default="all", help="逗号分隔：gen,ods,dwd,dws,ads 或 all")
    ap.add_argument("--raw-dir", default="data/raw")
    args = ap.parse_args()

    stages = list(STAGES) if args.stages == "all" else [s.strip() for s in args.stages.split(",")]
    for s in stages:
        if s not in STAGES:
            print(f"未知阶段：{s}（可选 {','.join(STAGES)}）", file=sys.stderr)
            return 2

    timings: dict[str, float] = {}
    spark = None
    try:
        for stage in stages:
            t0 = time.time()
            if stage == "gen":
                stage_gen(args.scale, args.raw_dir)
            else:
                if spark is None:
                    spark = build_spark(f"datapulse-pipeline-{args.scale}")
                if stage == "ods":
                    load_ods(spark, raw_dir=args.raw_dir)
                elif stage == "dwd":
                    build_dwd(spark)
                elif stage == "dws":
                    build_dws(spark)
                elif stage == "ads":
                    build_ads(spark)
            timings[stage] = round(time.time() - t0, 1)
            print(f"[{stage}] 完成，耗时 {timings[stage]}s")
    finally:
        if spark is not None:
            spark.stop()

    bench_dir = REPO_ROOT / "docs" / "benchmarks"
    bench_dir.mkdir(parents=True, exist_ok=True)
    bench_file = bench_dir / "pipeline_runs.md"
    if not bench_file.exists():
        bench_file.write_text("| 时间 | scale | 阶段 | 耗时(秒) |\n| --- | --- | --- | --- |\n", encoding="utf-8")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with bench_file.open("a", encoding="utf-8") as f:
        for stage, secs in timings.items():
            f.write(f"| {ts} | {args.scale} | {stage} | {secs} |\n")
    print("耗时留痕：docs/benchmarks/pipeline_runs.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
