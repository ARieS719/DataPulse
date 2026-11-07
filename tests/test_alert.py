"""告警通知器测试（无网络依赖：webhook 失败需优雅兜底）。"""

from __future__ import annotations

from dq_engine.alert import P0, P1, format_alert, notify, notify_block, notify_sla


def test_format_alert_contains_fields():
    msg = format_alert("质量熔断", P0, "表：t1\n失败规则：r1")
    assert "质量熔断" in msg and "P0" in msg and "表：t1" in msg and "失败规则：r1" in msg
    assert "时间：" in msg


def test_notify_without_webhook_logs_and_returns_true():
    assert notify("标题", P1, "详情") is True


def test_notify_block_message():
    assert notify_block("ods.fact_order", rules_failed=["order_id_unique", "amount_range"]) is True


def test_notify_sla_message():
    assert notify_sla("ods.ai_qa_log", 6, "2026-11-07 10:00:00") is True


def test_notify_with_unreachable_webhook_falls_back_gracefully():
    # 连接拒绝等网络异常不抛出（告警通道不可用不阻塞主链路）
    assert notify("标题", P0, "详情", webhook_url="http://127.0.0.1:1/hook") is True


def test_notify_with_mock_webhook(monkeypatch):
    """webhook 成功路径：POST 返回 200 视为投递成功。"""
    import json
    import urllib.request

    calls = []

    class _Resp:
        status = 200

    class _Opener:
        def __init__(self, url, data=None, headers=None, timeout=None):
            calls.append((url, json.loads(data.decode("utf-8"))))

        def __enter__(self):
            return _Resp()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(urllib.request, "Request", lambda url, data=None, headers=None: (url, data, headers))
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Opener(*req, timeout=timeout))
    assert notify("标题", P0, "详情", webhook_url="http://hook.example") is True
    assert len(calls) == 1
    payload = calls[0][1]
    assert payload["msgtype"] == "markdown"
    assert "P0" in payload["markdown"]["text"]
