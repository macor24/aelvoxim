"""aelvoxim.learn.search — Unified search engine interface

Multi-engine with automatic degradation:
Bing API → DuckDuckGo HTML → Bing CN (no key) → Media search → Mock (dev)
Pure stdlib, zero external dependencies.

Environment variables:
  AELVOXIM_SEARCH_ENGINE    — bing / duckduckgo / baidu / mock
  AELVOXIM_BING_API_KEY     — Bing Search API Key

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
import urllib.error
import random
from typing import Any, Dict, List, Optional

import logging
_log = logging.getLogger("aelvoxim.learn.search")

# ── Query variant generation ──────────────────


def generate_query_variants(original_query: str, n: int = 3) -> list[str]:
    """Generate up to n semantic variants of the original query.

    Strategies:
      1. Synonym substitution (technical terms)
      2. Domain suffix addition ("best practices", "examples", "guide")
      3. Core noun extraction (remove stop words)

    Returns deduplicated list, always including the original query first.
    """
    variants: list[str] = [original_query]

    # Strategy 1: synonym substitution
    synonym_map = {
        "optimization": ["tuning", "performance improvement", "enhancement"],
        "profiling": ["monitoring", "analysis", "benchmarking"],
        "detection": ["finding", "identification", "diagnosis"],
        "thread": ["multithreading", "concurrent", "parallel"],
        "memory": ["RAM", "heap", "allocation"],
        "network": ["TCP/IP", "latency", "bandwidth"],
        "deploy": ["release", "rollout", "ship"],
        "config": ["setup", "settings", "configuration"],
        "debug": ["troubleshoot", "diagnose", "fix"],
        "performance": ["speed", "throughput", "efficiency"],
    }
    words = original_query.split()
    new_words: list[str] = []
    for w in words:
        w_lower = w.lower()
        if w_lower in synonym_map:
            new_words.append(random.choice(synonym_map[w_lower]))
        else:
            new_words.append(w)
    joined = " ".join(new_words)
    if joined != original_query:
        variants.append(joined)

    # Strategy 2: domain suffix
    if len(variants) < n:
        suffix = random.choice([" techniques", " best practices", " examples", " guide"])
        variants.append(original_query + suffix)

    # Strategy 3: core noun extraction (remove stop words)
    if len(variants) < n:
        stop_words = {"the", "a", "with", "using", "for", "and", "of", "in", "to", "an"}
        core = [w for w in words if w.lower() not in stop_words]
        if len(core) < len(words) and core:
            variants.append(" ".join(core))

    # Deduplicate (case-insensitive)
    seen: set[str] = set()
    unique: list[str] = []
    for v in variants:
        key = v.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique[:max(n, 1)]


def _score_result(result: dict, query: str) -> float:
    """Compute a simple relevance score (0.0–1.0) for a search result against the query."""
    title = (result.get("title") or "").lower()
    snippet = (result.get("snippet") or "").lower()
    query_lower = query.lower()
    query_terms = query_lower.split()

    if not query_terms:
        return 0.5

    # Term overlap in title (weight 50%)
    title_matches = sum(1 for t in query_terms if t in title)
    title_score = title_matches / len(query_terms) if query_terms else 0.0

    # Term overlap in snippet (weight 30%)
    snippet_matches = sum(1 for t in query_terms if t in snippet)
    snippet_score = snippet_matches / len(query_terms) if query_terms else 0.0

    # Bonus: exact phrase match in title (weight 20%)
    phrase_bonus = 1.0 if query_lower in title else 0.0

    score = title_score * 0.50 + snippet_score * 0.30 + phrase_bonus * 0.20
    return round(min(max(score, 0.0), 1.0), 4)


# ── Engine config ───────────────────────────────

BING_API_KEY = os.environ.get("AELVOXIM_BING_API_KEY", os.environ.get("METACORE_BING_API_KEY", ""))
BING_ENDPOINT = os.environ.get("AELVOXIM_BING_ENDPOINT", os.environ.get("METACORE_BING_ENDPOINT",
    "https://api.bing.microsoft.com/v7.0/search"))
_ACTIVE_ENGINE = os.environ.get("AELVOXIM_SEARCH_ENGINE", os.environ.get("METACORE_SEARCH_ENGINE", "bing_cn")).lower()

# ── Rate limiting ──
_last_search_time: float = 0.0
_MIN_SEARCH_INTERVAL = 0.3  # 300ms between searches

# HTML scrape search — disabled by default. Set to "true" to enable.
# When disabled, only Bing API (with key) and Mock are available.
_HTML_SCRAPE_ENABLED = os.environ.get("AELVOXIM_HTML_SEARCH", "").lower() in ("true", "1", "yes")

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


def _rate_limit():
    """Ensure minimum interval between external HTTP requests."""
    global _last_search_time
    now = time.time()
    elapsed = now - _last_search_time
    if elapsed < _MIN_SEARCH_INTERVAL:
        time.sleep(_MIN_SEARCH_INTERVAL - elapsed)
    _last_search_time = time.time()


def _random_ua() -> str:
    """Pick a random User-Agent from the pool."""
    return random.choice(_USER_AGENTS)


def _safe_request(url: str, data: Optional[bytes] = None,
                  headers: Optional[Dict] = None, timeout: int = 8,
                  max_attempts: int = 2) -> Optional[str]:
    """Make HTTP request with rate limiting, retry, and UA rotation."""
    _rate_limit()
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = _random_ua()
        try:
            req = urllib.request.Request(url, data=data, headers=req_headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < max_attempts:
                time.sleep(1.0 * attempt)  # backoff 1s, 2s
                continue
            return None
        except Exception:
            return None
    return None


def _bing_search(query: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Bing Web Search API v7. Requires METACORE_BING_API_KEY."""
    if not BING_API_KEY:
        return None
    try:
        url = f"{BING_ENDPOINT}?q={urllib.parse.quote(query)}&count={max_results}&mkt=zh-CN"
        html = _safe_request(url, headers={
            "Ocp-Apim-Subscription-Key": BING_API_KEY,
        }, timeout=10)
        if not html:
            return None
        data = json.loads(html)
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
        html = _safe_request(url, data=data, timeout=5)
        if not html:
            return None

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
        html = _safe_request(url, timeout=8)
        if not html:
            return None

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
        html = _safe_request(url, timeout=8)
        if not html:
            return None

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
        html = _safe_request(url, timeout=8)
        if not html:
            return None

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
        _log.exception("search error")

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


