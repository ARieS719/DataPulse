-- 缓慢变化维 SCD2：用户维属性变更留痕（check 策略，变更列：age / city_tier / is_vip）
{% snapshot user_scd2 %}

{{
    config(
        target_schema = 'snapshots',
        unique_key = 'user_id',
        strategy = 'check',
        check_cols = ['age', 'city_tier', 'is_vip'],
    )
}}

SELECT user_id, register_time, age, gender, city_tier, channel_source, is_vip
FROM {{ ref('stg_users') }}

{% endsnapshot %}
