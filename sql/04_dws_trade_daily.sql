-- DWS：日 × 渠道 交易汇总
CREATE TABLE dws.dws_trade_daily AS
SELECT date(order_time) AS ds, channel,
       COUNT(*) AS order_cnt,
       COUNT(DISTINCT user_key) AS user_cnt,
       SUM(amount) AS order_amount,
       ROUND(AVG(amount), 2) AS avg_order_amount
FROM dwd.fact_order
WHERE status = '已支付'
GROUP BY date(order_time), channel;
