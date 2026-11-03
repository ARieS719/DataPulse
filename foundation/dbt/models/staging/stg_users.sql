-- staging：用户贴源清洗（空值规整 + 类型转换，与 Spark ETL 的 DWD 逻辑对齐）
SELECT
    user_id,
    register_time::TIMESTAMP AS register_time,
    COALESCE(age, -1) AS age,
    COALESCE(gender, 'U') AS gender,
    COALESCE(city_tier, 0) AS city_tier,
    channel_source,
    is_vip
FROM read_csv_auto('{{ var("raw_path") }}/dim_user.csv', header = true)
