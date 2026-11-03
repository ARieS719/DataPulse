-- staging：订单贴源清洗（金额规整复用宏 normalize_amount）
SELECT
    order_id,
    user_id,
    order_time::TIMESTAMP AS order_time,
    channel,
    category,
    {{ normalize_amount('amount') }} AS amount,
    {{ normalize_amount('coupon_discount') }} AS coupon_discount,
    status
FROM read_csv_auto('{{ var("raw_path") }}/fact_order.csv', header = true)
