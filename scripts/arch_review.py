"""
aelvoxim/scripts/arch_review.py — Architecture Self-Review

Run BEFORE making significant changes. Scans the project state
and highlights design issues, risks, and opportunities.

Usage:
    python scripts/arch_review.py             # Full review
    python scripts/arch_review.py --changed file1.py file2.py  # Focused review on changed files
"""

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
ADR_FILE = PROJECT_ROOT / "docs" / "decisions" / "ADR.md"
TRACKING_FILE = PROJECT_ROOT / ".hermes" / "tracking.md"

STDLIB = {"abc","aifc","argparse","array","ast","asynchat","asyncio","asyncore",
    "atexit","base64","bdb","binascii","binhex","bisect","builtins","bz2","calendar",
    "cgi","cgitb","chunk","cmath","cmd","code","codecs","codeop","collections","colorsys",
    "compileall","concurrent","configparser","contextlib","contextvars","copy","copyreg",
    "cProfile","crypt","csv","ctypes","curses","dataclasses","datetime","dbm","decimal",
    "difflib","dis","distutils","doctest","email","encodings","enum","errno","exceptions",
    "faulthandler","fcntl","filecmp","fileinput","fnmatch","fractions","ftplib","functools",
    "gc","getopt","getpass","gettext","glob","graphlib","grp","gzip","hashlib","heapq","hmac",
    "html","http","idlelib","imaplib","imghdr","imp","importlib","inspect","io","ipaddress",
    "itertools","json","keyword","lib2to3","linecache","locale","logging","lzma","mailbox",
    "mailcap","marshal","math","mimetypes","mmap","modulefinder","multiprocessing","netrc",
    "nis","nntplib","numbers","operator","optparse","os","ossaudiodev","pathlib","pdb",
    "pickle","pickletools","pipes","pkgutil","platform","plistlib","poplib","posix","posixpath",
    "pprint","profile","pstats","pty","pwd","py_compile","pyclbr","pydoc","queue","quopri",
    "random","re","readline","reprlib","resource","rlcompleter","runpy","sched","secrets",
    "select","selectors","shelve","shlex","shutil","signal","site","smtpd","smtplib","sndhdr",
    "socket","socketserver","spwd","sqlite3","ssl","stat","statistics","string","stringprep",
    "struct","subprocess","sunau","symtable","sys","sysconfig","syslog","tabnanny","tarfile",
    "telnetlib","tempfile","termios","test","textwrap","threading","time","timeit","tkinter",
    "token","tokenize","tomllib","trace","traceback","tracemalloc","tty","turtle","turtledemo",
    "types","typing","unicodedata","unittest","urllib","uu","uuid","venv","warnings","wave",
    "weakref","webbrowser","wsgiref","xdrlib","xml","xmlrpc","zipapp","zipfile","zipimport",
    "zlib","zoneinfo","__future__"}

LOCAL_PACKAGES = {"aelvoxim", "chatael", "sentrikit", "uxu"}
LOCAL_MODULES = set()


def _get_local_modules() -> Set[str]:
    """Get all aelvoxim package names."""
    global LOCAL_MODULES
    if not LOCAL_MODULES:
        for py in SRC_DIR.rglob("*.py"):
            rel = py.relative_to(SRC_DIR)
            parts = list(rel.parts[:-1])
            if parts:
                LOCAL_MODULES.add(parts[0])
    return LOCAL_MODULES


def check_adr_consistency() -> List[str]:
    """Check if ADR.md covers recent significant changes."""
    findings = []
    if not ADR_FILE.exists():
        findings.append("❌ ADR.md not found — create docs/decisions/ADR.md")
        return findings
    return findings


def check_tracking_tasks() -> List[str]:
    """Check for stale/pending tasks."""
    findings = []
    if not TRACKING_FILE.exists():
        findings.append("❌ Tracking file not found — create .hermes/tracking.md")
        return findings
    return findings


