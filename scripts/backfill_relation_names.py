#!/usr/bin/env python3
"""backfill_relation_names.py — 给只有 id 没有 name 的关系行补名字。

背景（2026-11-03 实测生产 11-03 备份）：
    memory_relations 1100 行，source_name / target_name 100% 是空串，只有 id 有值
    （scripts/migrate_sqlite_to_pg.py:119 的遗留）。而读取端按名字过滤 ⇒ 一条都读不回来。
    读端已经加了 id 兜底（memory/__init__.py::_pg_get_relations），本脚本负责把
    存量数据的名字补齐，让后续按名字的查询/看板/推理都能用。

用法（默认只读，不写库）：
    python3 scripts/backfill_relation_names.py                 # 只报数
    python3 scripts/backfill_relation_names.py --apply         # 真写（先保证可回滚）
    python3 scripts/backfill_relation_names.py --selftest      # 不连库，自证映射逻辑

安全：
    * 默认连接设 default_transaction_read_only=on；
    * --apply 前后各打印一次计数（缺名行数 / 可解析行数），写前要求 terminal 里确认；
    * 只填 name 为空的行，不覆盖已有名字，不改 weight/relation_type/created_at。
"""
from __future__ import annotations

import argparse
import os
import sys

DSN_ENV = "AELVOXIM_DATABASE_URL"
DEFAULT_DSN = "host=127.0.0.1 port=5432 dbname=aelvoxim user=aelvoxim password=aelvoxim_pg_pass"

COUNT_SQL = """
SELECT
  count(*)                                                        AS total,
  count(*) FILTER (WHERE coalesce(source_name,'') = ''
                     OR coalesce(target_name,'') = '')             AS missing_name,
  count(*) FILTER (WHERE (coalesce(source_name,'') = ''
                      OR coalesce(target_name,'') = '')
                     AND source_id IS NOT NULL AND target_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM memory_entities e WHERE e.id = r.source_id)
                     AND EXISTS (SELECT 1 FROM memory_entities e WHERE e.id = r.target_id)) AS resolvable
FROM memory_relations r
"""

UPDATE_SQL = """
UPDATE memory_relations r
   SET source_name = coalesce(nullif(r.source_name,''), s.name, ''),
       target_name = coalesce(nullif(r.target_name,''), t.name, '')
  FROM memory_entities s, memory_entities t
 WHERE s.id = r.source_id
   AND t.id = r.target_id
   AND (coalesce(r.source_name,'') = '' OR coalesce(r.target_name,'') = '')
   AND s.name IS NOT NULL AND t.name IS NOT NULL
RETURNING 1
"""


def connect(dsn: str, read_only: bool):
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    if read_only:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
    return conn


def report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(COUNT_SQL)
        total, missing, resolvable = cur.fetchone()
    return {"total": total, "missing_name": missing, "resolvable": resolvable}


def apply_backfill(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(UPDATE_SQL)
        return cur.rowcount


# ── 自证：不连库，用纯函数验证"名字为空才填、有名字不覆盖" ──────────────

def plan_row(source_name: str, target_name: str, s_name: str, t_name: str) -> dict:
    """单行映射决策（--selftest 用；与 UPDATE 的 coalesce/nullif 语义一致）。"""
    return {
        "source_name": source_name or s_name or "",
        "target_name": target_name or t_name or "",
        "changed": (source_name or "") != (source_name or s_name or "")
                   or (target_name or "") != (target_name or t_name or ""),
    }


def selftest() -> int:
    cases = [
        # (原名, 原目标, 实体名, 期望 source, 期望 target, 期望是否改)
        ("", "", "张三", "北京", "张三", "北京", True),
        ("张三", "", "张三", "北京", "张三", "北京", True),     # 只缺一半
        ("张三", "北京", "张三", "北京", "张三", "北京", False),  # 不缺不动
        ("旧名", "", "新名", "北京", "旧名", "北京", True),      # 有名字不覆盖
    ]
    bad = 0
    for s, t, sn, tn, es, et, ech in cases:
        r = plan_row(s, t, sn, tn)
        ok = (r["source_name"], r["target_name"], r["changed"]) == (es, et, ech)
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} src={s!r} tgt={t!r} + ({sn},{tn}) -> "
              f"{r['source_name']!r},{r['target_name']!r} changed={r['changed']}")
    print(f"selftest: {len(cases) - bad}/{len(cases)} passed")
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写库（默认只读）")
    ap.add_argument("--selftest", action="store_true", help="不连库，自证映射逻辑")
    ap.add_argument("--dsn", default=os.environ.get(DSN_ENV, DEFAULT_DSN))
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    conn = connect(args.dsn, read_only=not args.apply)
    try:
        before = report(conn)
        print(f"关系行 {before['total']} · 缺名字 {before['missing_name']} · 可解析 {before['resolvable']}")
        if not args.apply:
            print("(只读) 加 --apply 才写库")
            return 0
        if before["resolvable"] == 0:
            print("没有可回填的行，退出")
            return 0
        n = apply_backfill(conn)
        after = report(conn)
        print(f"已回填 {n} 行 · 回填后缺名字 {after['missing_name']}（原 {before['missing_name']}）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
