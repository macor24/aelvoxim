"""投递结果白名单（CodeQL #153 的路由层兜底，2026-11-03）

`/v1/webhook/test-delivery` 曾把 `deliver_event()` 的结果原样回给客户端：
内网主机、端口、SQL 细节一旦进了 `error` 字段就跟着响应出去。
修法两层：webhook.py 不再拼异常原文（已有 test_webhook_error_sanitization.py），
路由层再收一道白名单（本文件）。
"""
from aelvoxim.server.routes_system import _safe_delivery_results


def test_only_whitelisted_keys_survive():
    raw = [{
        "sub_id": "s1", "url": "https://example.com/hook", "status": "failed",
        "status_code": 500, "attempts": 3,
        "internal_host": "10.0.0.7", "db_dsn": "postgresql://user:pw@localhost/aelvoxim",
        "traceback": "Traceback (most recent call last): ...",
    }]
    out = _safe_delivery_results(raw)[0]
    assert set(out) <= {"sub_id", "url", "status", "status_code", "attempts", "error"}
    assert "internal_host" not in out and "db_dsn" not in out and "traceback" not in out


def test_error_text_is_folded_not_passed_through():
    raw = [
        {"sub_id": "a", "status": "failed", "error": "HTTP 404"},
        {"sub_id": "b", "status": "failed",
         "error": "connection refused to 10.0.0.7:5432 (password=hunter2)"},
    ]
    out = _safe_delivery_results(raw)
    assert out[0]["error"] == "HTTP error"
    assert out[1]["error"] == "delivery failed"
    assert all("10.0.0.7" not in str(r) and "hunter2" not in str(r) for r in out)


def test_success_row_has_no_error_key():
    out = _safe_delivery_results([{"sub_id": "a", "status": "delivered", "status_code": 200}])
    assert "error" not in out[0]


def test_malformed_input_does_not_crash():
    assert _safe_delivery_results(None) == []
    assert _safe_delivery_results([]) == []
    assert _safe_delivery_results(["not a dict", 42, None]) == []
