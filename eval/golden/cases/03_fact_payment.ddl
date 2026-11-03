# fact_payment 支付流水表（Golden Dataset 用例 03）
CREATE TABLE fact_payment (
    payment_id  STRING    COMMENT '支付流水主键',
    order_id    STRING    COMMENT '订单外键',
    user_id     STRING    COMMENT '用户外键',
    pay_time    TIMESTAMP COMMENT '支付时间',
    pay_channel STRING    COMMENT '支付渠道',
    pay_amount  DECIMAL(12,2) COMMENT '支付金额',
    currency    STRING    COMMENT '币种',
    pay_status  STRING    COMMENT '支付状态'
) COMMENT '支付流水表';
