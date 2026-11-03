# fact_order 交易订单表（Golden Dataset 用例 02）
CREATE TABLE fact_order (
    order_id        STRING    COMMENT '订单主键',
    user_id         STRING    COMMENT '用户外键',
    order_time      TIMESTAMP COMMENT '下单时间',
    channel         STRING    COMMENT '下单渠道',
    category        STRING    COMMENT '品类',
    amount          DECIMAL(12,2) COMMENT '订单金额',
    coupon_discount DECIMAL(12,2) COMMENT '优惠券抵扣',
    status          STRING    COMMENT '订单状态'
) COMMENT '交易订单表';
