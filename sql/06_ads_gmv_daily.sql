-- ADS：GMV 口径日报（交易 × 支付 对齐）
CREATE TABLE ads.ads_gmv_daily AS
SELECT COALESCE(p.ds, t.ds) AS ds,
       COALESCE(p.gmv, 0) AS gmv,
       COALESCE(t.order_cnt, 0) AS order_cnt,
       COALESCE(p.pay_cnt, 0) AS pay_cnt
FROM (
    SELECT ds, SUM(gmv) AS gmv, SUM(pay_cnt) AS pay_cnt
    FROM dws.dws_pay_daily GROUP BY ds
) p
FULL OUTER JOIN (
    SELECT ds, SUM(order_cnt) AS order_cnt
    FROM dws.dws_trade_daily GROUP BY ds
) t ON p.ds = t.ds;
