"""M6+：数据字典与敏感数据分级（数据治理侧核心交付）。

包含：
  * 数据字典契约（YAML）：表 → 字段 → 口径/类型/关联规则/敏感分级/脱敏方式/SLA；
  * 敏感数据分级（L1 公开 / L2 内部 / L3 敏感 / L4 机密）；
  * 与 M4 血缘联动：敏感字段所在表的下游影响面（blast radius）。

设计意图：质量规则解决"数据对不对"，数据字典解决"数据是什么、谁能用"，
两者合起来才是完整的治理闭环（对应 docs/production.md 的元数据中心规划）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

SENSITIVITY_LEVELS = ("L1", "L2", "L3", "L4")
SENSITIVITY_LABELS = {"L1": "公开", "L2": "内部", "L3": "敏感", "L4": "机密"}

CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"


class ColumnEntry(BaseModel):
    """字段级字典条目。"""

    dtype: str
    definition: str
    sensitivity: Literal["L1", "L2", "L3", "L4"]
    mask: str | None = None  # 脱敏方式：sha2 / mask / null（不需要）
    rules: list[str] = Field(default_factory=list)  # 关联的质量规则名


class TableEntry(BaseModel):
    """表级字典条目。"""

    sla_hours: float | None = None  # 质量 SLA：距上次 PASS/最新门禁的最长间隔（小时）
    owner: str | None = None
    columns: dict[str, ColumnEntry]


class DictionaryContract(BaseModel):
    """数据字典契约（与规则契约同构：YAML 声明、pydantic 校验）。"""

    version: int = Field(ge=1)
    tables: dict[str, TableEntry]

    @classmethod
    def load(cls, path: str | Path) -> DictionaryContract:
        import yaml

        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


class DataDictionary:
    """数据字典查询门面：字段口径、分级、SLA 与血缘影响面。"""

    def __init__(self, contract: DictionaryContract):
        self.contract = contract

    @classmethod
    def from_yaml(cls, path: str | Path) -> DataDictionary:
        return cls(DictionaryContract.load(path))

    def tables(self) -> list[str]:
        return sorted(self.contract.tables)

    def column(self, table: str, column: str) -> ColumnEntry | None:
        t = self.contract.tables.get(table)
        return t.columns.get(column) if t else None

    def sensitive_columns(self, min_level: str = "L3") -> list[dict]:
        """所有敏感级别 >= min_level 的字段（L3/L4 默认）。"""
        order = {lv: i for i, lv in enumerate(SENSITIVITY_LEVELS)}
        floor = order[min_level]
        out = []
        for table, t in self.contract.tables.items():
            for col, entry in t.columns.items():
                if order[entry.sensitivity] >= floor:
                    out.append(
                        {
                            "table": table,
                            "column": col,
                            "sensitivity": entry.sensitivity,
                            "label": SENSITIVITY_LABELS[entry.sensitivity],
                            "mask": entry.mask or "-",
                        }
                    )
        return out

    def masked_summary(self) -> dict:
        """脱敏落地情况：字典声明的脱敏字段 → 是否已由 DWD 层实施（sha2 匿名化等）。

        本仓库 DWD 层对 user_id 做 sha2 匿名化（user_key），其余字段如实标注。
        """
        masked = [c for c in self.sensitive_columns() if c["mask"] != "-"]
        return {
            "sensitive_total": len(self.sensitive_columns()),
            "masked_in_dict": len(masked),
            "implemented": {
                "ods.dim_user.user_id": "sha2 → dwd.*.user_key（etl_dwd.py）",
            },
        }

    def sla_map(self) -> dict:
        return {t: e.sla_hours for t, e in self.contract.tables.items() if e.sla_hours is not None}

    def downstream_impact(self, table: str, column: str, lineage_graph=None) -> dict:
        """敏感字段所在表的下游影响面：复用 M4 血缘 blast radius。"""
        entry = self.column(table, column)
        if entry is None:
            raise KeyError(f"字典中不存在 {table}.{column}")
        if lineage_graph is None:
            return {"table": table, "column": column, "sensitivity": entry.sensitivity, "downstream": None}
        report = lineage_graph.blast_radius(table)
        return {
            "table": table,
            "column": column,
            "sensitivity": entry.sensitivity,
            "label": SENSITIVITY_LABELS[entry.sensitivity],
            "mask": entry.mask or "-",
            "downstream_tables": report.downstream_tables,
            "max_depth": report.max_depth,
        }

    def flat_rows(self) -> list[dict]:
        """展平成"表-字段-口径-分级-脱敏-规则"行，供大盘表格渲染。"""
        rows = []
        for table, t in self.contract.tables.items():
            for col, entry in t.columns.items():
                rows.append(
                    {
                        "table": table,
                        "column": col,
                        "dtype": entry.dtype,
                        "definition": entry.definition,
                        "sensitivity": entry.sensitivity,
                        "label": SENSITIVITY_LABELS[entry.sensitivity],
                        "mask": entry.mask or "-",
                        "rules": ", ".join(entry.rules) or "-",
                        "sla_hours": t.sla_hours,
                    }
                )
        return rows
