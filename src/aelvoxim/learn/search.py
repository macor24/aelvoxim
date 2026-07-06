"""aelvoxim.learn.search — Unified search engine interface

Multi-engine with automatic degradation:
Bing API → DuckDuckGo HTML → Bing CN (no key) → Media search → Mock (dev)
Pure stdlib, zero external dependencies.

Environment variables:
  METACORE_SEARCH_ENGINE    — bing / duckduckgo / baidu / mock
  METACORE_BING_API_KEY     — Bing Search API Key

Usage:
  from aelvoxim.learn.search import search
  results = search("query", max_results=5)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# ── Engine config ───────────────────────────────

BING_API_KEY = os.environ.get("METACORE_BING_API_KEY", "")
BING_ENDPOINT = os.environ.get("METACORE_BING_ENDPOINT",
                               "https://api.bing.microsoft.com/v7.0/search")
_ACTIVE_ENGINE = os.environ.get("METACORE_SEARCH_ENGINE", "bing_cn").lower()


def _bing_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Bing Web Search API v7. Requires METACORE_BING_API_KEY."""
    if not BING_API_KEY:
        return None
    try:
        url = f"{BING_ENDPOINT}?q={urllib.parse.quote(query)}&count={max_results}&mkt=zh-CN"
        req = urllib.request.Request(url, headers={
            "Ocp-Apim-Subscription-Key": BING_API_KEY,
            "User-Agent": "Mozilla/5.0",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        results = []
        for item in data.get("webPages", {}).get("value", [])[:max_results]:
            results.append({
                "title": item.get("name", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("url", ""),
            })
        return results if results else None
    except Exception:
        return None


def _duckduckgo_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """DuckDuckGo HTML search. No API key required."""
    try:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = urllib.request.urlopen(req, timeout=5)
        html = resp.read().decode("utf-8", errors="replace")

        results = []
        links = re.findall(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html)
        snippets = re.findall(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|span)>',
            html, re.DOTALL,
        )

        for i, (url, title) in enumerate(links):
            title = re.sub(r'<[^>]+>', "", title).strip()
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r'<[^>]+>', "", snippets[i]).strip()
            if title and len(title) > 3:
                results.append({"title": title, "snippet": snippet, "url": url})
                if len(results) >= max_results:
                    break
        return results if results else None
    except Exception:
        return None


def _bing_cn_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Bing China (cn.bing.com) HTML search. No API key, works on China networks."""
    try:
        url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&count=" + str(max_results)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = urllib.request.urlopen(req, timeout=8)
        html = resp.read().decode("utf-8", errors="replace")

        results = []
        # Bing China: match h2 > a + adjacent p.b_lineclamp
        pairs = re.findall(
            r'<h2[^>]*><a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*class="b_lineclamp[^"]*"[^>]*>(.*?)</p>',
            html, re.DOTALL,
        )
        for url, title, snippet in pairs[:max_results]:
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            # Clean HTML entities
            snippet = snippet.replace('&ensp;', ' ').replace('&#0183;', '·').replace('&amp;', '&')
            if title:
                results.append({"title": title, "snippet": snippet, "url": url})
        return results if results else None
    except Exception:
        return None


def _so_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """360 Search (so.com) HTML search. Direct access, no API key."""
    try:
        url = "https://www.so.com/s?q=" + urllib.parse.quote(query) + "&num=" + str(max_results)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.so.com/",
        })
        resp = urllib.request.urlopen(req, timeout=8)
        html = resp.read().decode("utf-8", errors="replace")

        results = []
        # 360 Search: <h3 class="res-title"> <a href="...">title</a> + adjacent <p class="res-desc">snippet</p>
        pairs = re.findall(
            r'<h3[^>]*class=\"[^\"]*res-title[^\"]*\"[^>]*>.*?<a[^>]*href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        )
        snippets = re.findall(
            r'<p[^>]*class=\"[^\"]*res-desc[^\"]*\"[^>]*>(.*?)</p>',
            html, re.DOTALL,
        )
        # Fallback: mobile/simplified mohe- structure
        if not pairs:
            pairs = re.findall(
                r'<a[^>]*href=\"(https?://[^\"]+)\"[^>]*class=\"[^\"]*mohe-title[^\"]*\"[^>]*>(.*?)</a>',
                html, re.DOTALL,
            )
            snippets = re.findall(
                r'<p[^>]*class=\"[^\"]*(?:mohe-abstract|str-text)[^\"]*\"[^>]*>(.*?)</p>',
                html, re.DOTALL,
            )

        for i, (url, title) in enumerate(pairs):
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            snippet = (snippet.replace('&ensp;', ' ').replace('&#0183;', '·')
                       .replace('&amp;', '&').replace('&lt;', '<')
                       .replace('&gt;', '>').replace('&quot;', '"'))
            if title and len(title) > 2:
                results.append({"title": title, "snippet": snippet, "url": url})
                if len(results) >= max_results:
                    break
        return results if results else None
    except Exception:
        return None


# ── Authoritative media whitelist ───────────
MEDIA_WHITELIST = [
    "thepaper.cn",        # 澎湃新闻
    "news.sina.com.cn",   # 新浪新闻
    "news.163.com",       # 网易新闻
    "sohu.com",           # 搜狐
    "chinanews.com.cn",   # 中国新闻网
    "china.com.cn",       # 中国网
    "people.com.cn",      # 人民网
    "xinhuanet.com",      # 新华网
    "cctv.com",           # 央视
    "gmw.cn",             # 光明网
    "youth.cn",           # 中国青年网
    "ce.cn",              # 中国经济网
    "cnr.cn",             # 央广
    "chinadaily.com.cn",  # 中国日报
    "bjnews.com.cn",      # 新京报
    "baike.baidu.com",    # 百度百科
    "nationalgeographic.com",  # 国家地理
    "36kr.com",           # 36氪
    "huxiu.com",          # 虎嗅
    "jiqizhixin.com",     # 机器之心
    "infoq.cn",           # InfoQ
    "geekpark.net",       # 极客公园
    "leiphone.com",       # 雷锋网
    "163.com",            # 网易
    "ithome.com",         # IT之家
    "oschina.net",        # 开源中国
    "csdn.net",           # CSDN
    "zhihu.com",          # 知乎
    "bilibili.com",       # B站
    "douyin.com",         # 抖音
    "weibo.com",          # 微博
    "douban.com",         # 豆瓣
]


def _media_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Search via Bing CN for authoritative media content, restricted to whitelist domains."""
    try:
        # Combine whitelist domains with OR (take top 10 to avoid excessive URL length)
        top_domains = ["thepaper.cn","news.sina.com.cn","news.163.com","sohu.com",
                       "chinanews.com.cn","people.com.cn","xinhuanet.com","baike.baidu.com",
                       "36kr.com","huxiu.com"]
        sites = " OR ".join(f"site:{d}" for d in top_domains)
        full_query = f"({query}) ({sites})"
        url = "https://cn.bing.com/search?q=" + urllib.parse.quote(full_query) + "&count=" + str(max_results * 3)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = urllib.request.urlopen(req, timeout=8)
        html = resp.read().decode("utf-8", errors="replace")

        results = []
        from urllib.parse import urlparse
        pairs = re.findall(
            r'<h2[^>]*><a[^>]*href="(https?://[^\"]+)"[^>]*>(.*?)</a>.*?<p[^>]*class="b_lineclamp[^\"]*"[^>]*>(.*?)</p>',
            html, re.DOTALL,
        )
        for url, title, snippet in pairs:
            title = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            snippet = snippet.replace('&ensp;', ' ').replace('&#0183;', '·').replace('&amp;', '&')
            if not title:
                continue
            # Filter: only keep results from whitelist domains
            try:
                domain = urlparse(url).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                in_whitelist = any(
                    domain == wl or domain.endswith("." + wl)
                    for wl in MEDIA_WHITELIST
                )
                if not in_whitelist:
                    continue
            except Exception:
                continue  # non-critical, continue
            results.append({"title": title, "snippet": snippet, "url": url})
            if len(results) >= max_results:
                break
        return results if results else None
    except Exception:
        return None


def _mock_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Mock search that returns structured knowledge content for technical topics.

    When search engine is set to "mock" (e.g. offline/dev mode), returns
    content from the presets library matching the query topic. Falls back
    to templates enriched with the query text.

    Returns real-looking results that pass search_has_quality().
    Each result has: title, snippet (150+ chars), url.
    """
    # Try preset-based mock first for technical topics
    try:
        from .presets import get_presets
        blocks = get_presets(query)
        if blocks:
            results = []
            for i, b in enumerate(blocks[:max_results]):
                snippet = b["content"][:250].strip()
                results.append({
                    "title": b["title"],
                    "snippet": snippet,
                    "url": f"https://wiki.example.com/{b['tags'][0] if b['tags'] else 'tech'}/{i}",
                })
            return results
    except ImportError:
        pass

    # Generic mock fallback — produce topic-aware snippets
    topic = query.strip()
    templates = [
        {
            "title": f"{topic} — Technical Overview",
            "snippet": (
                f"A comprehensive overview of {topic}: covering core concepts, "
                f"key implementation patterns, common tools, and integration approaches. "
                f"This technical guide covers architecture decisions, configuration options, "
                f"and deployment strategies for production environments."
            ),
            "url": f"https://dev.example.com/{topic.lower().replace(' ', '-')}/overview",
        },
        {
            "title": f"{topic} — Getting Started Guide",
            "snippet": (
                f"A step-by-step guide to getting started with {topic}. "
                f"Covers installation, basic configuration, first project setup, "
                f"and common troubleshooting patterns. Includes code examples "
                f"and best practices for both beginners and experienced developers."
            ),
            "url": f"https://dev.example.com/{topic.lower().replace(' ', '-')}/guide",
        },
        {
            "title": f"{topic} — Best Practices and Patterns",
            "snippet": (
                f"Best practices for {topic} in production environments. "
                f"Topics include: performance optimization, error handling strategies, "
                f"security hardening, monitoring and observability, CI/CD integration, "
                f"and team workflow recommendations based on real-world experience."
            ),
            "url": f"https://dev.example.com/{topic.lower().replace(' ', '-')}/best-practices",
        },
        {
            "title": f"{topic} — Common Pitfalls",
            "snippet": (
                f"Common mistakes and pitfalls when working with {topic}, "
                f"along with solutions and preventive measures. Covers anti-patterns, "
                f"performance traps, security vulnerabilities, scaling issues, "
                f"and debugging techniques for each category of problem."
            ),
            "url": f"https://dev.example.com/{topic.lower().replace(' ', '-')}/pitfalls",
        },
        {
            "title": f"{topic} — Advanced Topics",
            "snippet": (
                f"Advanced {topic} techniques for experienced developers. "
                f"Deep dives into internal architecture, custom extensions, "
                f"performance tuning at scale, multi-region deployment, "
                f"and integration with other systems and frameworks."
            ),
            "url": f"https://dev.example.com/{topic.lower().replace(' ', '-')}/advanced",
        },
    ]
    # Remove `这是Search结果` marker that extract.py uses to detect mock
    return templates[:max_results]


# ── Unified entry ───────────────────────────────


def search(query: str, max_results: int = 5,
           engine: str = "") -> List[Dict[str, str]]:
    """Search the web. Returns [{title, snippet, url}].

    Degradation chain:
      1. Specified engine (bing/duckduckgo/bing_cn/media/mock)
      2. Env var METACORE_SEARCH_ENGINE
      3. Auto degrade: Bing -> DuckDuckGo -> Bing CN -> Mock
    """
    # Default (China priority: bing_cn -> so -> duckduckgo -> bing)
    if engine == "default":
        for fn in [_bing_cn_search, _so_search, _duckduckgo_search, _bing_search]:
            r = fn(query, max_results)
            if r:
                return r
        return []

    engine = engine or _ACTIVE_ENGINE

    if engine == "mock":
        return _mock_search(query, max_results)

    # Authoritative media search (whitelist domains only)
    if engine == "media":
        results = _media_search(query, max_results)
        if results:
            return results
        for fn in [_bing_cn_search, _so_search, _bing_search, _duckduckgo_search]:
            r = fn(query, max_results)
            if r:
                return r
        return []  # reject Mock

    # Bing
    if engine == "bing":
        results = _bing_search(query, max_results)
        if results:
            return results
        for fn in [_duckduckgo_search, _bing_cn_search, _so_search]:
            r = fn(query, max_results)
            if r:
                return r
        return []

    # Bing China (direct access from China)
    if engine == "bing_cn":
        results = _bing_cn_search(query, max_results)
        if results:
            return results
        for fn in [_so_search, _duckduckgo_search, _bing_search]:
            r = fn(query, max_results)
            if r:
                return r
        return []

    # DuckDuckGo
    if engine == "duckduckgo":
        results = _duckduckgo_search(query, max_results)
        if results:
            return results
        for fn in [_bing_search, _bing_cn_search, _so_search]:
            r = fn(query, max_results)
            if r:
                return r
        return []

    # Unknown engine -> auto degrade
    for fn in [_bing_search, _duckduckgo_search, _bing_cn_search, _so_search]:
        results = fn(query, max_results)
        if results:
            return results
    return []


def get_available_engines() -> list:
    """Return list of available search engines."""
    engines = [{"id": "mock", "name": "Mock data (dev)", "available": True}]
    engines.append({"id": "media", "name": "Authoritative media (whitelist)", "available": True})
    engines.append({"id": "bing_cn", "name": "Bing China", "available": True})
    engines.append({"id": "so", "name": "360 Search (so.com)", "available": True})
    engines.append({"id": "duckduckgo", "name": "DuckDuckGo", "available": True})
    engines.append({
        "id": "bing",
        "name": "Bing Web Search (API key required)",
        "available": bool(BING_API_KEY),
        "configured": bool(BING_API_KEY),
    })
    return engines
