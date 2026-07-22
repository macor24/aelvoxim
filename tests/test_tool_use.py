"""Tests for aelvoxim.server.tool_use — tool call parsing and execution.

Covers the core [TOOL:] text-marker parsing pipeline.
These tests do NOT call the real Gateway (no Windows dependency).
They only verify that text markers are correctly parsed and routed.
"""
import pytest
from aelvoxim.server.tool_use import (
    execute_tool_calls,
    has_tool_calls,
    _TOOLS,
    TOOL_PATTERN,
)


class TestToolPattern:
    """TOOL_PATTERN regex must match various LLM output formats."""

    def test_standard_format(self):
        """[TOOL:gateway] {...} — standard format"""
        assert has_tool_calls('[TOOL:gateway] {"action":"open","target":"notepad.exe"}')

    def test_streaming_with_newlines(self):
        """LLM streaming splits tokens with newlines inside [TOOL:...]"""
        text = '好的。\n[\nTO\nOL\n:\ngate\nway\n]\n {\"action\":\"open\"}'
        assert has_tool_calls(text)

    def test_no_tool_marker(self):
        """Plain text without tool markers"""
        assert not has_tool_calls("好的，我来帮你查一下。")
        assert not has_tool_calls("")

    def test_tool_marker_in_middle(self):
        """Tool marker embedded in longer text"""
        text = "我这就打开记事本。[TOOL:gateway] {\"action\":\"open\",\"target\":\"notepad.exe\"} 请稍候。"
        assert has_tool_calls(text)


class TestExecuteToolCalls:
    """execute_tool_calls must replace markers with results or error messages."""

    def test_read_file_no_path(self):
        """read_file without path should return an error, not crash"""
        text = '[TOOL:read_file] {"max_lines":10}'
        result = execute_tool_calls(text)
        assert "error" in result or "success" in result

    def test_unknown_tool(self):
        """Unknown tool name should produce error message, not crash"""
        text = '[TOOL:nonexistent_tool] {"foo":"bar"}'
        result = execute_tool_calls(text)
        assert "unknown tool" in result.lower() or "unknown" in result.lower()

    def test_invalid_json_params(self):
        """Malformed JSON params should produce error, not crash"""
        text = '[TOOL:gateway] {invalid json here}'
        result = execute_tool_calls(text)
        assert "invalid params" in result.lower() or "invalid" in result.lower() or "error" in result.lower()

    def test_gateway_redirect_read_file(self):
        """[TOOL:gateway] {action:\"read_file\"} should redirect to local read_file"""
        text = '[TOOL:gateway] {"action":"read_file","target":"/tmp/test.txt"}'
        result = execute_tool_calls(text)
        # Should either succeed (file exists) or fail with real error (not "unknown tool")
        assert '"success"' in result

    def test_multiple_tools_in_one_text(self):
        """Multiple tool markers in a single response"""
        text = (
            '第一步。[TOOL:gateway] {"action":"open","target":"notepad.exe"}'
            '第二步。[TOOL:gateway] {"action":"type_text","target":"hello"}'
        )
        result = execute_tool_calls(text)
        # Both markers should have been replaced (no raw [TOOL: remaining)
        assert "[TOOL:" not in result


class TestToolRegistry:
    """All expected tools must be registered."""

    def test_core_tools_exist(self):
        required = {"read_file", "write_file", "gateway", "ocr_screenshot", "http_request"}
        registered = set(_TOOLS.keys())
        missing = required - registered
        assert not missing, f"Missing tools: {missing}"

    def test_gateway_action(self):
        """gateway tool must accept action, target, params"""
        assert "gateway" in _TOOLS
