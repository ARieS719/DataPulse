# dim_user 用户维度表（Golden Dataset 用例 01）
CREATE TABLE dim_user (
    user_id         STRING    COMMENT '用户主键',
    register_time   TIMESTAMP COMMENT '注册时间',
    age             INT       COMMENT '年龄（18-65，允许少量缺失）',
    gender          STRING    COMMENT '性别 M/F/U',
    city_tier       INT       COMMENT '城市等级 1-5',
    channel_source  STRING    COMMENT '注册渠道',
    is_vip          INT       COMMENT '是否会员 0/1'
) COMMENT '用户维度表';
