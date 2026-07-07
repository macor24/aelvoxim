#!/usr/bin/env python3
"""
Aelvoxim 代码质量检查 — 改完代码后跑这个。

用法:
  python scripts/lint_check.py
  python scripts/lint_check.py --api=http://127.0.0.1:9701
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "src"
SERVER_HTML_DIR = SRC / "aelvoxim" / "server"
UI_DIR = SRC / "aelvoxim" / "ui"
PASS = 0
FAIL = 0


def p(text=""):
    print(text, flush=True)


def ok(msg):
    global PASS
    PASS += 1
    p(f"  ✓ {msg}")


def fail(msg, detail=""):
    global FAIL
    FAIL += 1
    p(f"  ✗ {msg}")
    if detail:
        for line in detail.strip().split("\n"):
            p(f"    {line}")


def check_html():
    p("\n" + "=" * 50)
    p("  HTML 文件检查")
    p("=" * 50)
    files = []
    if SERVER_HTML_DIR.exists():
        files.extend(SERVER_HTML_DIR.glob("*.html"))
    if UI_DIR.exists():
        files.extend(UI_DIR.glob("*.html"))
    if not files:
        ok("无 HTML 文件")
        return
    for f in sorted(files):
        content = f.read_text(encoding="utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", content, re.DOTALL)
        ok_flag = True
        for idx, s in enumerate(scripts):
            if s.count("{") != s.count("}"):
                fail(f"{f.name}: JS#大括号不平衡")
                ok_flag = False
            if s.count("(") != s.count(")"):
                fail(f"{f.name}: JS#圆括号不平衡")
                ok_flag = False
        for tag in ["script", "form"]:
            opens = len(re.findall(f"<{tag}[\\s>]", content))
            closes = content.count(f"</{tag}>")
            if opens != closes:
                fail(f"{f.name}: <{tag}> 标签不匹配 (开={opens}, 关={closes})")
                ok_flag = False
        if ok_flag:
            ok(f"{f.name}")


def check_imports():
    p("\n" + "=" * 50)
    p("  Python 导入检查")
    p("=" * 50)
    if not SRC.exists():
        fail("src/ 目录不存在")
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    modules = [
        "aelvoxim",
        "aelvoxim.server.auth",
        "aelvoxim.server.routes",
        "aelvoxim.server.routes_system",
    ]
    for mod in modules:
        try:
            r = subprocess.run(
                [sys.executable, "-c", f"import {mod}; print('ok')"],
                capture_output=True, text=True, timeout=15,
                env=env, cwd=BASE, stdin=subprocess.DEVNULL,
            )
            if r.returncode == 0:
                ok(f"import {mod}")
            else:
                fail(f"import {mod}", r.stderr[:300])
        except subprocess.TimeoutExpired:
            fail(f"import {mod} — 超时")


def check_api(api_base):
    p("\n" + "=" * 50)
    p(f"  API 冒烟测试 — {api_base}")
    p("=" * 50)
    try:
        r = urllib.request.urlopen(f"{api_base}/v1/health", timeout=5)
        if r.status == 200:
            ok("GET /v1/health")
        else:
            fail(f"GET /v1/health → {r.status}")
            return
    except Exception as e:
        fail(f"GET /v1/health — {e}")
        return
    try:
        data = json.dumps({"email": "gmxchz@126.com", "password": "admin123"}).encode()
        req = urllib.request.Request(
            f"{api_base}/v1/auth/login", data=data,
            headers={"Content-Type": "application/json"},
        )
        r = urllib.request.urlopen(req, timeout=5)
        key = json.loads(r.read()).get("api_key", "")
        if key:
            ok("POST /v1/auth/login")
        else:
            fail("POST /v1/auth/login — 无 api_key")
            return
    except Exception as e:
        fail(f"POST /v1/auth/login — {e}")
        return
    headers = {"Authorization": f"Bearer {key}"}
    endpoints = [
        ("GET", "/v1/admin/users", "GET /v1/admin/users"),
        ("GET", "/v1/admin/stats", "GET /v1/admin/stats"),
        ("GET", "/v1/config", "GET /v1/config"),
        ("GET", "/v1/admin/panel", "GET /v1/admin/panel (no auth)"),
        ("GET", "/v1/user/me", "GET /v1/user/me"),
    ]
    for method, path, label in endpoints:
        try:
            h = {} if path == "/v1/admin/panel" else headers
            r = urllib.request.Request(f"{api_base}{path}", headers=h)
            resp = urllib.request.urlopen(r, timeout=5)
            if resp.status == 200:
                ok(label)
            else:
                fail(f"{label} → {resp.status}")
        except urllib.error.HTTPError as e:
            fail(f"{label} → {e.code}")
        except Exception as e:
            fail(f"{label} — {e}")


if __name__ == "__main__":
    api_base = None
    for arg in sys.argv[1:]:
        if arg.startswith("--api="):
            api_base = arg.split("=", 1)[1]

    p("=" * 50)
    p("  Aelvoxim 代码质量检查")
    p(f"  项目: {BASE}")
    p("=" * 50)

    check_html()
    check_imports()

    if api_base:
        check_api(api_base)
    else:
        try:
            r = urllib.request.urlopen("http://127.0.0.1:9701/v1/health", timeout=2)
            if r.status == 200:
                check_api("http://127.0.0.1:9701")
        except Exception:
            p("\n" + "=" * 50)
            p("  API 冒烟测试 — 跳过（服务未运行）")
            p("=" * 50)

    p("\n" + "=" * 50)
    if FAIL == 0:
        p(f"  全部通过 ({PASS}/{PASS})")
    else:
        p(f"  通过 {PASS}, 失败 {FAIL}")
    p("=" * 50)
    sys.exit(0 if FAIL == 0 else 1)
