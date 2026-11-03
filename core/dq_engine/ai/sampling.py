"""M3：AI 语义分层抽样（纯 Python 抽样逻辑，LLM 评估调用由执行器注入）。

按 2% 抽样率对 AI 导购问答日志做分层抽样：以 (intent, model_version) 为层，
层内随机抽取且每层至少 1 条——保证小流量层同样有代表性，
避免"全量随机抽样吃不到长尾层"导致的语义质检盲区（对应立项规格书 §三.3.4）。

确定性：同一 seed 抽样结果完全一致（Golden Dataset 评测可复现）。
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence


def stratified_sample(
    rows: Iterable[dict],
    key_fields: Sequence[str],
    rate: float = 0.02,
    min_per_stratum: int = 1,
    seed: int = 42,
    id_field: str = "qa_id",
) -> list[dict]:
    """分层抽样。

    rows：可迭代的行字典，必须包含 key_fields 与 id_field 键。
    rate：抽样率（0.0~1.0）；min_per_stratum：每层保底条数。
    返回抽样出的行列表（保持输入顺序之外的稳定性由 seed 保证）。
    """
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"rate 必须在 [0,1]，收到 {rate}")
    if rate <= 0.0:
        return []

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row[f] for f in key_fields)
        groups.setdefault(key, []).append(row)

    rng = random.Random(seed)
    picked: list[dict] = []
    for key in sorted(groups, key=str):
        group = groups[key]
        n = max(min_per_stratum, int(round(len(group) * rate)))
        n = min(n, len(group))
        picked.extend(rng.sample(group, n))

    # 以 id_field 排序保证输出确定性（不依赖字典遍历顺序）
    picked.sort(key=lambda r: r[id_field])
    return picked


def sample_stats(rows: Sequence[dict], sampled: Sequence[dict], key_fields: Sequence[str]) -> dict:
    """抽样覆盖统计：总体/样本量、抽样率、每层覆盖情况（用于评测报告）。"""
    total_by_key: dict[tuple, int] = {}
    for row in rows:
        key = tuple(row[f] for f in key_fields)
        total_by_key[key] = total_by_key.get(key, 0) + 1
    sampled_by_key: dict[tuple, int] = {}
    for row in sampled:
        key = tuple(row[f] for f in key_fields)
        sampled_by_key[key] = sampled_by_key.get(key, 0) + 1
    return {
        "total": len(rows),
        "sampled": len(sampled),
        "rate": round(len(sampled) / len(rows), 4) if rows else 0.0,
        "strata_total": len(total_by_key),
        "strata_covered": len(sampled_by_key),
        "per_stratum": {
            str(k): {"total": total_by_key[k], "sampled": sampled_by_key.get(k, 0)} for k in total_by_key
        },
    }
