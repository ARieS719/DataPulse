-- staging：支付流水贴源清洗
SELECT
    payment_id,
    order_id,
    user_id,
    pay_time::TIMESTAMP AS pay_time,
    pay_channel,
    {{ normalize_amount('pay_amount') }} AS pay_amount,
    UPPER(COALESCE(currency, 'CNY')) AS currency,
    pay_status
FROM read_csv_auto('{{ var("raw_path") }}/fact_payment.csv', header = true)
