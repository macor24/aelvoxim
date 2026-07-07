"""
metacore.experts.code_review — Code Review Expert.

Performs static analysis on Python code blocks. Pure rule-based, no LLM calls.
Checks: syntax, dangerous calls, network calls, file ops, undefined vars,
        empty try/loops, import safety, basic style.

Each code block is analyzed in <50ms.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Dict, List, Optional, Tuple

from .base import BaseExpert, ExpertInput, ExpertOutput, register

# ── Dangerous function names ──

_DANGEROUS_CALLS = {
    "eval", "exec", "compile", "__import__",
    "os.system", "os.popen", "subprocess.call", "subprocess.Popen",
    "subprocess.run", "subprocess.check_call", "subprocess.check_output",
    "ctypes.CDLL", "ctypes.WinDLL", "ctypes.CFUNCTYPE",
}

_NETWORK_CALLS = {
    "socket", "requests.get", "requests.post", "requests.put",
    "urllib.request", "urllib.urlopen", "httpx", "aiohttp",
}

_RISKY_FILE_OPS = {
    "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
    "os.chmod", "os.chown",
}

_RISKY_MODULES = {
    "pickle", "shelve", "marshal", "cPickle",
}

# ── Extraction ──


def _extract_code_blocks(text: str) -> List[str]:
    """Extract Python code blocks from text (```python ... ```)."""
    blocks = re.findall(r"```(?:python|py)\s*\n(.+?)\n```", text, re.DOTALL)
    if not blocks:
        # Fallback: heuristic — lines with indented Python patterns
        if any(kw in text for kw in ("def ", "class ", "import ", "from ")):
            blocks = [text]
    return blocks


# ── Syntax check ──


def _check_syntax(code: str) -> Tuple[bool, str]:
    """Check if the code is valid Python syntax."""
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"


# ── AST-based checks ──


def _check_dangerous_calls(tree: ast.AST) -> List[Dict]:
    """Find dangerous function calls in AST."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_CALLS:
                issues.append({
                    "type": "dangerous_call",
                    "severity": "error",
                    "line": getattr(node, "lineno", 0),
                    "detail": f"Dangerous function: {node.func.id}()",
                })
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            full_name = ""
            if isinstance(node.func.value, ast.Name):
                full_name = f"{node.func.value.id}.{node.func.attr}"
            if full_name in _DANGEROUS_CALLS:
                issues.append({
                    "type": "dangerous_call",
                    "severity": "error",
                    "line": getattr(node, "lineno", 0),
                    "detail": f"Dangerous function: {full_name}()",
                })
    return issues


def _check_network_calls(tree: ast.AST) -> List[Dict]:
    """Find network-related calls."""
    issues = []
    import_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                import_names.add(node.module.split(".")[0])

    risky_imported = import_names & {"socket", "requests", "urllib", "httpx", "aiohttp"}
    for mod in sorted(risky_imported):
        issues.append({
            "type": "network_call",
            "severity": "warning",
            "line": 0,
            "detail": f"Network module imported: {mod}",
        })
    return issues


def _check_file_ops(tree: ast.AST) -> List[Dict]:
    """Find risky file operations."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                full = f"{node.func.value.id}.{node.func.attr}"
                if full in _RISKY_FILE_OPS:
                    issues.append({
                        "type": "risky_file_op",
                        "severity": "warning",
                        "line": getattr(node, "lineno", 0),
                        "detail": f"Risky file operation: {full}()",
                    })
    return issues


def _check_undefined_vars(tree: ast.AST) -> List[Dict]:
    """Check for potentially undefined variables (simple heuristic)."""
    import builtins as _b
    defined = set(dir(_b)) | {"self", "cls", "args", "kwargs", "_"}
    assigned = set()
    used = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.FunctionDef):
            assigned.add(node.name)
            for arg in node.args.args:
                assigned.add(arg.arg)
            if node.args.vararg:
                assigned.add(node.args.vararg.arg)
            if node.args.kwarg:
                assigned.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assigned.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    assigned.add(alias.asname or alias.name)
        elif isinstance(node, ast.For):
            # For loop variables: for x in ... → x is assigned
            if isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        assigned.add(elt.id)
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    assigned.add(item.optional_vars.id)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                assigned.add(node.name)
        elif isinstance(node, ast.comprehension):
            # List/dict/set comprehensions: [x for x in ...]
            if isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
            elif isinstance(node.target, ast.Tuple):
                for elt in node.target.elts:
                    if isinstance(elt, ast.Name):
                        assigned.add(elt.id)
        elif isinstance(node, ast.Lambda):
            for arg in node.args.args:
                assigned.add(arg.arg)

    undefined = used - assigned - defined
    issues = []
    for var in sorted(undefined):
        if len(var) <= 1 or var in ("print", "len", "range", "int", "str", "list", "dict"):
            continue
        issues.append({
            "type": "undefined_var",
            "severity": "warning",
            "line": 0,
            "detail": f"Possibly undefined variable: {var}",
        })
    return issues


def _check_empty_try_loops(tree: ast.AST) -> List[Dict]:
    """Find empty try blocks and infinite loops."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            if not node.body or (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                issues.append({
                    "type": "empty_try",
                    "severity": "warning",
                    "line": getattr(node, "lineno", 0),
                    "detail": "Empty try block — catches all exceptions without action",
                })
        if isinstance(node, ast.While):
            # Check for while True: without break
            if isinstance(node.test, ast.Constant) and node.test.value is True:
                has_break = any(
                    isinstance(n, ast.Break) for n in ast.walk(node)
                )
                if not has_break:
                    issues.append({
                        "type": "infinite_loop",
                        "severity": "warning",
                        "line": getattr(node, "lineno", 0),
                        "detail": "Potential infinite loop: while True without break",
                    })
    return issues


