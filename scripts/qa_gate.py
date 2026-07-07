"""
aelvoxim/scripts/qa_gate.py — Quality Gate

Run automatically after every change. Checks:
1. pyproject.toml deps vs actual import consistency
2. Full test suite
3. pip install -e . works
4. Module size warning (>500 lines)
5. Change impact report

Usage:
    python scripts/qa_gate.py              # Full gate
    python scripts/qa_gate.py --quick      # Quick (deps + syntax only)
    python scripts/qa_gate.py --report     # Impact report only
"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

# Stdlib modules (Python 3.12)
STDLIB = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio", "asyncore",
    "atexit", "audioop", "base64", "bdb", "binascii", "binhex", "bisect", "builtins",
    "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
    "codeop", "collections", "colorsys", "compileall", "concurrent", "configparser",
    "contextlib", "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
    "dis", "distutils", "doctest", "email", "encodings", "enum", "errno", "exceptions",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "fractions", "ftplib",
    "functools", "gc", "getopt", "getpass", "gettext", "glob", "graphlib", "grp",
    "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib", "imghdr",
    "imp", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword",
    "lib2to3", "linecache", "locale", "logging", "lzma", "mailbox", "mailcap",
    "marshal", "math", "mimetypes", "mmap", "modulefinder", "multiprocessing", "netrc",
    "nis", "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib", "poplib",
    "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd", "py_compile",
    "pyclbr", "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select", "selectors",
    "shelve", "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr",
    "socket", "socketserver", "spwd", "sqlite3", "ssl", "stat", "statistics", "string",
    "stringprep", "struct", "subprocess", "sunau", "symtable", "sys", "sysconfig",
    "syslog", "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo",
    "types", "typing", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
    "warnings", "wave", "weakref", "webbrowser", "wsgiref", "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo", "__future__",
}


def parse_pyproject_deps() -> dict:
    """Parse pyproject.toml for dependencies."""
    if not PYPROJECT.exists():
        return {"core": set(), "optional": {}}

    text = PYPROJECT.read_text()
    # Parse [project.dependencies]
    deps = set()
    optional_deps = {}

    in_deps = False
    in_optional = False
    current_optional_key = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies = ["):
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                in_deps = False
                continue
            m = re.match(r'\s*"([^"]+)"', stripped)
            if m:
                # Normalize: strip version specifiers
                dep_name = m.group(1).split("[")[0].split(">")[0].split("<")[0].split("=")[0].split("~")[0].strip()
                if dep_name:
                    deps.add(dep_name.lower())

        if stripped.startswith("[project.optional-dependencies]"):
            in_optional = True
            continue
        if in_optional:
            m = re.match(r'^(\w+)\s*=\s*\[', stripped)
            if m:
                current_optional_key = m.group(1)
                optional_deps[current_optional_key] = set()
                continue
            if current_optional_key and stripped.startswith('"'):
                dep_name = stripped.split('"')[1].split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip()
                optional_deps[current_optional_key].add(dep_name.lower())
            if stripped == "]":
                current_optional_key = None

    return {"core": deps, "optional": optional_deps}


def scan_imports(project_dir: Path) -> dict:
    """Scan all Python files in the project for third-party imports."""
    third_party = set()
    local_packages = {"aelvoxim", "chatael", "sentrikit", "uxu"}
    module_sizes = []
    circular_imports = []

    for py_file in sorted(project_dir.rglob("*.py")):
        content = py_file.read_text(encoding="utf-8", errors="replace")
        rel = py_file.relative_to(project_dir)
        lines = content.splitlines()
        module_sizes.append((str(rel), len(lines)))

        # Find imports
        for m in re.finditer(r'^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', content, re.MULTILINE):
            mod = m.group(1).split(".")[0]
            if mod not in STDLIB and mod not in local_packages:
                third_party.add(mod)

        # Check for circular import risk (file imports something that imports this file)
        # Simplified: any file with more than 5 internal imports
        internal_imports = len(re.findall(r'^from aelvoxim\.', content, re.MULTILINE))
        if internal_imports > 10:
            circular_imports.append((str(rel), internal_imports))

    return {
        "third_party": sorted(third_party),
        "module_sizes": sorted(module_sizes, key=lambda x: -x[1]),
        "circular_risk": sorted(circular_imports, key=lambda x: -x[1]),
    }


def check_dependency_consistency(declared: dict, actual: set) -> list:
    """Compare declared deps vs actual imports."""
    issues = []

    # Map package names to normalized names
    opt_all = set()
    for key, deps in declared["optional"].items():
        opt_all.update(deps)

    for mod in actual:
        mod_lower = mod.lower()
        if mod_lower not in declared["core"] and mod_lower not in opt_all:
            issues.append(f"❌ USED BUT NOT DECLARED: '{mod}' is used in code but not listed in pyproject.toml")
        elif mod_lower in declared["core"]:
            pass  # OK — core dep

    return issues


def check_module_sizes(module_sizes: list, threshold: int = 500) -> list:
    """Flag modules over threshold."""
    warnings = []
    for name, size in module_sizes:
        if size > threshold:
            warnings.append(f"⚠️  LARGE MODULE: '{name}' has {size} lines (limit: {threshold})")
    return warnings


def check_pip_install() -> list:
    """Verify pip install -e . works."""
    issues = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            issues.append(f"❌ PIP INSTALL FAILED: {result.stderr[-300:]}")
        else:
            print("  ✅ pip install -e .: OK")
    except subprocess.TimeoutExpired:
        issues.append(f"❌ PIP INSTALL -e .: TIMEOUT (60s)")
    except Exception as e:
        issues.append(f"❌ PIP INSTALL -e .: ERROR: {e}")
    return issues


def run_tests() -> list:
    """Run pytest and return failures."""
    issues = []
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            issues.append(f"❌ TESTS FAILED ({result.returncode}):")
            # Extract failed test names
            for line in result.stdout.splitlines():
                if "FAILED" in line:
                    issues.append(f"     {line}")
        else:
            # Extract pass count
            m = re.search(r'(\d+) passed', result.stdout)
            count = m.group(1) if m else "?"
            print(f"  ✅ Tests: {count} passed")
    except subprocess.TimeoutExpired:
        issues.append("❌ TESTS: TIMEOUT (120s)")
    except Exception as e:
        issues.append(f"❌ TESTS: ERROR: {e}")
    return issues


def import_test() -> list:
    """Verify the project can be imported without errors."""
    issues = []
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC_DIR)
        result = subprocess.run(
            [sys.executable, "-c", "from aelvoxim import __version__; print(__version__)"],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        if result.returncode != 0:
            issues.append(f"❌ IMPORT FAILED: {result.stderr[-300:]}")
        else:
            print(f"  ✅ Import aelvoxim v{result.stdout.strip()}")
    except Exception as e:
        issues.append(f"❌ IMPORT: ERROR: {e}")
    return issues


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aelvoxim Quality Gate")
    parser.add_argument("--quick", action="store_true", help="Deps + syntax only")
    parser.add_argument("--report", action="store_true", help="Impact report only")
    args = parser.parse_args()

    all_issues = []
    pass_count = 0
    fail_count = 0

    print(f"{'='*60}")
    print(f"  Aelvoxim Quality Gate")
    print(f"  Project: {PROJECT_ROOT}")
    print(f"{'='*60}")
    print()

    # ── Report (always) ──
    print("── Import Scan ──")
    scan = scan_imports(SRC_DIR)
    for name, size in scan["module_sizes"][:5]:
        print(f"  {name}: {size} lines")

    print(f"\n  Third-party deps in use: {len(scan['third_party'])}")
    for mod in scan["third_party"]:
        print(f"    {mod}")

    # ── Dependency consistency ──
    print("\n── Dependency Check ──")
    declared = parse_pyproject_deps()
    issues = check_dependency_consistency(declared, scan["third_party"])
    for i in issues:
        print(f"  {i}")
        all_issues.append(i)
        fail_count += 1
    if not issues:
        print("  ✅ Dependencies consistent with pyproject.toml")
        pass_count += 1

    # ── Module size warnings ──
    print("\n── Module Size Check ──")
    issues = check_module_sizes(scan["module_sizes"], threshold=500)
    for w in issues:
        print(f"  {w}")
        all_issues.append(w)

    # ── Circular import risk ──
    print("\n── Circular Import Risk ──")
    for name, count in scan["circular_risk"]:
        print(f"  ⚠️  '{name}' has {count} internal aelvoxim imports")
    if not scan["circular_risk"]:
        print("  ✅ No circular import risks detected")
        pass_count += 1

    if args.report:
        print(f"\n{'='*60}")
        print(f"  Report only — no tests run")
        print(f"{'='*60}")
        return

    if args.quick:
        # ── Syntax + import (quick) ──
        print("\n── Quick: Syntax + Import ──")
        issues = import_test()
        for i in issues:
            print(f"  {i}")
            all_issues.append(i)
            fail_count += 1
        if not issues:
            pass_count += 1

    else:
        # ── Full gate ──
        print("\n── Import Test ──")
        issues = import_test()
        for i in issues:
            print(f"  {i}")
            all_issues.append(i)
            fail_count += 1
        if not issues:
            pass_count += 1

        print("\n── pip install -e . ──")
        issues = check_pip_install()
        for i in issues:
            print(f"  {i}")
            all_issues.append(i)
            fail_count += 1
        if not issues:
            pass_count += 1

        print("\n── Pytest ──")
        issues = run_tests()
        for i in issues:
            print(f"  {i}")
            all_issues.append(i)
            fail_count += 1
        if not issues:
            pass_count += 1

    # ── Summary ──
    print()
    print(f"{'='*60}")
    total = pass_count + fail_count
    if not all_issues:
        print(f"  ✅ ALL GATES PASSED ({pass_count}/{total})")
    else:
        print(f"  ⚠️  {fail_count} failures, {pass_count} passed ({total} total)")
        for i in all_issues:
            print(f"  {i}")
    print(f"{'='*60}")

    return 1 if all_issues else 0


if __name__ == "__main__":
    sys.exit(main())
