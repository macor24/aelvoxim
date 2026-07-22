"""Regression tests for isError-compliance in Windows-MCP tool handlers.

6 of 19 @mcp.tool() handlers caught `except Exception` and returned a
formatted `'Error: ...'` string, which FastMCP wraps as success content
with `isError=false`. The fix replaces each such return with a bare
`raise` so the original exception propagates and FastMCP sets
`isError=true` on the wire.

Reference: https://composio.dev/blog/mcp-security-vulnerabilities (Dayna
Blackwell MCP security audit, June 2026).
"""
import pytest

try:
    from fastmcp.exceptions import ToolError
except ImportError:  # fastmcp not on the test platform
    ToolError = None  # type: ignore[misc,assignment]


pytestmark = pytest.mark.skipif(
    ToolError is None, reason="fastmcp not installed; test is non-Windows or fastmcp missing"
)


def test_shell_tool_error_is_error_true(monkeypatch):
    """Shell tool failure must surface as ToolError → isError=true on wire."""
    from windows_mcp.tools.shell import powershell_tool
    from windows_mcp.powershell import PowerShellExecutor

    def _raise(cmd, timeout):  # noqa: ARG001
        raise RuntimeError("command rejected")

    monkeypatch.setattr(PowerShellExecutor, "execute_command", _raise)
    with pytest.raises(ToolError) as exc_info:
        powershell_tool(command="bad-cmd")
    assert "Error" in str(exc_info.value)


def test_clipboard_tool_error_is_error_true(monkeypatch):
    """Clipboard tool failure must surface as ToolError."""
    from windows_mcp.tools.clipboard import clipboard_tool
    from windows_mcp.clipboard import ClipboardManager

    def _raise():
        raise RuntimeError("clipboard locked")

    monkeypatch.setattr(ClipboardManager, "copy", _raise)
    with pytest.raises(ToolError):
        clipboard_tool(mode="set", text="x")


def test_registry_tool_error_is_error_true(monkeypatch):
    """Registry tool failure must surface as ToolError."""
    from windows_mcp.tools.registry import registry_tool
    from windows_mcp.registry import RegistryManager

    def _raise(key):  # noqa: ARG001
        raise PermissionError("access denied")

    monkeypatch.setattr(RegistryManager, "read_value", _raise)
    with pytest.raises(ToolError):
        registry_tool(mode="get", name="HKLM\\X")
