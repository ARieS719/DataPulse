-- marts：用户维表（代理键 user_key + 当前态；历史版本由 snapshots/user_scd2.sql 维护）
SELECT
    row_number() OVER (ORDER BY user_id) AS user_key,  -- 代理键（与 DWD 层 sha2 匿名化口径不同，此处演示建模惯例）
    user_id,
    register_time,
    age,
    gender,
    city_tier,
    channel_source,
    is_vip
FROM {{ ref('stg_users') }}
