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

# ── Windows Gateway host auto-detection ──
# Desktop Gateway(9705) runs on the Windows host, reachable via the WSL default gateway IP.
# 2026-07-12: Gateway(9705) must run on the Windows host (WSL can't control desktop).
# Old code referenced an undefined _win_host variable → NameError.
# Changed to auto-detect WSL default gateway (= Windows host IP),
# with env var override support.
_GATEWAY_HOST: str = os.environ.get("AELVOXIM_GATEWAY_HOST", "")
if not _GATEWAY_HOST:
    _GATEWAY_HOST = "127.0.0.1"  # fallback: same machine
    try:
        _r = subprocess.run(
            ["ip", "route"], capture_output=True, text=True, timeout=3,
        )
        for _line in _r.stdout.splitlines():
            if _line.startswith("default"):
                _parts = _line.split()
                if len(_parts) > 2:
                    _GATEWAY_HOST = _parts[2]
                    break
    except Exception:
        log.exception("tool_use error")

def register(name: str):
    """Decorator to register a tool function."""
    def decorator(fn):
        _TOOLS[name] = fn
        return fn
    return decorator


def available_tools() -> List[str]:
    """Return list of registered tool names."""
    return list(_TOOLS.keys())


def check_gateway() -> bool:
    """Check if Desktop Gateway (9705) is reachable on the local network."""
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://{_GATEWAY_HOST}:9705/api/status",
                         method="GET")
        with _ur.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


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
            log.exception("tool_use error")
    # Check persisted authorized paths
    for d in _load_allowed():
        try:
            path.resolve().relative_to(Path(d).resolve())
            return True
        except ValueError:
            log.exception("tool_use error")
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
    if os.environ.get("AELVOXIM_DISABLE_CODE_EXEC", "0") == "1":
        return {"success": False, "error": "Code execution is disabled (AELVOXIM_DISABLE_CODE_EXEC=1)"}
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
    """Search the web for current information. Returns up to 5 results."""
    try:
        from ..learn.search import search as _web_search
        results = _web_search(query[:200], max_results=5)
        if results:
            safe = []
            for r in results[:5]:
                title = (r.get("title") or "")[:120]
                snippet = (r.get("snippet") or "")[:300]
                url = (r.get("url") or "")[:200]
                if title.strip() or snippet.strip():
                    safe.append({"title": title, "snippet": snippet, "url": url})
            if safe:
                return {"success": True, "results": safe}
        return {"success": False, "error": "No results found"}
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}"}


