# M3 尾巴 · Airflow 调度验证报告

- 时间：2026-11-04（Airflow 2.9.3 + LocalExecutor，自定义镜像 datapulse-airflow:2.9.3）
- 结论：✅ DAG `datapulse_etl_wap` 7/7 任务全部 success（含 WAP 写入审计分支 → 门禁 → 原子合并全链路）

## 运行环境

```
docker compose --profile airflow up -d --build
# datapulse-airflow-init       Exited (0)（db migrate + admin 用户创建）
# datapulse-airflow-webserver  Up（http://localhost:8080，admin/admin）
# datapulse-airflow-scheduler  Up（LocalExecutor，业务库共用 postgres）
```

自定义镜像：`apache/airflow:2.9.3-python3.11` + openjdk-17 + pyspark/sqlglot 等项目依赖（airflow/Dockerfile.airflow）。
DAG 目录挂载仓库根，`.airflowignore` 排除 airflow/logs 挂载点（修复递归扫描环路 RuntimeError）。

## 任务级结果（run_id=manual__2026-11-03T19:12:03+00:00）

| 任务 | 状态 | 耗时 | 说明 |
| --- | --- | --- | --- |
| gen_data | success | 1.0s | 生成 small 档 4 表 CSV |
| ods_write_audit | success | 28.2s | 建空表 + 审计分支 + WAP 写入（main 快照不变） |
| quality_gate | success | 18.6s | 合并扫描质检 → 3 表全 PASS → fast_forward 原子合并 |
| build_dwd | success | 29.1s | DWD 落盘（读已合并的 main） |
| build_dws | success | 24.5s | DWS 落盘 |
| build_ads | success | 22.9s | ADS 落盘 |
| reconcile | success | 16.2s | 跨层对账 3/3 PASS |

## 关键日志摘录

```
quality_gate:  [fact_order  ] verdict=PASS   merged=True (6981.8 ms)
               [fact_payment] verdict=PASS   merged=True (2036.8 ms)
               [ai_qa_log   ] verdict=PASS   merged=True (396.4 ms)
               ✅ 全部门禁通过，审计分支已原子合并
reconcile:     [PASS] DWS 交易额 6520170.40 == DWD 明细合计 6520170.40
               [PASS] DWS GMV 5966088.50 == DWD 成功流水合计 5966088.50
               [PASS] DWD 行数 10000 == ODS 行数 10000
```

## 过程中修复的两个坑（已固化进仓库）

1. **DAG 发现递归环路**（RuntimeError: recursive loop）：dags_folder 挂载仓库根，扫描进入 airflow/logs 挂载点 → 新增根目录 `.airflowignore` 排除。
2. **全新 warehouse 首跑失败**（TABLE_OR_VIEW_NOT_FOUND）：ods_wap_write.py 原假设 ODS 表已存在 → 增加 `_ensure_table`（首跑建空表，保持 WAP 语义：批数据只进审计分支、main 空快照直到门禁通过后 fast_forward）。