def _check_import_safety(tree: ast.AST) -> List[Dict]:
    """Check imports of risky modules."""
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _RISKY_MODULES:
                    issues.append({
                        "type": "risky_module",
                        "severity": "warning",
                        "line": getattr(node, "lineno", 0),
                        "detail": f"Risky module: {alias.name}",
                    })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in _RISKY_MODULES:
                    issues.append({
                        "type": "risky_module",
                        "severity": "warning",
                        "line": getattr(node, "lineno", 0),
                        "detail": f"Risky module: {node.module}",
                    })
    return issues


def _check_style(code: str) -> List[Dict]:
    """Check basic code style issues."""
    issues = []
    lines = code.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # Mixed tab/space
        if "\t" in line[: len(line) - len(stripped)]:
            issues.append({
                "type": "mixed_indent",
                "severity": "info",
                "line": i,
                "detail": "Mixed tabs and spaces in indentation",
            })
        # Long lines
        if len(line.rstrip()) > 200:
            issues.append({
                "type": "long_line",
                "severity": "info",
                "line": i,
                "detail": f"Line too long ({len(line)} > 200 chars)",
            })
    return issues


# ── Unified analysis ──


def analyze_code(code: str) -> Dict[str, Any]:
    """Run all code quality checks on a Python code block.

    Returns dict with:
        - syntax_ok: bool
        - syntax_error: str (if syntax_ok is False)
        - issues: list of issue dicts
        - summary: {total, errors, warnings, infos}
    """
    result: Dict[str, Any] = {"issues": [], "summary": {}}

    # 1. Syntax
    syntax_ok, syntax_err = _check_syntax(code)
    result["syntax_ok"] = syntax_ok
    if not syntax_ok:
        result["syntax_error"] = syntax_err
        result["issues"].append({
            "type": "syntax",
            "severity": "error",
            "line": 0,
            "detail": syntax_err,
        })
        # Cannot do AST analysis on invalid code
        errors = [i for i in result["issues"] if i["severity"] == "error"]
        warnings = [i for i in result["issues"] if i["severity"] == "warning"]
        result["summary"] = {
            "total": len(result["issues"]),
            "errors": len(errors),
            "warnings": len(warnings),
        }
        return result

    # 2. AST analysis
    tree = ast.parse(code)
    result["issues"].extend(_check_dangerous_calls(tree))
    result["issues"].extend(_check_network_calls(tree))
    result["issues"].extend(_check_file_ops(tree))
    result["issues"].extend(_check_undefined_vars(tree))
    result["issues"].extend(_check_empty_try_loops(tree))
    result["issues"].extend(_check_import_safety(tree))
    result["issues"].extend(_check_style(code))

    errors = [i for i in result["issues"] if i["severity"] == "error"]
    warnings = [i for i in result["issues"] if i["severity"] == "warning"]
    result["summary"] = {
        "total": len(result["issues"]),
        "errors": len(errors),
        "warnings": len(warnings) + len([i for i in result["issues"] if i["severity"] == "info"]),
    }
    return result


# ── Expert class ──


@register
class CodeReviewExpert(BaseExpert):
    """Reviews Python code for syntax errors, dangerous patterns, and quality issues.

    Runs inside the Expert Orchestrator pipeline. Pure rule-based, no LLM calls.
    Each invocation analyzes all code blocks found in the user query or context.
    """
    _capabilities = ["code", "review", "audit", "quality", "syntax", "static_analysis"]

    name = "code_review"

    def run(self, inp: ExpertInput) -> ExpertOutput:
        block = self._check_shared_block(inp)
        if block:
            block.expert_name = self.name
            return block

        details: Dict[str, Any] = {
            "code_blocks": [],
            "analysis_results": [],
            "summary": {"total": 0, "errors": 0, "warnings": 0},
        }

        query = inp.query or ""

        # Extract code blocks
        blocks = _extract_code_blocks(query)
        if not blocks:
            return ExpertOutput(
                expert_name=self.name,
                opinion="No code to review.",
                confidence=1.0,
                details=details,
            )

        # Analyze each block
        all_errors = 0
        all_warnings = 0
        all_total = 0
        for code in blocks:
            result = analyze_code(code)
            details["code_blocks"].append(code[:100])
            details["analysis_results"].append(result)
            s = result["summary"]
            all_total += s["total"]
            all_errors += s["errors"]
            all_warnings += s["warnings"]

        details["summary"] = {
            "total": all_total,
            "errors": all_errors,
            "warnings": all_warnings,
        }

        # Build opinion
        first_result = details["analysis_results"][0] if details["analysis_results"] else {}
        if not blocks:
            opinion_text = "No code to review."
        else:
            if not first_result.get("syntax_ok", True):
                opinion_text = f"Code review: SYNTAX ERROR — {first_result.get('syntax_error', '')}"
            elif all_total == 0:
                opinion_text = "Code review: No issues found, code looks clean."
            else:
                opinion_text = f"Code review: {all_total} issues ({all_errors} errors, {all_warnings} warnings)"

        # Confidence: lower when errors found
        has_errors = any(r.get("summary", {}).get("errors", 0) > 0
                         for r in details["analysis_results"])
        confidence = 1.0 if not has_errors else 0.3

        return ExpertOutput(
            expert_name=self.name,
            opinion=opinion_text,
            confidence=confidence,
            details=details,
            error="Code review failed: syntax error" if not first_result.get("syntax_ok", True) else None,
        )
