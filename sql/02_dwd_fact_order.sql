-- DWD：明细清洗（匿名化 + 空值规整 + 枚举标准化）
CREATE TABLE dwd.fact_order AS
SELECT order_id,
       sha2(user_id, 256) AS user_key,
       order_time, channel, category,
       CASE WHEN amount IS NULL OR amount <= 0 THEN 0 ELSE amount END AS amount,
       CASE WHEN coupon_discount IS NULL OR coupon_discount < 0 THEN 0 ELSE coupon_discount END AS coupon_discount,
       CASE WHEN trim(status) IN ('已支付', '待支付', '已取消', '已退款')
            THEN trim(status) ELSE '其他' END AS status
FROM ods.fact_order;