@register("gateway")
def gateway_operation(
    action: str = "", target: str = "", params: Optional[Dict] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute a Desktop Gateway operation on the local machine.
    Accepts additional kwargs (path, text, etc.) forwarded to Gateway."""
    import urllib.request as _ur

    # Alias map: common LLM-generated action names → real Gateway actions
    _ACTION_ALIASES = {
        "open_app": "open",
        "open_application": "open",
        "launch_app": "open",
        "launch": "open",
        "start_app": "open",
        "click": "mouse_click",
        "click_at": "mouse_click",
        "type": "type_text",
        "input_text": "type_text",
        "write_text": "type_text",
        "press_key": "send_keys",
        "press_keys": "send_keys",
        "keyboard": "send_keys",
        "take_screenshot": "screenshot",
        "capture_screen": "screenshot",
    }
    _resolved = _ACTION_ALIASES.get(action, action)
    if _resolved != action:
        log.info("Gateway action alias: %s -> %s", action, _resolved)

    _merged_params = dict(params or {})
    _merged_params.update({k: v for k, v in kwargs.items() if v is not None})
    body = json.dumps({
        "operation": {"action": _resolved, "target": target, "params": _merged_params}
    }).encode()
    req = _ur.Request(
        f"http://{_GATEWAY_HOST}:9705/api/execute",
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


@register("ocr_screenshot")
def ocr_screenshot(target: str = "") -> Dict[str, Any]:
    """Screenshot a window and run OCR. Returns text blocks with coordinates.

    Args:
        target: Window title to capture (empty = fullscreen).

    Returns:
        {"success": bool, "text_blocks": [...], "full_text": str, "error": str}
    """
    import urllib.request as _ur

    body = json.dumps({
        "operation": {"action": "ocr_screenshot", "target": target}
    }).encode()
    req = _ur.Request(
        f"http://{_GATEWAY_HOST}:9705/api/execute",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('AELVOXIM_GATEWAY_KEY', '')}"},
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"success": False, "error": f"OCR Gateway unavailable: {e}"}


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
    if os.environ.get("AELVOXIM_DISABLE_CODE_EXEC", "0") == "1":
        return {"success": False, "error": "Code execution is disabled (AELVOXIM_DISABLE_CODE_EXEC=1)"}
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


# Match [TOOL:xxx]{...} with flexible whitespace (streaming may insert newlines)
# Each character in "TOOL" can be separated by whitespace due to token-level streaming.
TOOL_PATTERN = re.compile(
    r'\['         # [
    r'\s*'        # optional whitespace
    r'T\s*O\s*O\s*L'  # T O O L with optional whitespace between chars
    r'\s*'        #
    r':'          # :
    r'\s*'        #
    r'(\w+)'      # tool name
    r'\s*'        #
    r'\]'         # ]
    r'\s*'        #
    r'(\{.*?\})'  # JSON params
, re.DOTALL)


def _clean_tool_markers(text: str) -> str:
    """Remove whitespace that streaming may insert inside [TOOL:xxx]{...} blocks."""
    import re as _re
    def _clean_one(m: _re.Match) -> str:
        full = m.group(0)
        return "".join(full.split())
    # Match [TO...]{...} — remove ALL whitespace inside the match regardless of token boundaries
    return _re.sub(
        r'\[\s*T\s*[^]]*\]\s*\{[^}]*\}',
        _clean_one,
        text,
        flags=re.DOTALL,
    )


def has_tool_calls(text: str) -> bool:
    """Check if text contains any tool markers."""
    # Strip all whitespace for detection (streaming may split tokens across chunks)
    return bool(TOOL_PATTERN.search("".join(text.split())))


def execute_tool_calls(text: str) -> str:
    """Scan text for [TOOL:xxx] markers, execute each, and replace with results.

    Args:
        text: LLM output potentially containing tool markers.

    Returns:
        Updated text with tool markers replaced by execution results.
    """
    # Remove whitespace from tool markers (streaming may split tokens across chunks)
    text = _clean_tool_markers(text)

    def _replace(match: re.Match) -> str:
        action = match.group(1)
        params_str = match.group(2)
        try:
            params = json.loads(params_str)
        except json.JSONDecodeError as e:
            # Try fixing backslash Windows paths: C:\2.txt is invalid JSON
            if "\\" in params_str:
                try:
                    _fixed = params_str.replace("\\", "\\\\")
                    # But don't double-escape already-escaped chars
                    _fixed = _fixed.replace('\\\\\\"', '\\"')
                    params = json.loads(_fixed)
                except json.JSONDecodeError:
                    log.warning("Tool %s: invalid JSON params: %s", action, e)
                    return f'[Tool {action}: invalid params]'
            else:
                log.warning("Tool %s: invalid JSON params: %s", action, e)
                return f'[Tool {action}: invalid params]'

        # Redirect: [TOOL:gateway] {action:"read_file"...} → use local read_file
        if action == "gateway" and isinstance(params, dict):
            inner_action = params.get("action", "")
            if inner_action in ("read_file", "write_file", "run_code"):
                log.info("Redirecting [TOOL:gateway] %s → local %s", inner_action, inner_action)
                # Map common LLM key names to function parameters
                inner_params = dict(params)
                # Remove Gateway-level keys before forwarding
                inner_params.pop("action", None)
                inner_params.pop("mode", None)
                # Convert Windows paths to WSL paths
                _path = inner_params.get("target") or inner_params.get("path", "")
                if _path:
                    import re as _p_re
                    _m = _p_re.match(r'^([A-Za-z]):\\\\(.*)', _path)
                    if _m:
                        _path = "/mnt/{}/{}{}".format(
                            _m.group(1).lower(),
                            _m.group(2).replace("\\", "/"),
                        )
                    elif _p_re.match(r'^[A-Za-z]:\\', _path):
                        _drive = _path[0].lower()
                        _rest = _path[3:].replace('\\', '/')
                        _path = f"/mnt/{_drive}/{_rest}"
                    inner_params["path"] = _path
                    inner_params.pop("target", None)
                inner_handler = _TOOLS.get(inner_action)
                if inner_handler:
                    try:
                        inner_result = inner_handler(**inner_params)
                        return json.dumps(inner_result, ensure_ascii=False)
                    except Exception as e:
                        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

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
