"""DataPulse 源数据生成器 —— 零第三方依赖，仅用 Python 标准库。

产出四张源表（CSV + manifest.json）：
  dim_user      用户维度表
  fact_order    交易订单表
  fact_payment  支付流水表
  ai_qa_log     AI 导购问答日志表

设计原则：
  * 完全确定性：同一 --seed 每次生成完全一致的数据（评测可复现）。
  * 生成"干净但真实"的数据：含业务上合理的空值与小概率边缘情况；
    脏数据（重复主键/孤儿外键/金额截断/乱码等）一律由 chaos/ 注入器负责，
    保证故障注入实验的归因清晰（混沌工程的方法论要求）。

用法：
  python foundation/data_gen/generate.py --scale small
  python foundation/data_gen/generate.py --scale medium --seed 7 --out data/raw
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 规模档位：medium 的 fact_order = 100 万行，是量化指标（<3 秒扫描）的基准档
SCALES = {
    "small": {"users": 1_000, "orders": 10_000, "payments": 7_800, "qa": 5_000},
    "medium": {"users": 100_000, "orders": 1_000_000, "payments": 780_000, "qa": 200_000},
    "large": {"users": 500_000, "orders": 5_000_000, "payments": 3_900_000, "qa": 1_000_000},
}

CHANNELS = ["抖音", "快手", "小红书", "微信小程序", "淘宝", "京东", "官网"]
CHANNEL_W = [24, 12, 16, 14, 18, 11, 5]
CATEGORIES = ["服饰", "数码", "美妆", "食品", "家居", "母婴", "图书"]
CATEGORY_W = [22, 18, 15, 20, 12, 7, 6]
CITY_TIERS = [1, 2, 3, 4, 5]
CITY_W = [12, 28, 30, 20, 10]
ORDER_STATUS = ["已支付", "待支付", "已取消", "已退款"]
ORDER_STATUS_W = [78, 10, 8, 4]
PAY_CHANNELS = ["支付宝", "微信支付", "银联", "花呗", "余额"]
PAY_W = [40, 35, 10, 9, 6]
PAY_STATUS = ["成功", "失败"]
PAY_STATUS_W = [97, 3]
QA_INTENTS = ["商品咨询", "价格咨询", "物流查询", "售后处理", "闲聊"]
QA_QUESTIONS = {
    "商品咨询": ["这件衣服有 XL 码吗？", "这个手机是什么屏幕？", "这款面霜适合油皮吗？"],
    "价格咨询": ["现在有优惠吗？", "满减活动到什么时候？", "会员价还能叠加优惠券吗？"],
    "物流查询": ["我的订单什么时候发货？", "可以指定顺丰吗？", "偏远地区包邮吗？"],
    "售后处理": ["尺码不合适怎么退？", "退货邮费谁承担？", "可以七天无理由吗？"],
    "闲聊": ["你好呀", "在吗？", "谢谢啦"],
}
QA_ANSWERS = {
    "商品咨询": ["亲，这款有 XL 码，库存充足哦。", "您好，该机型采用 6.7 英寸 OLED 屏。", "这款面霜清爽不闷痘，油皮可用。"],
    "价格咨询": ["目前有满 300 减 40 的活动。", "满减活动持续到本月底。", "会员价可与优惠券叠加使用。"],
    "物流查询": ["订单会在 48 小时内发出。", "可以备注指定顺丰，需补差价。", "偏远地区满 99 元包邮。"],
    "售后处理": ["支持 7 天无理由退货，原包装退回即可。", "非质量问题退货运费由买家承担。", "本店支持七天无理由退换。"],
    "闲聊": ["您好，请问有什么可以帮您？", "在的，亲，请讲。", "不客气，祝您购物愉快！"],
}
MODEL_VERSIONS = ["v1.0", "v1.1"]

# 周内日模式权重（周一..周日，周末峰值）：让逐日订单量更贴近真实业务，
# 使 3σ 水位线的"渠道断流"检测在合理噪声水平下可工作（见 run_chaos_experiment 的 volume 通道）
WEEKDAY_WEIGHTS = [0.85, 0.90, 0.95, 1.00, 1.05, 1.20, 1.15]


def _rng(seed: int, salt: str) -> random.Random:
    return random.Random(f"{seed}:{salt}")


def _day_weights(base: datetime, span_days: int) -> list[float]:
    return [WEEKDAY_WEIGHTS[(base.weekday() + d) % 7] for d in range(span_days)]


def _ts(base: datetime, rng: random.Random, day_weights: list[float]) -> str:
    day = rng.choices(range(len(day_weights)), weights=day_weights, k=1)[0]
    delta = timedelta(days=day, seconds=rng.randrange(86400))
    return (base + delta).strftime("%Y-%m-%d %H:%M:%S")


def _weighted(rng: random.Random, values, weights):
    return rng.choices(values, weights=weights, k=1)[0]


def gen_users(rng: random.Random, n: int, base: datetime, out: Path) -> int:
    """用户维度表。"""
    path = out / "dim_user.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "register_time", "age", "gender", "city_tier", "channel_source", "is_vip"])
        for i in range(1, n + 1):
            reg = base - timedelta(days=rng.randrange(730), seconds=rng.randrange(86400))
            # 年龄段加权（18-24:15 / 25-34:35 / 35-44:25 / 45-54:15 / 55-65:10），2% 为空值（合理边缘情况）
            band = _weighted(rng, [0, 1, 2, 3, 4], [15, 35, 25, 15, 10])
            age = "" if rng.random() < 0.02 else rng.randint([18, 25, 35, 45, 55][band], [24, 34, 44, 54, 65][band])
            gender = _weighted(rng, ["M", "F", "U"], [48, 48, 4])
            tier = "" if rng.random() < 0.01 else _weighted(rng, CITY_TIERS, CITY_W)
            channel = _weighted(rng, CHANNELS, CHANNEL_W)
            is_vip = 1 if rng.random() < 0.15 else 0
            w.writerow([f"U{i:07d}", reg.strftime("%Y-%m-%d %H:%M:%S"), age, gender, tier, channel, is_vip])
    return n


def gen_orders(rng: random.Random, n_users: int, n: int, base: datetime, span_days: int, out: Path) -> int:
    """交易订单表。"""
    path = out / "fact_order.csv"
    day_weights = _day_weights(base, span_days)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["order_id", "user_id", "order_time", "channel", "category", "amount", "coupon_discount", "status"])
        for i in range(1, n + 1):
            amount = round(rng.uniform(9.9, 199.9), 2) if rng.random() < 0.7 else round(rng.uniform(200.0, 4999.9), 2)
            coupon = round(amount * rng.uniform(0.05, 0.2), 2) if rng.random() < 0.4 else 0.0
            w.writerow([
                f"O{i:011d}",
                f"U{rng.randint(1, n_users):07d}",
                _ts(base, rng, day_weights),
                _weighted(rng, CHANNELS, CHANNEL_W),
                _weighted(rng, CATEGORIES, CATEGORY_W),
                f"{amount:.2f}",
                f"{coupon:.2f}",
                _weighted(rng, ORDER_STATUS, ORDER_STATUS_W),
            ])
    return n


def gen_payments(rng: random.Random, orders_csv: Path, out: Path) -> int:
    """支付流水表：仅针对状态为'已支付'的订单生成一条流水（干净数据，孤儿流水由混沌注入器制造）。"""
    path = out / "fact_payment.csv"
    count = 0
    with orders_csv.open("r", encoding="utf-8") as fin, path.open("w", newline="", encoding="utf-8") as fout:
        reader = csv.DictReader(fin)
        w = csv.writer(fout)
        w.writerow(["payment_id", "order_id", "user_id", "pay_time", "pay_channel", "pay_amount", "currency", "pay_status"])
        for row in reader:
            if row["status"] != "已支付":
                continue
            count += 1
            pay_time = datetime.strptime(row["order_time"], "%Y-%m-%d %H:%M:%S") + timedelta(seconds=rng.randint(1, 600))
            pay_amount = round(float(row["amount"]) - float(row["coupon_discount"]), 2)
            w.writerow([
                f"P{count:011d}",
                row["order_id"],
                row["user_id"],
                pay_time.strftime("%Y-%m-%d %H:%M:%S"),
                _weighted(rng, PAY_CHANNELS, PAY_W),
                f"{pay_amount:.2f}",
                "CNY",
                _weighted(rng, PAY_STATUS, PAY_STATUS_W),
            ])
    return count


def gen_qa(rng: random.Random, n_users: int, n: int, base: datetime, span_days: int, out: Path) -> int:
    """AI 导购问答日志表。"""
    path = out / "ai_qa_log.csv"
    day_weights = _day_weights(base, span_days)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["qa_id", "user_id", "session_time", "intent", "question", "answer", "model_version", "latency_ms", "session_len"])
        for i in range(1, n + 1):
            intent = _weighted(rng, QA_INTENTS, [35, 20, 15, 20, 10])
            w.writerow([
                f"Q{i:011d}",
                f"U{rng.randint(1, n_users):07d}",
                _ts(base, rng, day_weights),
                intent,
                rng.choice(QA_QUESTIONS[intent]),
                rng.choice(QA_ANSWERS[intent]),
                _weighted(rng, MODEL_VERSIONS, [70, 30]),
                rng.randint(180, 2500),
                _weighted(rng, [1, 2, 3, 4, 5, 6, 7, 8], [30, 22, 15, 12, 8, 6, 4, 3]),
            ])
    return n


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="DataPulse 源数据生成器")
    ap.add_argument("--scale", choices=sorted(SCALES), default="small")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--start", default="2025-01-01", help="数据起始日期 YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=90, help="数据时间跨度（天）")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = datetime.strptime(args.start, "%Y-%m-%d")
    cfg = SCALES[args.scale]

    t0 = time.time()
    counts = {}
    counts["dim_user"] = gen_users(_rng(args.seed, "users"), cfg["users"], base, out)
    counts["fact_order"] = gen_orders(_rng(args.seed, "orders"), cfg["users"], cfg["orders"], base, args.days, out)
    counts["fact_payment"] = gen_payments(_rng(args.seed, "payments"), out / "fact_order.csv", out)
    counts["ai_qa_log"] = gen_qa(_rng(args.seed, "qa"), cfg["users"], cfg["qa"], base, args.days, out)

    files = {}
    for name in counts:
        p = out / f"{name}.csv"
        files[f"{name}.csv"] = {"rows": counts[name], "bytes": p.stat().st_size, "sha256": sha256_file(p)}

    manifest = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scale": args.scale,
        "seed": args.seed,
        "start": args.start,
        "days": args.days,
        "rows": counts,
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"生成完成（scale={args.scale}, seed={args.seed}, 耗时 {time.time() - t0:.1f}s）→ {out}")
    for name, n in counts.items():
        print(f"  {name:<14} {n:>10,} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
