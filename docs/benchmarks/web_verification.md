# M5 Web 大盘联调验证报告

- 时间：2026-11-04（Docker Desktop v29.8.0）
- 结论：✅ 全部端点验证通过（PostgreSQL 持久化模式 + 血缘图全接线）

## 基础设施

```
docker compose --profile core up -d
# datapulse-postgres  Up (healthy)   postgres:16-alpine, 5432
# datapulse-redis     Up (healthy)   redis:7-alpine, 6379
```

## 服务启动

| 服务 | 命令 | 端口 |
| --- | --- | --- |
| FastAPI 控制面（PG 模式） | `uvicorn scripts.run_api_pg:app --host 127.0.0.1 --port 8000` | 8000 |
| Streamlit 大盘 | `streamlit run web/dashboard.py`（env: DQP_PG_DSN / DQP_LINEAGE_SQL_DIR） | 8501 |

`scripts/run_api_pg.py` 为 M5 联调入口：PostgreSQL 连接 + schema 初始化 + sql/ 目录血缘扫描全接线。

## 端点验证结果

| 端点 | 请求 | 结果 |
| --- | --- | --- |
| GET /health | — | `{"status":"ok","store":"postgres","lineage_loaded":true}` ✅ |
| POST /api/rules/validate | 合法 2 规则 YAML | `{"ok":true,"table":"fact_order","count":2,"types":["unique","range"]}` ✅ |
| POST /api/rules/validate | 非法 YAML | HTTP 422（契约校验拒绝）✅ |
| POST /api/quality/runs | 4 条模拟门禁记录 | run_id 1–4 入库 ✅ |
| GET /api/quality/history | limit=4 | 4 条读回（PostgreSQL 持久化）✅ |
| GET /api/quality/health | — | 通过率 0.5 / 熔断 2 / 接入表 3（按表分组）✅ |
| GET /api/lineage/blast?table=fact_order | — | 下游 `dws_trade_daily → ads_gmv_daily`，max_depth=2 ✅ |
| GET /（Streamlit） | — | HTTP 200（6467 bytes）✅ |

## M6+ 治理扩展端点（TestClient 验证 + 联调接线）

| 端点 | 验证方式 | 结果 |
| --- | --- | --- |
| GET /api/quality/health（质量分） | 混合 PASS/BLOCK/WARN 上报 | 质量分 83.3、C 级（BLOCK 40 分 / WARN 10 分权重）✅ |
| GET /api/quality/sla | 数据字典 sla_map × 门禁历史 | 4 表 never_checked；BLOCK 后转 last_blocked ✅ |
| GET /api/dictionary | 数据字典契约加载 | 4 表 20 字段（口径/类型/分级/脱敏/规则/SLA）✅ |
| GET /api/dictionary/sensitive | min_level=L3 | L3+ 敏感字段清单（user_id 等）✅ |
| GET /api/dictionary/blast | 字典 × 血缘联动 | `ods.dim_user.user_id`（L3/sha2）下游影响面 ✅ |
| 未加载字典/血缘时的降级 | 直接访问 | HTTP 503（字典未加载）/ 404（字段不存在）✅ |

告警闭环：`quality_gate_cli` 在 BLOCK 时经可插拔通知器投递 P0 告警
（日志恒定生效；`DQP_WEBHOOK_URL` 可选钉钉/企微机器人，网络失败优雅兜底）。

## 降级策略（设计要点）

web/db.py 的 QualityStore 在 PG 不可用时自动降级为进程内存模式（控制面可用性优先）；
Streamlit 大盘在缺 DQP_PG_DSN / DQP_LINEAGE_SQL_DIR 时优雅降级不崩溃——均已验证（代码路径 + 启动日志）。
