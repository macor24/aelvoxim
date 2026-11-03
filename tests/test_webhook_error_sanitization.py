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
