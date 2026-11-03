"""M2/M3：AI 规则生成助手（Rule Copilot）—— 元数据探查 + 上下文组装 + 结构化提示词。

纯 Python 部分（本模块，零 I/O 可测）：
  parse_ddl            DDL → 表/列/类型/注释（覆盖本仓库 Golden 用例格式）
  profile_csv          CSV → 列统计（非空率/枚举 Top 值/数值范围）+ 样本行
  build_copilot_prompt 结构化提示词组装（DDL + 样本 + 统计 + RAG 业务口径）
  validate_generated   pydantic 契约校验生成结果（语法合法率评测的入口）

LLM API 调用由执行器注入（可替换 DeepSeek/Qwen），对应立项规格书 §三.2。
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dq_engine.rules.schema import Ruleset

_COLUMN_RE = re.compile(
    r"^\s*(?P<name>\w+)\s+(?P<type>[A-Za-z0-9_<>(), ]+?)\s*(?:COMMENT\s+'(?P<comment>[^']*)')?\s*,?\s*$"
)
_CREATE_RE = re.compile(r"CREATE\s+TABLE\s+`?(\w+)`?\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    type: str
    comment: str = ""


@dataclass
class TableProfile:
    table: str
    columns: list[ColumnMeta] = field(default_factory=list)
    sample_rows: list[dict] = field(default_factory=list)
    column_stats: dict = field(default_factory=dict)
    ddl: str = ""


def parse_ddl(ddl: str) -> TableProfile:
    """解析 CREATE TABLE DDL（支持行内/行尾 COMMENT 与反引号）。"""
    m = _CREATE_RE.search(ddl)
    if not m:
        raise ValueError("无法解析 DDL：未找到 CREATE TABLE")
    table = m.group(1)
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    columns: list[ColumnMeta] = []
    for line in body.splitlines():
        cm = _COLUMN_RE.match(line)
        if cm:
            columns.append(ColumnMeta(cm.group("name"), cm.group("type").strip(), (cm.group("comment") or "").strip()))
    if not columns:
        raise ValueError("DDL 中未解析出任何列")
    return TableProfile(table=table, columns=columns, ddl=ddl)


def _safe_float(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def profile_csv(csv_path: str | Path, sample_size: int = 10, stats_rows: int = 1000) -> TableProfile:
    """CSV → 元数据探查：列统计（非空率/枚举 Top 值/数值范围）+ 样本行。"""
    path = Path(csv_path)
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        all_rows = list(reader)

    stats: dict[str, dict] = {}
    for col in header:
        values = [r[col] for r in all_rows[:stats_rows]]
        non_null = [v for v in values if v not in ("", None)]
        numeric = [_safe_float(v) for v in non_null]
        nums = [x for x in numeric if x is not None]
        stats[col] = {
            "null_rate": round(1 - len(non_null) / len(values), 4) if values else 0.0,
            "distinct": len(set(non_null)),
            "top_values": [{"value": v, "count": c} for v, c in Counter(non_null).most_common(3)],
            "numeric_min": min(nums) if nums else None,
            "numeric_max": max(nums) if nums else None,
        }
    return TableProfile(
        table=path.stem,
        columns=[ColumnMeta(name=c, type="") for c in header],
        sample_rows=all_rows[:sample_size],
        column_stats=stats,
    )


def build_copilot_prompt(profile: TableProfile, rag_docs: list[str] | None = None) -> str:
    """组装结构化提示词：DDL + 列统计 + 样本 + RAG 检索到的业务口径文档。"""
    lines = [
        "你是资深数据质量工程师。请为目标表生成数据质量校验规则（YAML 契约）。",
        "",
        "## 规则 Schema（必须严格遵守）",
        "- version: 1；table: 目标表名",
        "- 每条规则字段：name（唯一）、type、level（P0 阻断级 / P1 告警级）、description（必填）及类型相关参数",
        "- type 可选：not_null, unique, accepted_values, range, row_count, timeliness, fk_integrity, sum_align",
        "",
        "## 生成要求",
        "1. 主键列必须生成 unique 规则（P0）",
        "2. 外键列生成 fk_integrity（P0，指向注释中说明的引用表）",
        "3. 枚举列生成 accepted_values（P0 核心状态 / P1 一般枚举）",
        "4. 金额/数值列生成 range（P1）与必要的跨表 sum_align（P0）",
        "5. 仅输出 YAML 文本，禁止输出任何解释性文字或代码块标记",
        "",
        f"## 目标表 DDL\n```sql\n{profile.ddl or '（无 DDL，以列清单为准）'}\n```",
        "## 列清单\n" + "\n".join(f"- {c.name} {c.type}" + (f"  # {c.comment}" if c.comment else "") for c in profile.columns),
        "## 列统计（非空率 / Top 枚举值 / 数值范围）\n" + yaml.safe_dump(profile.column_stats, allow_unicode=True, sort_keys=False),
        "## 样本数据（前 10 行）\n" + yaml.safe_dump(profile.sample_rows, allow_unicode=True, sort_keys=False),
    ]
    if rag_docs:
        lines += ["## 业务口径约束（RAG 检索）"] + [f"- {d}" for d in rag_docs]
    return "\n".join(lines)


def validate_generated(generated_yaml: str) -> Ruleset:
    """校验 AI 生成的 YAML 契约（语法/参数非法时抛 pydantic ValidationError）。"""
    return Ruleset.model_validate(yaml.safe_load(generated_yaml))
