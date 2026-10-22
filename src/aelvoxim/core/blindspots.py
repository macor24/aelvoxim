"""盲点统计：用户问了、但知识检索空手而归。

2026-10-21：自省的 9 个信号全部来自系统自己的账本，看不见"答没答上"。
本模块只做统计，不做判断，也不调 LLM：
  - 读 chat_monitor/*.jsonl（每轮对话一条）
  - 空手 = knowledge.hit 为假 或 hit_count == 0
  - 输出总数、空手数、空手率、Top 缺口主题、Top 空手问句（前 60 字）
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

_STOP_CJK = {"怎么", "什么", "如何", "为什么", "可以", "需要", "一个", "这个", "那个", "现在"}
_STOP_EN = {"about", "after", "again", "their", "there", "these", "those", "which", "while",
            "would", "should", "could", "where", "what", "when", "with", "from", "into",
            "your", "yours", "have", "does", "doing", "make", "using", "please", "still",
            "just", "like", "some", "such", "than", "then", "them", "this", "that"}


def _monitor_dir() -> Path:
    from ..utils import METACORE_DIR
    return METACORE_DIR / "chat_monitor"


def _records(hours: float) -> List[Dict[str, Any]]:
    cutoff = time.time() - hours * 3600
    out: List[Dict[str, Any]] = []
    d = _monitor_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("timestamp")
                if isinstance(ts, (int, float)) and float(ts) >= cutoff:
                    out.append(r)
        except Exception:
            continue
    return out


def _tokens(text: str) -> List[str]:
    """CJK 二元组 + 英文长词（去停用词），不依赖分词器。"""
    text = (text or "").lower()
    toks = [t for t in re.findall(r"[a-z]{5,}", text) if t not in _STOP_EN]
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in cjk:
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in _STOP_CJK:
                toks.append(bg)
    return toks


def stats(hours: float = 24.0) -> Dict[str, Any]:
    """近 N 小时的检索盲点统计。空数据返回 total=0，不抛异常。"""
    recs = _records(hours)
    total = len(recs)
    miss = 0
    topic_counter: Counter = Counter()
    query_counter: Counter = Counter()
    for r in recs:
        k = r.get("knowledge") or {}
        is_miss = (not k.get("hit")) or int(k.get("hit_count") or 0) == 0
        if not is_miss:
            continue
        miss += 1
        qh = str(r.get("query_head") or "").strip()
        if qh:
            query_counter[qh[:40]] += 1
            for t in _tokens(qh)[:6]:
                topic_counter[t] += 1
        for t in (k.get("topics") or [])[:3]:
            if t:
                topic_counter[str(t)[:40]] += 1
    return {
        "window_hours": hours,
        "total": total,
        "miss": miss,
        "miss_ratio": round(miss / total, 3) if total else 0.0,
        "topics": [{"topic": t, "n": n} for t, n in topic_counter.most_common(5)],
        "top_queries": [q for q, _ in query_counter.most_common(5)],
    }
