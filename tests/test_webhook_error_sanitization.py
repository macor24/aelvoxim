"""Webhook 错误信息卫生（CodeQL #153，2026-11-03）。

WHY: `/v1/webhook/test-delivery` 把 `deliver_event()` 的结果原样回给客户端，而
`deliver_event` / `subscribe` 曾把 `str(e)` 放进 `error` 字段 ⇒ 内网主机/端口/SQL
细节随 API 响应外泄。异常原文只该进日志。

本文件钉两件事：
1) 这两个返回点不许再出现 `str(e)`；
2) 返回的错误文案是稳定的人类可读串（前端/文档依赖它）。
"""
import os
import re

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "aelvoxim", "server", "webhook.py")


def _src():
    return open(SRC, encoding="utf-8").read()


def test_no_exception_text_in_client_facing_errors():
    s = _src()
    # 只看 return/响应字典里的 error 字段，日志行(f"...{e}")不算
    bad = re.findall(r'"error":\s*(?:str\(e\)|f"[^"]*\{e\}[^"]*")', s)
    assert not bad, "客户端可见的 error 仍拼接了异常原文: %s" % bad


def test_error_strings_are_stable():
    s = _src()
    assert '"error": "failed to create subscription"' in s
    assert '"error": "connection failed"' in s
    assert '"error": "max retries exceeded"' in s


def test_exception_detail_still_logged():
    """可观测性不能一起丢：原文必须进日志。"""
    s = _src()
    assert "_log.exception(\"webhook subscribe failed\")" in s
    assert "webhook delivery failed after {max_retries} attempts" in s


# ── 路由层兜底（同一个 alert #153 的第二道闸）────────────────────────────
def test_route_returns_sanitized_results():
    from aelvoxim.server.routes_system import _safe_delivery_results as safe
    host = "postgres://admin:pw@10.0.0.7:5432/aelvoxim"
    out = safe([{"sub_id": "s1", "url": "http://x", "status": "failed",
                 "error": host, "secret": "leak"}])
    assert out[0]["error"] == "delivery failed", out
    assert "secret" not in out[0], "白名单外的键必须丢弃"
    assert "10.0.0.7" not in str(out), "内网地址不得出现在响应里"


def test_route_keeps_http_error_label_and_unknown_shapes():
    from aelvoxim.server.routes_system import _safe_delivery_results as safe
    assert safe([{"error": "HTTP 500"}])[0]["error"] == "HTTP error"
    assert safe(None) == [] and safe([None, "x"]) == []


def test_route_uses_the_sanitizer():
    """源级棘轮：路由不许直接 return 裸 results。"""
    p = os.path.join(os.path.dirname(__file__), "..", "src", "aelvoxim", "server", "routes_system.py")
    s = open(p, encoding="utf-8").read()
    assert "return {\"results\": _safe_delivery_results(results)}" in s
    assert "return {\"results\": results}" not in s
