-- marts：订单事实表（星型模型：事实关联维度代理键）
SELECT
    o.order_id,
    d.user_key,
    o.order_time,
    o.channel,
    o.category,
    o.amount,
    o.coupon_discount,
    o.status
FROM {{ ref('stg_orders') }} o
LEFT JOIN {{ ref('dim_user') }} d
    ON o.user_id = d.user_id
