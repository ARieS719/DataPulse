"""YAML 规则契约的 pydantic Schema（M2 里程碑核心）。

规则文件即"声明式数据契约"：每条规则都是强类型对象，加载时完成语法与参数合法性校验，
非法规则在进入编译器之前就被拒绝（fail-fast，对应立项规格书 §三.2 的事前建规）。

支持 8 类规则：
  not_null          字段非空率下限
  unique            主键/字段唯一性（重复行数 = 0）
  accepted_values   枚举白名单覆盖率下限（NULL 计入不匹配）
  range             数值范围违规比例上限（NULL 不违规，由 not_null 单独管辖）
  row_count         行数上下限（至少指定其一）
  timeliness        最新时间戳不早于某时刻
  fk_integrity      外键孤儿比例上限（跨表，NULL 外键计入孤儿）
  sum_align         跨表聚合金额对齐（对称差 |a-b|/(|a|+|b|) 比例上限）
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

P0: Literal["P0"] = "P0"
P1: Literal["P1"] = "P1"


def _check_ident(v: str) -> str:
    if not IDENT_RE.match(v):
        raise ValueError(f"非法标识符（仅允许字母/数字/下划线，且以字母或下划线开头）: {v!r}")
    return v


def _check_datetime(v: str) -> str:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            datetime.strptime(v, fmt)
            return v
        except ValueError:
            continue
    raise ValueError(f"时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS: {v!r}")


class _RuleBase(BaseModel):
    name: str
    level: Literal["P0", "P1"]
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_ident(cls, v: str) -> str:
        return _check_ident(v)


class NotNullRule(_RuleBase):
    type: Literal["not_null"]
    column: str
    min_fill_ratio: float = Field(default=0.99, ge=0.0, le=1.0)

    @field_validator("column")
    @classmethod
    def _col(cls, v: str) -> str:
        return _check_ident(v)


class UniqueRule(_RuleBase):
    type: Literal["unique"]
    column: str

    @field_validator("column")
    @classmethod
    def _col(cls, v: str) -> str:
        return _check_ident(v)


class AcceptedValuesRule(_RuleBase):
    type: Literal["accepted_values"]
    column: str
    values: list[str | int]
    min_match_ratio: float = Field(default=0.999, ge=0.0, le=1.0)

    @field_validator("column")
    @classmethod
    def _col(cls, v: str) -> str:
        return _check_ident(v)

    @field_validator("values")
    @classmethod
    def _vals(cls, v):
        if len(v) < 1:
            raise ValueError("values 至少需要一个枚举值")
        return v


class RangeRule(_RuleBase):
    type: Literal["range"]
    column: str
    min: float | None = None
    max: float | None = None
    max_violation_ratio: float = Field(default=0.001, ge=0.0, le=1.0)

    @field_validator("column")
    @classmethod
    def _col(cls, v: str) -> str:
        return _check_ident(v)

    @model_validator(mode="after")
    def _min_max(self):
        if self.min is None and self.max is None:
            raise ValueError("range 规则必须至少指定 min 或 max 之一")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min({self.min}) 不能大于 max({self.max})")
        return self


class RowCountRule(_RuleBase):
    type: Literal["row_count"]
    min_rows: int | None = Field(default=None, ge=0)
    max_rows: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds(self):
        if self.min_rows is None and self.max_rows is None:
            raise ValueError("row_count 规则必须至少指定 min_rows 或 max_rows 之一")
        if self.min_rows is not None and self.max_rows is not None and self.min_rows > self.max_rows:
            raise ValueError("min_rows 不能大于 max_rows")
        return self


class TimelinessRule(_RuleBase):
    type: Literal["timeliness"]
    column: str
    expect_not_before: str

    @field_validator("column")
    @classmethod
    def _col(cls, v: str) -> str:
        return _check_ident(v)

    @field_validator("expect_not_before")
    @classmethod
    def _time(cls, v: str) -> str:
        return _check_datetime(v)


class FkIntegrityRule(_RuleBase):
    type: Literal["fk_integrity"]
    fk_column: str
    ref_table: str
    ref_column: str
    max_orphan_ratio: float = Field(default=0.001, ge=0.0, le=1.0)

    @field_validator("fk_column", "ref_table", "ref_column")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_ident(v)


_ARITH_EXPR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\s*[+\-*/]\s*[A-Za-z_][A-Za-z0-9_]*|\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\))*$")
_FILTER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*(=|!=|<>)\s*'[^']*'$")


class SumAlignRule(_RuleBase):
    type: Literal["sum_align"]
    agg_column: str | None = None  # 简单列求和；与 agg_expr 二选一
    agg_expr: str | None = None  # 算术表达式（如 "amount - coupon_discount"，仅允许列名与 +-*/ 括号）
    agg_filter: str | None = None  # 主表过滤（如 "status = '已支付'"，仅允许 列 = '字面量' 形式）
    ref_table: str
    ref_agg_column: str
    max_diff_ratio: float = Field(default=0.01, ge=0.0, le=1.0)

    @field_validator("agg_column", "ref_table", "ref_agg_column")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_ident(v)

    @field_validator("agg_expr")
    @classmethod
    def _expr(cls, v):
        if not _ARITH_EXPR_RE.match(v):
            raise ValueError(f"agg_expr 仅允许列名与 + - * / 及括号组成的算术表达式: {v!r}")
        return v

    @field_validator("agg_filter")
    @classmethod
    def _filter(cls, v):
        if not _FILTER_RE.match(v):
            raise ValueError(f"agg_filter 仅允许 列 = '字面量' 形式（支持 = != <>）: {v!r}")
        return v

    @model_validator(mode="after")
    def _agg(self):
        if (self.agg_column is None) == (self.agg_expr is None):
            raise ValueError("sum_align 规则必须且只能指定 agg_column 或 agg_expr 之一")
        return self


Rule = Annotated[
    NotNullRule | UniqueRule | AcceptedValuesRule | RangeRule | RowCountRule | TimelinessRule | FkIntegrityRule | SumAlignRule,
    Field(discriminator="type"),
]


class Ruleset(BaseModel):
    version: int = 1
    table: str
    rules: list[Rule] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _ver(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"暂仅支持 version=1，收到 {v}")
        return v

    @field_validator("table")
    @classmethod
    def _table_ident(cls, v: str) -> str:
        return _check_ident(v)

    @model_validator(mode="after")
    def _rule_names_unique(self):
        names = [r.name for r in self.rules]
        if len(names) != len(set(names)):
            raise ValueError(f"规则名必须唯一，出现重复: {sorted({n for n in names if names.count(n) > 1})}")
        return self
