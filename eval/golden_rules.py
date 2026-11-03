"""M6：规则生成准确率评测（Golden Dataset）。

对比 AI 生成的 YAML 规则与资深工程师手写标准答案：
  * syntax_valid_rate：pydantic 契约校验通过率（语法合法率）
  * coverage：按 (规则类型, 目标列) 归一化后的期望规则命中率（核心规则覆盖率）
  * per_case：每例的缺失规则清单（用于改进提示词的归因分析）

Golden Dataset 位于 eval/golden/cases/（DDL + 手写标准答案 YAML）。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from dq_engine.rules.schema import Ruleset


def _rule_keys(ruleset: Ruleset) -> set[tuple[str, str | None]]:
    keys = set()
    for r in ruleset.rules:
        col = (
            getattr(r, "column", None)
            or getattr(r, "fk_column", None)
            or (getattr(r, "agg_expr", None) or getattr(r, "agg_column", None))
        )
        keys.add((str(r.type), col))
    return keys


def _load_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _parse(thing: str) -> dict:
    """接受 YAML 文本或文件路径。"""
    p = Path(thing)
    return _load_yaml(p) if p.is_file() else yaml.safe_load(thing)


def evaluate_rule_generation(cases: list[dict]) -> dict:
    """cases: [{name, ddl, expected_yaml, generated_yaml}] → 汇总指标。"""
    per_case = []
    syntax_ok = 0
    for c in cases:
        try:
            gen_rs = Ruleset.model_validate(_parse(c["generated_yaml"]))
        except Exception as exc:  # 语法非法：直接记为 0 覆盖
            per_case.append({"name": c["name"], "syntax_valid": False, "coverage": 0.0, "error": str(exc)})
            continue
        syntax_ok += 1
        exp_rs = Ruleset.model_validate(_parse(c["expected_yaml"]))
        exp_keys = _rule_keys(exp_rs)
        gen_keys = _rule_keys(gen_rs)
        hit = len(exp_keys & gen_keys)
        coverage = hit / len(exp_keys) if exp_keys else 1.0
        per_case.append(
            {
                "name": c["name"],
                "syntax_valid": True,
                "coverage": round(coverage, 4),
                "hit": hit,
                "expected": len(exp_keys),
                "missing": sorted(str(k) for k in (exp_keys - gen_keys)),
            }
        )

    n = len(cases)
    return {
        "cases": n,
        "syntax_valid_rate": round(syntax_ok / n, 4) if n else 0.0,
        "avg_coverage": round(sum(c["coverage"] for c in per_case) / n, 4) if n else 0.0,
        "per_case": per_case,
    }


def load_golden_cases(cases_dir: str | Path) -> list[dict]:
    """从 eval/golden/cases/ 加载全部评测用例（*.ddl + 同名 *.expected.yaml）。"""
    root = Path(cases_dir)
    cases = []
    for ddl_path in sorted(root.glob("*.ddl")):
        stem = ddl_path.stem
        expected = root / f"{stem}.expected.yaml"
        if not expected.exists():
            continue
        cases.append(
            {
                "name": stem,
                "ddl": ddl_path.read_text(encoding="utf-8"),
                "expected_yaml": expected.read_text(encoding="utf-8"),
            }
        )
    return cases


def evaluate_against_golden(cases_dir: str | Path, generated_map: dict[str, str]) -> dict:
    """generated_map: {用例名: AI 生成的 YAML 文本}。"""
    cases = load_golden_cases(cases_dir)
    for c in cases:
        c["generated_yaml"] = generated_map.get(c["name"], "")
    return evaluate_rule_generation(cases)
