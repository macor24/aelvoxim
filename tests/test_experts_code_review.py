"""Tests for aelvoxim.experts.code_review — AST-based code security analysis.

All functions are pure (no LLM, no DB, no API calls).
"""
import pytest
from aelvoxim.experts.code_review import (
    analyze_code,
    _check_syntax,
    _check_dangerous_calls,
    _check_network_calls,
    _check_file_ops,
    _check_undefined_vars,
    _check_import_safety,
    _check_style,
    _extract_code_blocks,
)


class TestExtractCodeBlocks:
    """Code block extraction from mixed text."""

    def test_detects_python_code(self):
        text = "Here is code:\ndef hello():\n    print('hi')"
        blocks = _extract_code_blocks(text)
        assert len(blocks) > 0

    def test_no_code_returns_empty(self):
        text = "This is just plain text without any code."
        blocks = _extract_code_blocks(text)
        assert blocks == []

    def test_empty_string(self):
        assert _extract_code_blocks("") == []


class TestCheckSyntax:
    """Python syntax validation."""

    def test_valid_syntax(self):
        valid, msg = _check_syntax("x = 1\nprint(x)")
        assert valid is True
        assert msg is None or msg == ""

    def test_invalid_syntax(self):
        valid, msg = _check_syntax("x = ")
        assert valid is False
        assert msg is not None

    def test_empty_code(self):
        valid, msg = _check_syntax("")
        assert valid is True

    def test_class_and_function(self):
        code = "class Foo:\n    def bar(self):\n        pass"
        valid, msg = _check_syntax(code)
        assert valid is True


class TestDangerousCalls:
    """Detect dangerous function calls (eval, exec, subprocess, etc.)."""

    def test_detect_eval(self):
        import ast
        tree = ast.parse("eval('1+1')")
        issues = _check_dangerous_calls(tree)
        assert len(issues) > 0
        assert any("eval" in i["detail"] for i in issues)

    def test_detect_exec(self):
        import ast
        tree = ast.parse("exec('x=1')")
        issues = _check_dangerous_calls(tree)
        assert len(issues) > 0
        assert any("exec" in i["detail"] for i in issues)

    def test_safe_code_no_issues(self):
        import ast
        tree = ast.parse("x = 1\ny = 2")
        issues = _check_dangerous_calls(tree)
        assert issues == []

    def test_empty_code(self):
        import ast
        tree = ast.parse("pass")
        issues = _check_dangerous_calls(tree)
        assert issues == []


class TestNetworkCalls:
    """Detect network calls (requests, urllib, socket)."""

    def test_detect_requests_get(self):
        import ast
        tree = ast.parse("import requests\nrequests.get('http://evil.com')")
        issues = _check_network_calls(tree)
        assert len(issues) > 0

    def test_detect_urllib(self):
        import ast
        tree = ast.parse("from urllib.request import urlopen\nurlopen('http://evil.com')")
        issues = _check_network_calls(tree)
        assert len(issues) > 0

    def test_safe_code_no_issues(self):
        import ast
        tree = ast.parse("x = 1\nprint(x)")
        issues = _check_network_calls(tree)
        assert issues == []


class TestFileOps:
    """Detect file operations (open, write, delete)."""

    def test_detect_file_write(self):
        import ast
        tree = ast.parse("open('/etc/passwd', 'w').write('hack')")
        issues = _check_file_ops(tree)
        assert len(issues) > 0

    def test_detect_shutil_rm(self):
        import ast
        tree = ast.parse("import shutil\nshutil.rmtree('/important')")
        issues = _check_file_ops(tree)
        assert len(issues) > 0

    def test_safe_read_only(self):
        import ast
        tree = ast.parse("open('/tmp/test.txt', 'r').read()")
        issues = _check_file_ops(tree)
        assert issues == []


class TestImportSafety:
    """Import safety checks."""

    def test_wildcard_import(self):
        import ast
        tree = ast.parse("from os import *")
        issues = _check_import_safety(tree)
        assert len(issues) > 0

    def test_safe_import(self):
        import ast
        tree = ast.parse("import sys\nimport json")
        issues = _check_import_safety(tree)
        assert issues == []


class TestAnalyzeCode:
    """Full pipeline: analyze_code combines all checks."""

    def test_safe_code(self):
        result = analyze_code("x = 1\nprint(x)")
        assert result.get("syntax_ok") is True

    def test_dangerous_code(self):
        code = "import os\nos.system('rm -rf /')"
        result = analyze_code(code)
        assert len(result.get("issues", [])) > 0

    def test_empty_code(self):
        result = analyze_code("")
        assert result.get("syntax_ok") is True

    def test_result_has_required_keys(self):
        result = analyze_code("print('hello')")
        for key in ("issues", "summary", "syntax_ok"):
            assert key in result, f"Missing key: {key}"
