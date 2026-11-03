-- DWS：日 × 支付渠道 GMV 汇总
CREATE TABLE dws.dws_pay_daily AS
SELECT date(pay_time) AS ds, pay_channel AS channel,
       COUNT(*) AS pay_cnt,
       SUM(CASE WHEN pay_status = '成功' THEN pay_amount ELSE 0 END) AS gmv,
       COUNT(DISTINCT user_key) AS pay_user_cnt,
       SUM(CASE WHEN pay_status = '成功' THEN 1 ELSE 0 END) AS success_cnt
FROM dwd.fact_payment
GROUP BY date(pay_time), pay_channel;
