"""M6：Markdown 评测报告生成（量化指标留痕的最终交付物）。

渲染三部分指标：
  1. AI 规则生成评测（语法合法率 / 核心规则覆盖率）
  2. AI-RCA 根因诊断评测（故障分类准确率）
  3. 混沌拦截评测（P0 拦截率 / 召回率 / 误报率，联调实验后填充）

用法：
  from eval.report import write_eval_report
  write_eval_report(metrics, "docs/eval_report.md")
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def render_eval_report(metrics: dict) -> str:
    lines = ["# DataPulse 评测报告", "", f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}", ""]

    rg = metrics.get("rule_generation")
    if rg:
        lines += [
            "## 1. AI 规则生成评测（Golden Dataset）",
            "",
            f"- 语法合法率：**{rg['syntax_valid_rate'] * 100:.1f}%**",
            f"- 核心规则覆盖率：**{rg['avg_coverage'] * 100:.1f}%**",
            "",
            "| 用例 | 语法合法 | 覆盖率 | 命中/期望 | 缺失规则 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for c in rg["per_case"]:
            lines.append(
                f"| {c['name']} | {'✅' if c['syntax_valid'] else '❌'} "
                f"| {c['coverage'] * 100:.1f}% "
                f"| {c.get('hit', 0)}/{c.get('expected', '-')} "
                f"| {', '.join(c.get('missing', [])) or '-'} |"
            )
        lines.append("")

    rca = metrics.get("rca")
    if rca:
        lines += [
            "## 2. AI-RCA 根因诊断评测",
            "",
            f"- 场景数：{rca['scenarios']}",
            f"- 故障分类准确率：**{rca['accuracy'] * 100:.1f}%**",
            "",
            "| 场景 | 注入故障 | 预测类别 | 结果 |",
            "| --- | --- | --- | --- |",
        ]
        for c in rca["per_case"]:
            lines.append(
                f"| {c['scenario']} | {c['fault_type']} | {c['predicted']} | {'✅' if c['correct'] else '❌'} |"
            )
        lines.append("")

    chaos = metrics.get("chaos")
    if chaos:
        lines += [
            "## 3. 混沌拦截评测",
            "",
            f"- P0 拦截率：**{chaos.get('p0_block_rate', 0) * 100:.1f}%**",
            f"- 动态异常召回率：**{chaos.get('recall', 0) * 100:.1f}%**",
            f"- 误报率：**{chaos.get('false_positive_rate', 0) * 100:.1f}%**",
            "",
            "> 本节指标由混沌联调实验脚本生成（8 类故障 × 分级规则矩阵）。",
            "",
        ]

    lines += ["---", "", "> 本报告由 eval/report.py 自动生成，量化数字全部可复现（脚本 + 数据 + seed 留痕）。"]
    return "\n".join(lines)


def write_eval_report(metrics: dict, path: str | Path = "docs/eval_report.md") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_eval_report(metrics), encoding="utf-8")
