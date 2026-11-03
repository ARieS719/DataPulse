-- 口径变更只改一处：金额规整逻辑（NULL/非正 → 0）
{% macro normalize_amount(amount_col) %}
CASE WHEN {{ amount_col }} IS NULL OR {{ amount_col }} <= 0 THEN 0 ELSE {{ amount_col }} END
{% endmacro %}
