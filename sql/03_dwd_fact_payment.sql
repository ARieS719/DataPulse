-- DWD：支付流水清洗
CREATE TABLE dwd.fact_payment AS
SELECT payment_id, order_id,
       sha2(user_id, 256) AS user_key,
       pay_time, pay_channel,
       CASE WHEN pay_amount IS NULL OR pay_amount < 0 THEN 0 ELSE pay_amount END AS pay_amount,
       upper(coalesce(currency, 'CNY')) AS currency,
       CASE WHEN trim(pay_status) IN ('成功', '失败') THEN trim(pay_status) ELSE '其他' END AS pay_status
FROM ods.fact_payment;