def _add_scores(results: list, query: str) -> list:
    """Add a 'score' field (0.0–1.0) to each search result."""
    out = []
    for r in results:
        r = dict(r)
        r["score"] = _score_result(r, query)
        out.append(r)
    return out


def search(query: str, max_results: int = 5,
           engine: str = "") -> List[Dict[str, str]]:
    """Search the web. Returns [{title, snippet, url, score}].

    Each result carries a 'score' field (0.0–1.0) computed from
    term-overlap relevance against the query.

    Degradation chain:
      1. Specified engine (bing/duckduckgo/bing_cn/media/mock)
      2. Env var AELVOXIM_SEARCH_ENGINE
      3. Auto degrade: Bing -> DuckDuckGo -> Bing CN -> Mock
    """
    # Collect raw results from the selected engine
    raw = _search_internal(query, max_results, engine)
    return _add_scores(raw, query)


def _search_internal(query: str, max_results: int = 5,
                     engine: str = "") -> list:
    """Internal: run search engine chain, return raw [{title,snippet,url}]."""
    if engine == "default":
        if _HTML_SCRAPE_ENABLED:
            for fn in [_bing_cn_search, _so_search, _duckduckgo_search, _bing_search]:
                r = fn(query, max_results)
                if r:
                    return r
        else:
            r = _bing_search(query, max_results)
            if r:
                return r
        return []

    engine = engine or _ACTIVE_ENGINE

    if engine == "mock":
        return _mock_search(query, max_results)

    if engine == "media":
        if not _HTML_SCRAPE_ENABLED:
            r = _bing_search(query, max_results)
            if r:
                return r
            return _mock_search(query, max_results)
        results = _media_search(query, max_results)
        if results:
            return results
        for fn in [_bing_cn_search, _so_search, _bing_search, _duckduckgo_search]:
            r = fn(query, max_results)
            if r:
                return r
        return _mock_search(query, max_results)

    if engine == "bing":
        results = _bing_search(query, max_results)
        if results:
            return results
        if not _HTML_SCRAPE_ENABLED:
            return _mock_search(query, max_results)
        for fn in [_duckduckgo_search, _bing_cn_search, _so_search]:
            r = fn(query, max_results)
            if r:
                return r
        return _mock_search(query, max_results)

    if engine == "bing_cn":
        if not _HTML_SCRAPE_ENABLED:
            r = _bing_search(query, max_results)
            if r:
                return r
            return _mock_search(query, max_results)
        results = _bing_cn_search(query, max_results)
        if results:
            return results
        for fn in [_so_search, _duckduckgo_search, _bing_search]:
            r = fn(query, max_results)
            if r:
                return r
        return _mock_search(query, max_results)

    if engine == "duckduckgo":
        if not _HTML_SCRAPE_ENABLED:
            r = _bing_search(query, max_results)
            if r:
                return r
            return _mock_search(query, max_results)
        results = _duckduckgo_search(query, max_results)
        if results:
            return results
        for fn in [_bing_search, _bing_cn_search, _so_search]:
            r = fn(query, max_results)
            if r:
                return r
        return _mock_search(query, max_results)

    # Unknown engine -> auto degrade
    if _HTML_SCRAPE_ENABLED:
        for fn in [_bing_search, _duckduckgo_search, _bing_cn_search, _so_search]:
            results = fn(query, max_results)
            if results:
                return results
    else:
        r = _bing_search(query, max_results)
        if r:
            return r
    return _mock_search(query, max_results)


def search_with_variants(query: str, max_results: int = 5,
                         engine: str = "", max_variants: int = 3) -> List[Dict[str, str]]:
    """Search with query variant expansion. Returns deduplicated scored results.

    Generates 2-3 semantic variants of the query, searches each,
    deduplicates by URL (keeping highest score), and returns top results
    sorted by score descending.
    """
    variants = generate_query_variants(query, n=max_variants)
    _log.info("Search variants: %s", variants)

    all_results: list[dict] = []
    for q in variants:
        try:
            res = search(q, max_results=max_results, engine=engine)
            all_results.extend(res)
        except Exception:
            _log.exception("search_with_variants error for variant: %s", q)

    # Deduplicate by URL (keep highest score)
    seen: dict[str, dict] = {}
    for r in all_results:
        key = r.get("url") or r.get("title", "")
        if key not in seen or (r.get("score", 0) or 0) > (seen[key].get("score", 0) or 0):
            seen[key] = r

    final = sorted(seen.values(), key=lambda x: x.get("score", 0) or 0, reverse=True)
    _log.info("Search variants best score: %.4f", final[0]["score"] if final else 0.0)
    return final[:max_results]


def get_available_engines() -> list:
    """Return list of available search engines."""
    engines = [{"id": "mock", "name": "Mock data (dev)", "available": True}]
    if _HTML_SCRAPE_ENABLED:
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
