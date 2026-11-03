"""M6：AI-RCA 根因诊断命中率评测。

场景构造：8 类混沌故障 × 变体（比例/目标表差异），Ground Truth 见
eval/golden/rca_golden.json（signals → 真实注入的故障类别）。

评测指标：
  * accuracy：故障分类准确率（本框架可离线评测）
  * 代码行定位命中率：需真实 ETL 环境 + Git 变更记录，联调阶段采集
    （framework 已预留字段，M6 完整评测报告补齐）
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from dq_engine.ai.rca import classify_by_signals


def load_rca_golden(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_rca(
    golden: list[dict],
    classify: Callable[[list[str]], str] = classify_by_signals,
) -> dict:
    """golden: [{scenario, signals, expected, fault_type}]"""
    correct = 0
    confusion: dict[tuple[str, str], int] = {}
    per_case = []
    for case in golden:
        pred = classify(case["signals"])
        ok = pred == case["expected"]
        correct += int(ok)
        confusion[(case["expected"], pred)] = confusion.get((case["expected"], pred), 0) + 1
        per_case.append({**case, "predicted": pred, "correct": ok})

    n = len(golden)
    return {
        "scenarios": n,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "correct": correct,
        "confusion": {f"{e} → {p}": c for (e, p), c in sorted(confusion.items())},
        "per_case": per_case,
    }
