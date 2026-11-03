"""M6：脏数据故障注入器（Data Chaos Engineering，参考 Chaos Monkey 思想）。

8 类真实企业级故障（对应立项规格书 §四.2，第 9 类 data_skew 在 S2 支线实现——
它改变的是写入分布而非数据内容）：

  duplicate_pk       CDC 消息重复消费：复制 12% 订单记录 → 主键唯一性熔断
  orphan_fk          多源同步延迟：5% 支付流水外键指向不存在订单
  amount_truncate    金额精度截断：60% 行 float→int 强转 → 汇总偏差约 0.3%
  timestamp_mix      时间戳格式混写：30% 行改为 ISO-8601 带 Z → 解析失败/时区错位
  schema_drift       Schema 漂移：latency_ms 数值改字符串 + answer 核心键置空
  channel_drop       渠道断流：删除某渠道 60% 行（格式全部合法）→ 3σ 水位线
  psi_shift          客群分布偏移：30% 年轻用户 age 篡改到 55-65 → PSI 漂移
  semantic_pollution 语料污染：5% 回答掺乱码/复读机/答非所问
  data_skew         （第 9 类，S2 支线专用）热点 key 倾斜：改写渠道分布制造热点，
                    用于性能域调优实验，不参与 M6 拦截矩阵

设计原则：
  * 确定性：同一 seed 注入结果完全一致（评测可复现）
  * 不改动原始数据：始终写入副本目录，并在其中落 inject_report.json 留痕
  * 归因清晰：每次只注入一类故障（混沌实验方法论）

用法：
  python chaos/injector.py --fault duplicate_pk --in data/raw --out data/chaos
  python chaos/injector.py --fault all --in data/raw --out data/chaos --seed 42
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_RATIOS = {
    "duplicate_pk": 0.12,
    "orphan_fk": 0.05,
    "amount_truncate": 0.60,  # 60% 行截断 → 汇总偏差 ≈ 0.5 × 0.6 / 100 ≈ 0.3%
    "timestamp_mix": 0.30,
    "schema_drift": 0.30,
    "channel_drop": 0.60,
    "psi_shift": 1.0,  # 全部 18-34 岁用户漂移到 55-65（强漂移注入，PSI 应 > 0.25）
    "semantic_pollution": 0.05,
    # 第 9 类（S2 支线专用，不参与 M6 拦截矩阵）：改写分布制造热点 key，验证倾斜治理
    "data_skew": 0.80,
}

# 参与 M6 拦截矩阵评测的 8 类规范故障（data_skew 属于性能域，不在此列）
CANONICAL_FAULTS = [
    "duplicate_pk",
    "orphan_fk",
    "amount_truncate",
    "timestamp_mix",
    "schema_drift",
    "channel_drop",
    "psi_shift",
    "semantic_pollution",
]

GIBBERISH = ["�烫烫烫�", "锟斤拷锟斤拷", "▇▇▇▇ 系统错误", "&&&%%%###"]
OFF_TOPIC = ["今天天气不错。", "该问题与购物无关。", "请咨询人工客服获取帮助。", "我不知道你在说什么。"]


@dataclass
class InjectReport:
    fault: str
    ratio: float
    seed: int
    files_changed: list[str] = field(default_factory=list)
    rows_affected: int = 0
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _read(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------ 8 类故障


def _fault_duplicate_pk(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "fact_order.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writerows(sampled)
    return InjectReport("duplicate_pk", ratio, -1, ["fact_order.csv"], n, "追加重复行 → 主键唯一性规则应熔断")


def _fault_orphan_fk(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "fact_payment.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for i, row in enumerate(sampled):
        row["order_id"] = f"ORPHAN-{i:05d}"
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("orphan_fk", ratio, -1, ["fact_payment.csv"], n, "外键指向不存在的订单 → fk_integrity 规则应熔断")


def _fault_amount_truncate(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    """金额精度截断：仅对 amount 做 float→int 强转（coupon 不动）。

    检测语义：订单应付金额（amount - coupon_discount）与支付流水 pay_amount 对齐——
    只截断 amount 时，应付金额合计产生约 0.3% 偏差（0.5 平均小数 × 60% 行 / 均价 ~100）。
    """
    path = dst / "fact_order.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for row in sampled:
        row["amount"] = str(int(float(row["amount"])))
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("amount_truncate", ratio, -1, ["fact_order.csv"], n, "float→int 强转（仅 amount）→ 高精度 sum_align 规则应熔断")


def _fault_timestamp_mix(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "fact_order.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for row in sampled:
        row["order_time"] = row["order_time"].replace(" ", "T") + "Z"
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("timestamp_mix", ratio, -1, ["fact_order.csv"], n, "时间戳混写 ISO-8601 → 解析失败 → not_null/时效规则应告警")


def _fault_schema_drift(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "ai_qa_log.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for row in sampled:
        row["latency_ms"] = "N/A"
        row["answer"] = ""
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("schema_drift", ratio, -1, ["ai_qa_log.csv"], n, "数值改字符串 + 核心键置空 → 解析容错与元数据告警")


def _fault_channel_drop(rng: random.Random, dst: Path, ratio: float, channel: str) -> InjectReport:
    path = dst / "fact_order.csv"
    rows = _read(path)
    target = [r for r in rows if r["channel"] == channel]
    n = max(1, int(len(target) * ratio))
    drop = set(id(r) for r in rng.sample(target, n))
    kept = [r for r in rows if id(r) not in drop]
    _write(path, kept, list(rows[0].keys()))
    return InjectReport("channel_drop", ratio, -1, ["fact_order.csv"], n, f"删除渠道[{channel}] {n} 行（格式合法）→ 3σ 水位线应拦截隐性暴跌")


def _fault_psi_shift(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "dim_user.csv"
    rows = _read(path)
    young = [r for r in rows if r["age"] != "" and 18 <= int(r["age"]) <= 34]
    n = max(1, int(len(young) * ratio))
    sampled = rng.sample(young, n)
    for row in sampled:
        row["age"] = str(rng.randint(55, 65))
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("psi_shift", ratio, -1, ["dim_user.csv"], n, "年龄分布强漂移（年轻用户→高年龄段）→ PSI > 0.25 应告警")


def _fault_semantic_pollution(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    path = dst / "ai_qa_log.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for row in sampled:
        mode = rng.choice(["gibberish", "repeater", "offtopic"])
        if mode == "gibberish":
            row["answer"] = rng.choice(GIBBERISH)
        elif mode == "repeater":
            row["answer"] = row["question"] * 3
        else:
            row["answer"] = rng.choice(OFF_TOPIC)
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("semantic_pollution", ratio, -1, ["ai_qa_log.csv"], n, "掺入乱码/复读/答非所问 → 语义分层抽样质检应检出")


def _fault_data_skew(rng: random.Random, dst: Path, ratio: float) -> InjectReport:
    """S2 支线：热点 key 倾斜——把 ratio 比例的订单渠道改写到单一热点（抖音），
    模拟"80% 订单集中在单一渠道"的生产级数据倾斜场景（数据仍合法，用于性能实验）。"""
    path = dst / "fact_order.csv"
    rows = _read(path)
    n = max(1, int(len(rows) * ratio))
    sampled = rng.sample(rows, n)
    for row in sampled:
        row["channel"] = "抖音"
    _write(path, rows, list(rows[0].keys()))
    return InjectReport("data_skew", ratio, -1, ["fact_order.csv"], n, f"渠道热点改写：{ratio:.0%} 订单集中到抖音 → 倾斜治理实验")


FAULT_FUNCS = {
    "duplicate_pk": _fault_duplicate_pk,
    "orphan_fk": _fault_orphan_fk,
    "amount_truncate": _fault_amount_truncate,
    "timestamp_mix": _fault_timestamp_mix,
    "schema_drift": _fault_schema_drift,
    "channel_drop": _fault_channel_drop,
    "psi_shift": _fault_psi_shift,
    "semantic_pollution": _fault_semantic_pollution,
    "data_skew": _fault_data_skew,
}


def inject(
    fault: str,
    src_dir: str | Path,
    dst_dir: str | Path,
    ratio: float | None = None,
    seed: int = 42,
    channel: str = "抖音",
) -> InjectReport:
    """对 src_dir 的 CSV 副本注入指定故障，写入 dst_dir/<fault>/。"""
    if fault not in DEFAULT_RATIOS:
        raise ValueError(f"未知故障类型 {fault!r}，可选：{sorted(DEFAULT_RATIOS)}")

    src = Path(src_dir)
    dst = Path(dst_dir) / fault
    dst.mkdir(parents=True, exist_ok=True)
    for csv_file in src.glob("*.csv"):
        shutil.copy2(csv_file, dst / csv_file.name)

    rng = random.Random(f"{seed}:{fault}")
    r = FAULT_FUNCS[fault](rng, dst, DEFAULT_RATIOS[fault] if ratio is None else ratio, **(dict(channel=channel) if fault == "channel_drop" else {}))
    r.seed = seed
    (dst / "inject_report.json").write_text(json.dumps(r.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return r


def inject_all(src_dir: str | Path, dst_dir: str | Path, seed: int = 42) -> dict[str, InjectReport]:
    """依次注入 8 类规范故障（每类独立子目录），返回 {fault: report}。
    data_skew 属性能域实验（S2），单独用 inject('data_skew', ...) 触发。"""
    return {fault: inject(fault, src_dir, dst_dir, seed=seed) for fault in CANONICAL_FAULTS}


def main() -> int:
    ap = argparse.ArgumentParser(description="DataPulse 混沌故障注入器")
    ap.add_argument("--fault", choices=sorted(DEFAULT_RATIOS) + ["all"], required=True)
    ap.add_argument("--in", dest="src_dir", default="data/raw")
    ap.add_argument("--out", dest="dst_dir", default="data/chaos")
    ap.add_argument("--ratio", type=float, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--channel", default="抖音")
    args = ap.parse_args()

    if args.fault == "all":
        reports = inject_all(args.src_dir, args.dst_dir, seed=args.seed)
        for f, r in reports.items():
            print(f"  [{f:<18}] 影响 {r.rows_affected:>6} 行 → {r.details}")
        print(f"注入完成：8 类故障 → {args.dst_dir}/*/inject_report.json")
        return 0

    r = inject(args.fault, args.src_dir, args.dst_dir, ratio=args.ratio, seed=args.seed, channel=args.channel)
    print(f"[{r.fault}] 影响 {r.rows_affected} 行 → {r.details}")
    print(f"报告：{Path(args.dst_dir) / r.fault / 'inject_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
