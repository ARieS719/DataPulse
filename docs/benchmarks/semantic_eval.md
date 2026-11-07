# AI 语义质检评测报告（规则基线）

- 标注集规模：**200 条**（其中污染样本 10 条，三类各注入）
- 检出指标：Precision **100.0%** / Recall **100.0%** / F1 **100.0%**

| 污染类型 | 检出 | 漏检 | 召回 |
| --- | --- | --- | --- |
| gibberish | 4 | 0 | 100.0% |
| echo | 3 | 0 | 100.0% |
| off_topic | 3 | 0 | 100.0% |

## LLM 模式评估协议（rubric）

规则基线是无 LLM API 时的兜底方案；接入 LLM 后使用**同一标注集**评测，
判定要求：对每条样本输出 `{polluted: bool, fault_type: str|null, reason: str}`。
评分标准：
1. 检出正确：polluted 与标注一致（+1）；类型一致（额外 +0，类型用于根因细分）；
2. 误报：clean 判为 polluted（Precision 扣分，权重同检出）；
3. 漏检：polluted 判为 clean（Recall 扣分）；
4. reason 必须给出可复核依据（否则该条不计分）。
对比口径：**LLM 相对规则基线的 ΔRecall / ΔPrecision 即为 AI 增益的量化证据**
（与 AI-RCA 的「基线对照」设计同构，见 core/dq_engine/ai/rca.py）。

---

> 本报告由 eval/semantic_eval.py 自动生成（固定 seed + 共享污染词表，可复现）。
