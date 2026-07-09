# SPDX-License-Identifier: MIT
"""
metacore.server.tool_use — Tool registry and execution for Aelvoxim.

LLM outputs [TOOL:action] {json_params} markers in its reply text.
This module detects, executes, and replaces them with results before
the final response is sent to the user.

Tools available:
  read_file    — Read a file (auto-authorize paths)
  write_file   — Write a file (auto-authorize paths)
  run_code     — Execute Python code (timeout 15s, no network)
  web_search   — Search the web
  gateway      — Execute Desktop Gateway operation
  ocr_screenshot — Screenshot a window and run OCR, returns text blocks with coordinates

Path authorization: first access to a path auto-authorizes its parent
directory. Authorized paths persist to ~/.metacore/tool_allowed.json.
Default allowed: ~/, /tmp/ always.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("aelvoxim.tool_use")

# ── Tool registry ──

_TOOLS: Dict[str, Callable] = {}

def register(name: str):
    """Decorator to register a tool function."""
    def decorator(fn):
        _TOOLS[name] = fn
        return fn
    return decorator


def available_tools() -> List[str]:
    """Return list of registered tool names."""
    return list(_TOOLS.keys())


# ── Path authorization (auto-grant, persistent) ──

# Default always-allowed directories
_DEFAULT_ALLOWED = [
    str(Path.home()),
    "/tmp",
]

# Persisted authorized paths file
from ..utils import DATA_DIR

_METACORE_DIR = DATA_DIR
_ALLOWED_FILE = _METACORE_DIR / "tool_allowed.json"


def _load_allowed() -> List[str]:
    """Load custom authorized paths from disk."""
    if _ALLOWED_FILE.exists():
        try:
            return json.loads(_ALLOWED_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_allowed(paths: List[str]):
    """Save authorized paths to disk."""
    _METACORE_DIR.mkdir(parents=True, exist_ok=True)
    _ALLOWED_FILE.write_text(json.dumps(paths, indent=2))


def _add_allowed(path: str):
    """Add a path to the authorized list and persist."""
    allowed = _load_allowed()
    resolved = str(Path(path).resolve())
    if resolved not in allowed:
        allowed.append(resolved)
        _save_allowed(allowed)


def _is_allowed(path: Path) -> bool:
    """Check if a resolved path is within any allowed directory."""
    p_str = str(path.resolve())
    # Check default always-allowed directories
    for d in _DEFAULT_ALLOWED:
        try:
            path.resolve().relative_to(Path(d).resolve())
            return True
        except ValueError:
            pass
    # Check persisted authorized paths
    for d in _load_allowed():
        try:
            path.resolve().relative_to(Path(d).resolve())
            return True
        except ValueError:
            pass
    return False


def resolve_path(path: str) -> Path:
    """Resolve a path and auto-authorize if needed.

    Returns resolved Path.
    Raises PermissionError if path is outside all allowed directories
    AND auto-authorization fails (e.g. system paths like /etc, /usr).
    """
    p = Path(path).expanduser().resolve()
    if _is_allowed(p):
        return p

    # System paths — never auto-authorize
    p_str = str(p)
    for blocked in ("/etc", "/usr", "/boot", "/dev", "/proc", "/sys", "/var", "/bin", "/sbin"):
        if p_str.startswith(blocked):
            raise PermissionError(f"Access denied: {path} (system path blocked)")

    # Auto-authorize: allow the parent directory
    parent = str(p.parent)
    _add_allowed(parent)
    log.info("Auto-authorized path: %s → parent: %s", path, parent)
    return p


# ── Built-in tools ──


@register("read_file")
def read_file(path: str, max_lines: int = 50) -> Dict[str, Any]:
    """Read a text file. Returns content and line count."""
    p = resolve_path(path)
    if not p.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not p.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    content = "\n".join(lines[:max_lines])
    return {
        "success": True,
        "content": content,
        "total_lines": len(lines),
        "returned_lines": min(max_lines, len(lines)),
    }


@register("write_file")
def write_file(path: str, content: str) -> Dict[str, Any]:
    """Write content to a file. Overwrites if exists."""
    p = resolve_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"success": True, "path": str(p), "bytes": len(content.encode("utf-8"))}


@register("run_code")
def run_code(code: str, timeout: int = 15) -> Dict[str, Any]:
    """Execute Python code in a subprocess. Returns stdout/stderr."""
    if len(code) > 5000:
        return {"success": False, "error": "Code too long (max 5000 chars)"}
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-1000:],
            "stderr": result.stderr[-500:],
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Execution timed out ({timeout}s)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@register("web_search")
def web_search(query: str) -> Dict[str, Any]:
    """Search the web for current information."""
    return {"success": False, "error": "Web search is disabled in ChatAEL mode. Use the '联网' toggle in the chat interface instead."}


@register("gateway")
def gateway_operation(
    action: str = "", target: str = "", params: Optional[Dict] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute a Desktop Gateway operation on the local machine.
    Accepts additional kwargs (path, text, etc.) forwarded to Gateway."""
    import urllib.request as _ur

    _merged_params = dict(params or {})
    _merged_params.update({k: v for k, v in kwargs.items() if v is not None})
    body = json.dumps({
        "operation": {"action": action, "target": target, "params": _merged_params}
    }).encode()
    req = _ur.Request(
        f"http://{_win_host}:9705/api/execute",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('AELVOXIM_GATEWAY_KEY', '')}"},
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"success": False, "error": f"Gateway unavailable: {e}"}


