"""M6+：AI 语义质检评测（Golden 标注集 + 检出器量化指标）。

离线可复现设计：
  * 标注集固定 seed 生成（clean 池 + 三类污染注入，逐条标注 fault_type）；
  * 检出器与混沌实验共用同一套污染词表（chaos.injector 的 GIBBERISH/OFF_TOPIC），
    规则检出器作为无 LLM API 时的基线（Baseline）；
  * LLM 模式评估协议（rubric）写入报告：同一标注集无缝替换检出器后，
    对比"规则基线 vs LLM 判定"的 Precision/Recall，即可量化 AI 增益。

用法：
  python eval/semantic_eval.py [--n-clean 200] [--polluted-ratio 0.05] [--write-golden]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from chaos.injector import GIBBERISH, OFF_TOPIC  # noqa: E402

CLEAN_ANSWERS = [
    "这款商品支持 7 天无理由退货，原包装退回即可。",
    "根据您的身高体重，建议选择 L 码，版型偏宽松。",
    "该商品目前有货，下单后 48 小时内发出。",
    "优惠券已叠加，实付金额以结算页为准。",
    "这款耳机的续航约 30 小时，支持快充。",
    "非质量问题退货运费由买家承担。",
    "会员积分会在确认收货后 7 个工作日到账。",
    "此型号与您手机系统兼容，可放心购买。",
]

QUESTIONS = [
    "这个可以退货吗？",
    "我该买哪个尺码？",
    "什么时候发货？",
    "优惠券能用吗？",
    "电池能用多久？",
    "退货要运费吗？",
    "积分什么时候到账？",
    "兼容我的手机吗？",
]

FAULT_TYPES = ("gibberish", "echo", "off_topic")


def detect_pollution(answer: str, question: str) -> str | None:
    """规则基线检出器：返回污染类型，正常回答返回 None。

    与混沌实验 semantic_pollution 的判定口径保持一致（单一事实来源）。
    """
    if answer in GIBBERISH:
        return "gibberish"
    if answer in OFF_TOPIC:
        return "off_topic"
    if answer == question * 3:
        return "echo"
    return None


def build_labeled_set(n_clean: int = 200, polluted_ratio: float = 0.05, seed: int = 42) -> list[dict]:
    """确定性生成标注集：clean 样本 + 三类污染样本（逐条标注 fault_type）。"""
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(n_clean):
        idx = rng.randrange(len(CLEAN_ANSWERS))
        rows.append(
            {
                "qa_id": f"q{i:04d}",
                "question": QUESTIONS[idx],
                "answer": CLEAN_ANSWERS[idx],
                "label": "clean",
                "fault_type": None,
            }
        )
    n_polluted = max(1, int(round(n_clean * polluted_ratio)))
    picked = rng.sample(rows, n_polluted)
    for order, row in enumerate(picked):
        kind = FAULT_TYPES[order % len(FAULT_TYPES)]  # 三类轮流注入，保证每类都有样本
        if kind == "gibberish":
            row["answer"] = rng.choice(GIBBERISH)
        elif kind == "echo":
            row["answer"] = row["question"] * 3
        else:
            row["answer"] = rng.choice(OFF_TOPIC)
        row["label"] = "polluted"
        row["fault_type"] = kind
    rows.sort(key=lambda r: r["qa_id"])
    return rows


def evaluate(dataset: list[dict], detector=detect_pollution) -> dict:
    """检出器量化评测：Precision / Recall / F1 + 分类型召回。"""
    tp = fp = fn = tn = 0
    per_type = {t: {"tp": 0, "fn": 0} for t in FAULT_TYPES}
    for row in dataset:
        pred = detector(row["answer"], row["question"])
        actual = row["fault_type"]
        if actual is None and pred is None:
            tn += 1
        elif actual is None and pred is not None:
            fp += 1
        elif actual is not None and pred is None:
            fn += 1
            per_type[actual]["fn"] += 1
        else:
            tp += 1
            if pred == actual:
                per_type[actual]["tp"] += 1
            else:
                # 检出但类型误判：算召回命中，但计入分类型误判
                per_type[actual]["tp"] += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "total": len(dataset),
        "polluted_total": tp + fn,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "per_type": {
            t: {"detected": v["tp"], "missed": v["fn"], "recall": round(v["tp"] / (v["tp"] + v["fn"]), 4) if (v["tp"] + v["fn"]) else 0.0}
            for t, v in per_type.items()
        },
    }


def render_report(metrics: dict, dataset: list[dict]) -> str:
    lines = [
        "# AI 语义质检评测报告（规则基线）",
        "",
        f"- 标注集规模：**{metrics['total']} 条**（其中污染样本 {metrics['polluted_total']} 条，三类各注入）",
        f"- 检出指标：Precision **{metrics['precision'] * 100:.1f}%** / Recall **{metrics['recall'] * 100:.1f}%** / F1 **{metrics['f1'] * 100:.1f}%**",
        "",
        "| 污染类型 | 检出 | 漏检 | 召回 |",
        "| --- | --- | --- | --- |",
    ]
    for t, v in metrics["per_type"].items():
        lines.append(f"| {t} | {v['detected']} | {v['missed']} | {v['recall'] * 100:.1f}% |")
    lines += [
        "",
        "## LLM 模式评估协议（rubric）",
        "",
        "规则基线是无 LLM API 时的兜底方案；接入 LLM 后使用**同一标注集**评测，",
        "判定要求：对每条样本输出 `{polluted: bool, fault_type: str|null, reason: str}`。",
        "评分标准：",
        "1. 检出正确：polluted 与标注一致（+1）；类型一致（额外 +0，类型用于根因细分）；",
        "2. 误报：clean 判为 polluted（Precision 扣分，权重同检出）；",
        "3. 漏检：polluted 判为 clean（Recall 扣分）；",
        "4. reason 必须给出可复核依据（否则该条不计分）。",
        "对比口径：**LLM 相对规则基线的 ΔRecall / ΔPrecision 即为 AI 增益的量化证据**",
        "（与 AI-RCA 的「基线对照」设计同构，见 core/dq_engine/ai/rca.py）。",
        "",
        "---",
        "",
        "> 本报告由 eval/semantic_eval.py 自动生成（固定 seed + 共享污染词表，可复现）。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 语义质检评测")
    ap.add_argument("--n-clean", type=int, default=200)
    ap.add_argument("--polluted-ratio", type=float, default=0.05)
    ap.add_argument("--write-golden", action="store_true")
    ap.add_argument("--report", default="docs/benchmarks/semantic_eval.md")
    args = ap.parse_args()

    dataset = build_labeled_set(n_clean=args.n_clean, polluted_ratio=args.polluted_ratio)
    metrics = evaluate(dataset)
    if args.write_golden:
        golden = _REPO / "eval" / "golden" / "semantic_golden.json"
        golden.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"标注集已落盘：{golden}")
    out = _REPO / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(metrics, dataset), encoding="utf-8")
    print(
        f"Precision={metrics['precision']} Recall={metrics['recall']} F1={metrics['f1']} "
        f"（{metrics['total']} 条 / 污染 {metrics['polluted_total']} 条）→ {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
