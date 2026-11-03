-- marts：GMV 日报（口径统一：gmv = 成功支付金额合计，指标字典见 docs/metrics.md / S3 支线）
SELECT
    date_trunc('day', pay_time) AS ds,
    SUM(CASE WHEN pay_status = '成功' THEN pay_amount ELSE 0 END) AS gmv,
    COUNT(*) AS pay_cnt,
    COUNT(DISTINCT user_id) AS pay_user_cnt
FROM {{ ref('stg_payments') }}
GROUP BY 1
