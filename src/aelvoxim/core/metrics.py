"""学习闭环的指标：置信度加权产出 / 验证通过率 / A-B 分组。

2026-10-22：原来的"条目/轮"被每方向每天 5 次的速率限制锁死，量不出质量差异。
这里改成置信度加权：W = Σ(新条目 confidence)，能区分"少而准"和"多而糙"。
只读 PostgreSQL，不写任何东西。
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

DSN_ENV = "AELVOXIM_DATABASE_URL"


def _connect():
    import os
    import psycopg2  # type: ignore
    dsn = os.environ.get(DSN_ENV) or (
        "host=127.0.0.1 port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass")
    return psycopg2.connect(dsn)


def fetch_daily(days: int = 7) -> List[Dict[str, Any]]:
    """按 (方向, 日) 取产出：条数、置信度之和、已验证数。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT d.topic,
                   date_trunc('day', e.created_at)::date AS day,
                   count(*) AS n,
                   coalesce(sum(e.confidence), 0) AS w,
                   count(*) FILTER (WHERE e.validated) AS validated
            FROM learning_directions d
            JOIN knowledge_entries e ON e.topic = d.topic
            WHERE d.status = 'active'
              AND e.created_at > now() - (%s || ' days')::interval
            GROUP BY 1, 2
            """,
            (int(days),),
        )
        return [
            {"topic": r[0], "day": r[1].isoformat() if isinstance(r[1], date) else str(r[1]),
             "n": int(r[2]), "w": float(r[3]), "validated": int(r[4])}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """把行聚成：总量、按日、按方向、通过率。"""
    by_day: Dict[str, Dict[str, float]] = {}
    by_dir: Dict[str, Dict[str, float]] = {}
    for r in rows:
        d = by_day.setdefault(r["day"], {"n": 0, "w": 0.0, "validated": 0})
        d["n"] += r["n"]
        d["w"] += r["w"]
        d["validated"] += r["validated"]
        t = by_dir.setdefault(r["topic"], {"n": 0, "w": 0.0, "validated": 0})
        t["n"] += r["n"]
        t["w"] += r["w"]
        t["validated"] += r["validated"]
    n_total = sum(v["n"] for v in by_day.values())
    v_total = sum(v["validated"] for v in by_day.values())
    return {
        "days": sorted(by_day),
        "by_day": {k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in by_day.items()},
        "by_direction": {k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in by_dir.items()},
        "n_total": n_total,
        "w_total": round(sum(v["w"] for v in by_day.values()), 3),
        "pass_rate": round(v_total / n_total, 3) if n_total else 0.0,
    }


def arm_of(topic: str) -> str:
    """按方向名的 hash 奇偶固定分组（A=实验组，B=对照组），避免选择性偏差。"""
    h = hashlib.sha1((topic or "").encode("utf-8")).hexdigest()
    return "A" if int(h[:8], 16) % 2 == 0 else "B"


def by_arm(rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {"A": {"n": 0, "w": 0.0, "validated": 0},
                                        "B": {"n": 0, "w": 0.0, "validated": 0}}
    for r in rows:
        a = arm_of(r["topic"])
        out[a]["n"] += r["n"]
        out[a]["w"] += r["w"]
        out[a]["validated"] += r["validated"]
    return out


def mann_whitney_p(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """双侧 Mann-Whitney U 的正态近似 p 值（样本少时仅作参考）。"""
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return None
    combined = [(v, 0) for v in a] + [(v, 1) for v in b]
    combined.sort(key=lambda x: x[0])
    ranks: Dict[int, float] = {}
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    ra = sum(ranks[i] for i, (_, g) in enumerate(combined) if g == 0)
    ua = ra - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    sd = (na * nb * (na + nb + 1) / 12.0) ** 0.5
    if sd == 0:
        return None
    z = abs(ua - mu) / sd
    # 正态近似双侧 p
    import math
    return round(math.erfc(z / math.sqrt(2)), 4)