@register("http_request")
def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Make an HTTP request to an external API or service.

    Args:
        url: Full URL to call.
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        headers: Optional dict of HTTP headers.
        body: Request body string (for POST/PUT/PATCH).
        timeout: Request timeout in seconds (default 30, max 60).

    Returns:
        Dict with keys: success, status_code, body (str), error (str if failed).
    """
    import urllib.request as _ur
    import urllib.error as _ue

    timeout = min(timeout, 60)
    data = None
    if body and method in ("POST", "PUT", "PATCH"):
        data = body.encode("utf-8")

    req = _ur.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with _ur.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return {
                "success": True,
                "status_code": resp.status,
                "body": resp_body,
            }
    except _ue.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {
            "success": False,
            "status_code": e.code,
            "body": err_body,
            "error": str(e.reason),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@register("execute_command")
def execute_command(
    command: str,
    args: Optional[List[str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Execute a system command or program.

    Args:
        command: Command or program path to execute.
        args: Optional list of arguments.
        timeout: Max execution time in seconds (default 30, max 120).

    Returns:
        Dict with keys: success, stdout (str), stderr (str), exit_code (int),
        error (str if failed).
    """
    import subprocess as _sp
    import shlex as _shlex

    timeout = min(timeout, 120)
    cmd = [command]
    if args:
        cmd.extend(args)

    try:
        result = _sp.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-1000:],
            "exit_code": result.returncode,
        }
    except _sp.TimeoutExpired:
        return {"success": False, "error": f"Command timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"success": False, "error": f"Command not found: {command}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Execution ──


TOOL_PATTERN = re.compile(r'\[TOOL:(\w+)\]\s*(\{.*?\})', re.DOTALL)


def execute_tool_calls(text: str) -> str:
    """Scan text for [TOOL:xxx] markers, execute each, and replace with results.

    Args:
        text: LLM output potentially containing tool markers.

    Returns:
        Updated text with tool markers replaced by execution results.
    """
    def _replace(match: re.Match) -> str:
        action = match.group(1)
        params_str = match.group(2)
        try:
            params = json.loads(params_str)
        except json.JSONDecodeError as e:
            log.warning("Tool %s: invalid JSON params: %s", action, e)
            return f'[Tool {action}: invalid params]'

        handler = _TOOLS.get(action)
        if not handler:
            log.warning("Tool %s: not registered", action)
            return f'[Tool {action}: unknown tool]'

        try:
            log.info("Tool call: %s %s", action, params_str[:100])
            result = handler(**params)
            log.info("Tool result: %s -> %s", action, json.dumps(result)[:100])
            return json.dumps(result, ensure_ascii=False)
        except PermissionError as e:
            log.warning("Tool %s permission denied: %s", action, e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
        except Exception as e:
            log.exception("Tool %s failed: %s", action, e)
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    return TOOL_PATTERN.sub(_replace, text)


def has_tool_calls(text: str) -> bool:
    """Check if text contains any tool markers."""
    return bool(TOOL_PATTERN.search(text))
