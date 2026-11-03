"""sqlglot 版本诊断脚本：打印三条典型查询的 AST 结构。

用途：定位血缘解析器与当前 sqlglot 版本的 AST 布局差异。
运行：python scripts/debug_sqlglot.py
把输出整段贴回即可。
"""

from __future__ import annotations

import sqlglot

QUERIES = [
    "SELECT user_id FROM t WHERE status = '已支付'",
    "SELECT o.order_id, p.pay_amount FROM fact_order o JOIN fact_payment p ON o.order_id = p.order_id",
    "SELECT user_id FROM fact_order o JOIN dim_user u ON o.user_id = u.user_id",
    "SELECT * FROM (SELECT user_id, age FROM dim_user) u",
]


def main() -> None:
    print("sqlglot version:", sqlglot.__version__)
    for sql in QUERIES:
        print("=" * 78)
        print("SQL:", sql)
        try:
            tree = sqlglot.parse_one(sql)
            print(tree.dump())
        except Exception as exc:  # noqa: BLE001
            print(f"PARSE ERROR: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