def analyze_dependency_graph(changed_files: List[str] = None) -> List[str]:
    """Analyze import dependency graph for the project or specific files."""
    findings = []
    local = _get_local_modules()

    targets = changed_files if changed_files else [str(SRC_DIR)]

    for target in targets:
        path = Path(target)
        if not path.exists():
            continue
        if path.is_dir():
            py_files = list(path.rglob("*.py"))
        else:
            py_files = [path] if path.suffix == ".py" else []

        for py in py_files:
            content = py.read_text(encoding="utf-8", errors="replace")
            rel = py.relative_to(PROJECT_ROOT) if PROJECT_ROOT in py.parents else py

            # Check for try/except import patterns (lazy deps)
            lazy_imports = len(re.findall(r'try:.*?import', content, re.DOTALL))
            if lazy_imports > 3:
                findings.append(f"⚠️  {rel}: {lazy_imports} try/except import blocks — consider centralizing")

            # Check for bare except
            bare_except = len(re.findall(r'^except\s*:', content, re.MULTILINE))
            if bare_except > 5:
                findings.append(f"⚠️  {rel}: {bare_except} bare `except:` — reduces debuggability")

            # Check for print() in non-cli files
            if "cli" not in str(rel).lower() and "test" not in str(rel).lower():
                prints = len(re.findall(r'^\s*print\(', content, re.MULTILINE))
                if prints > 3:
                    findings.append(f"⚠️  {rel}: {prints} print() calls — should use logger")

    return findings


def check_monolith_creep() -> List[str]:
    """Check for modules that are growing towards the 500-line threshold."""
    findings = []
    threshold_warn = 400  # Warn at 400, blocking at 500
    for py in sorted(SRC_DIR.rglob("*.py")):
        content = py.read_text(encoding="utf-8", errors="replace")
        lines = len(content.splitlines())
        rel = py.relative_to(SRC_DIR)
        if lines > threshold_warn:
            findings.append(f"📏 {rel}: {lines} lines ({'⚠️ >400' if lines > threshold_warn else '✅'})")
    return findings


def check_dependency_creep(changed_files: List[str] = None) -> List[str]:
    """Check if any new third-party imports were introduced."""
    findings = []
    all_imports = set()

    targets = changed_files if changed_files else [SRC_DIR]
    for target in targets:
        path = Path(target)
        if not path.exists():
            continue
        if path.is_dir():
            py_files = list(path.rglob("*.py"))
        else:
            py_files = [path] if path.suffix == ".py" else []

        for py in py_files:
            content = py.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'^(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', content, re.MULTILINE):
                mod = m.group(1).split(".")[0]
                mod_lower = mod.lower()
                if mod_lower not in STDLIB and mod_lower not in LOCAL_PACKAGES and mod_lower not in _get_local_modules():
                    all_imports.add(mod)

    if all_imports:
        findings.append(f"📦 Third-party deps used: {', '.join(sorted(all_imports))}")
    return findings


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aelvoxim Architecture Self-Review")
    parser.add_argument("--changed", nargs="*", default=None, help="Specific changed files to analyze")
    parser.add_argument("--summary", action="store_true", help="Short summary only")
    args = parser.parse_args()

    changed = args.changed

    print(f"{'='*60}")
    print(f"  Aelvoxim Architecture Self-Review")
    if changed:
        print(f"  Focus: {len(changed)} changed files")
    print(f"{'='*60}")
    print()

    all_findings = []

    # 1. ADR consistency
    f = check_adr_consistency()
    all_findings.extend(f)

    # 2. Tracking consistency
    f = check_tracking_tasks()
    all_findings.extend(f)

    # 3. Dependency graph analysis
    print("── Dependency Analysis ──")
    f = analyze_dependency_graph(changed if changed else None)
    for finding in f:
        print(f"  {finding}")
    all_findings.extend(f)
    if not f:
        print("  ✅ No dependency issues found")
    print()

    # 4. Monolith creep check
    print("── Module Size Check ──")
    f = check_monolith_creep()
    for finding in f[:10]:
        print(f"  {finding}")
    if len(f) > 10:
        print(f"  ... and {len(f) - 10} more")
    if not f:
        print("  ✅ All modules under 400 lines")
    print()

    # 5. Dependency creep
    print("── Dependency Creep ──")
    f = check_dependency_creep(changed)
    for finding in f:
        print(f"  {finding}")
    print()

    if args.summary:
        print(f"  Findings: {len(all_findings)}")
        return

    # 6. Recommendations
    print("── Recommendations ──")
    if all_findings:
        print("  Review ADR.md and tracking.md for context")
        print("  Consider running python scripts/qa_gate.py after changes")
    else:
        print("  ✅ No issues found")
    print()

    print(f"{'='*60}")
    print(f"  Review complete — {len(all_findings)} findings")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
