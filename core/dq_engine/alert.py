"""M6+：告警通知器（可插拔）——质量熔断 / 超 SLA 事件的闭环出口。

设计：
  * 与门禁解耦：quality_gate 只产出事件字典，notify() 负责格式化与投递；
  * 默认 LogNotifier（本地演示留痕），配置 DQP_WEBHOOK_URL 后追加钉钉/企微
    群机器人投递（webhook POST，urllib 标准库实现，无额外依赖）；
  * 分级：P0（熔断，阻断发布）/ P1（告警，不阻断）。

用法：
  from dq_engine.alert import notify_block, notify_sla
  notify_block(table="ods.fact_order", verdict="BLOCK", rules=[...], webhook_url=os.getenv("DQP_WEBHOOK_URL"))
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime

logger = logging.getLogger("datapulse.alert")

P0 = "P0"
P1 = "P1"
DEFAULT_WEBHOOK_ENV = "DQP_WEBHOOK_URL"


def format_alert(title: str, level: str, detail: str) -> str:
    """钉钉/企微 markdown 消息体（也用于日志留痕）。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"【DataPulse · {level}】{title}\n"
        f"时间：{ts}\n"
        f"{detail}"
    )


def _post_webhook(url: str, payload: dict) -> bool:
    """POST JSON 到群机器人 webhook；失败仅记日志不抛出（告警通道不可用不阻塞主链路）。"""
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001 网络异常需兜底
        logger.warning("webhook 投递失败：%s", exc)
        return False


def notify(title: str, level: str, detail: str, webhook_url: str | None = None) -> bool:
    """投递一条告警：日志通道恒定生效，webhook 可选。

    返回 True 表示至少日志通道已记录（本地演示口径）。
    """
    message = format_alert(title, level, detail)
    if level == P0:
        logger.error(message)
    else:
        logger.warning(message)
    url = webhook_url or os.getenv(DEFAULT_WEBHOOK_ENV)
    if url:
        _post_webhook(
            url,
            {"msgtype": "markdown", "markdown": {"title": f"DataPulse {level}", "text": message}},
        )
    return True


def notify_block(table: str, rules_failed: list[str], webhook_url: str | None = None) -> bool:
    """P0 熔断告警：门禁 BLOCK 时由 quality_gate_cli / run_wap_gate 调用。"""
    detail = (
        f"表：{table}\n"
        f"失败规则：{', '.join(rules_failed) or '-'}\n"
        "处置：审计分支保留、下游 DAG 挂起，待修复后重跑门禁。"
    )
    return notify("质量熔断：数据发布被阻断", P0, detail, webhook_url)


def notify_sla(table: str, sla_hours: float, last_seen: str, webhook_url: str | None = None) -> bool:
    """P1 SLA 告警：表超出质量 SLA（未按时巡检 / 最近一次 BLOCK）。"""
    detail = (
        f"表：{table}\n"
        f"SLA：{sla_hours} 小时；最近一次门禁：{last_seen}\n"
        "处置：检查调度任务与上游供数状态。"
    )
    return notify("质量 SLA 超时", P1, detail, webhook_url)
