"""DataPulse 批式流水线 DAG（M3：Airflow + WAP 熔断编排）。

WAP 熔断语义（核心）：
  gen_data → ods_write_audit（写入审计分支，main 不可见）
          → quality_gate（合并扫描质检；任一 P0 失败退出码非 0）
          → build_dwd/dws/ads（依赖门禁成功；BLOCK 时下游自动挂起）
          → reconcile（跨层对账收口）

本机运行说明：
  * Airflow 以自定义镜像运行（airflow/Dockerfile.airflow：含 Java17 + 项目依赖）；
  * 仓库根目录挂载至 /opt/airflow/dags，DAG 文件位于 airflow/dags/etl_pipeline.py；
  * 运行期产物（warehouse/data）写入容器内 /tmp/dqp（每次 cd 前缀），避免与宿主机冲突。
"""

from __future__ import annotations

from datetime import datetime

from airflow.operators.bash import BashOperator

from airflow import DAG

REPO = "/opt/airflow/dags"
WORK = "/tmp/dqp"

default_args = {"owner": "datapulse", "depends_on_past": False, "retries": 1}

with DAG(
    dag_id="datapulse_etl_wap",
    default_args=default_args,
    description="湖仓分层 ETL + WAP 写入前质量熔断门禁",
    schedule_interval=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["datapulse", "quality-gate"],
) as dag:

    gen_data = BashOperator(
        task_id="gen_data",
        bash_command=f"cd {WORK} && python {REPO}/foundation/data_gen/generate.py --scale small --out {WORK}/data/raw",
    )

    ods_write_audit = BashOperator(
        task_id="ods_write_audit",
        bash_command=f"cd {WORK} && python {REPO}/scripts/ods_wap_write.py --raw-dir {WORK}/data/raw",
    )

    quality_gate = BashOperator(
        task_id="quality_gate",
        bash_command=f"cd {WORK} && python {REPO}/scripts/quality_gate_cli.py",
    )

    build_dwd = BashOperator(
        task_id="build_dwd",
        bash_command=f"cd {WORK} && python {REPO}/foundation/etl/etl_dwd.py",
    )
    build_dws = BashOperator(
        task_id="build_dws",
        bash_command=f"cd {WORK} && python {REPO}/foundation/etl/etl_dws.py",
    )
    build_ads = BashOperator(
        task_id="build_ads",
        bash_command=f"cd {WORK} && python {REPO}/foundation/etl/etl_ads.py",
    )
    reconcile = BashOperator(
        task_id="reconcile",
        bash_command=f"cd {WORK} && python {REPO}/scripts/reconcile.py",
    )

    gen_data >> ods_write_audit >> quality_gate >> build_dwd >> build_dws >> build_ads >> reconcile
