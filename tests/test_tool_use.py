"""Tests for aelvoxim.server.tool_use — tool call parsing and execution.

Covers the core [TOOL:] text-marker parsing pipeline.
These tests never touch the network or a desktop (2026-10-17: the Desktop
Gateway tools [TOOL:gateway] / [TOOL:ocr_screenshot] were removed, so the
parser is exercised with read_file, which is local and safe).
"""
from aelvoxim.server.tool_use import (
    execute_tool_calls,
    has_tool_calls,
    _TOOLS,
)


class TestToolPattern:
    """TOOL_PATTERN regex must match various LLM output formats."""

    def test_standard_format(self):
        """[TOOL:read_file] {...} — standard format"""
        assert has_tool_calls('[TOOL:read_file] {"target":"/tmp/x.txt"}')

    def test_streaming_with_newlines(self):
        """LLM streaming splits tokens with newlines inside [TOOL:...]"""
        text = '好的。\n[\nTO\nOL\n:\nread\n_fil\ne\n]\n {"target":"/tmp/x.txt"}'
        assert has_tool_calls(text)

    def test_no_tool_marker(self):
        """Plain text without tool markers"""
        assert not has_tool_calls("好的，我来帮你查一下。")
        assert not has_tool_calls("")

    def test_tool_marker_in_middle(self):
        """Tool marker embedded in longer text"""
        text = '我先看一眼。 [TOOL:read_file] {"target":"/tmp/x.txt"} 请稍候。'
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
        text = '[TOOL:read_file] {invalid json here}'
        result = execute_tool_calls(text)
        assert "invalid params" in result.lower() or "invalid" in result.lower() or "error" in result.lower()

    def test_multiple_tools_in_one_text(self):
        """Multiple tool markers in a single response"""
        text = (
            '第一步。[TOOL:read_file] {"target":"/tmp/a.txt"}'
            '第二步。[TOOL:read_file] {"target":"/tmp/b.txt"}'
        )
        result = execute_tool_calls(text)
        # Both markers should have been replaced (no raw [TOOL: remaining)
        assert "[TOOL:" not in result


class TestToolRegistry:
    """All expected tools must be registered."""

    def test_core_tools_exist(self):
        required = {"read_file", "write_file", "run_code", "web_search", "http_request"}
        registered = set(_TOOLS.keys())
        missing = required - registered
        assert not missing, f"Missing tools: {missing}"

    def test_desktop_tools_are_gone(self):
        """[TOOL:gateway] / [TOOL:ocr_screenshot] removed with the Gateway (2026-10-17)."""
        assert "gateway" not in _TOOLS
        assert "ocr_screenshot" not in _TOOLS
