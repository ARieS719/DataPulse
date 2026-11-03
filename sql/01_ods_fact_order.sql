-- DataPulse 数仓 ETL 脚本集（血缘扫描演示用，逻辑与 foundation/etl 一致）
-- 用于：python scripts/lineage_scan.py --sql-dir sql

-- ODS：原始贴源（source_trade 为外部贴源表）
CREATE TABLE ods.fact_order AS
SELECT order_id, user_id,
       to_timestamp(order_time, 'yyyy-MM-dd HH:mm:ss') AS order_time,
       channel, category,
       CAST(amount AS DECIMAL(12,2)) AS amount,
       CAST(coupon_discount AS DECIMAL(12,2)) AS coupon_discount,
       status
FROM source_trade.trade_order;
