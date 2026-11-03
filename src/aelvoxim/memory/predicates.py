# SPDX-License-Identifier: MIT
"""memory.predicates — 受控谓词表。

为什么要这张表（2026-11-03 实测）：
    memory_relations 里 1100 条边，1099 条是 `discussed`（user → topic 的聊天碎片），
    0 条语义边 ⇒ 图既不能推理也不能出题。没有受控谓词，边就是日志。

三条口径：
    1. CANONICAL  里的谓词才是"知识边"，可以被推理/闭包使用；
    2. NON_KNOWLEDGE 是共现/交互痕迹，保留但不进推理（默认已被 1100 条污染证明）；
    3. ALIASES 负责把历史写法归一，避免 is-a / isa / 属于 变成三种边。

改谓词表 = 改口径：新谓词必须同时写进 TRANSITIVE（能否传递）与测试。
"""
from __future__ import annotations

from typing import Dict, Set

# 语义谓词：可推理。transitive=True 才允许参与传递闭包。
CANONICAL: Dict[str, bool] = {
    "is_a": True,          # A 是一种 B（含 subclass/instance，历史别名统一到这里）
    "part_of": True,       # A 是 B 的一部分
    "has_part": True,
    "member_of": True,
    "has_member": True,
    "located_in": True,
    "prerequisite": True,  # A 是 B 的前置条件
    "depends_on": True,
    "causes": False,       # 因果不传递（A→B→C 不等于 A→C）
    "uses": False,
    "works_at": False,
    "likes": False,
    "feels": False,
    "owns": False,
}

TRANSITIVE: Set[str] = {p for p, t in CANONICAL.items() if t}

# 非知识边：共现/交互痕迹。写入照旧（要留历史），但推理与出题一律不用。
NON_KNOWLEDGE: Set[str] = {
    "discussed", "mentioned", "related", "associated", "buy", "viewed", "clicked", "co_occur",
}

ALIASES: Dict[str, str] = {
    "isa": "is_a", "is-a": "is_a", "instance_of": "is_a", "subclass_of": "is_a",
    "type_of": "is_a", "kind_of": "is_a", "belong_to": "is_a",
    "属于": "is_a", "是一种": "is_a", "是一个": "is_a",
    "包含": "has_part", "含有": "has_part", "组成": "part_of",
    "belongs_to": "member_of", "memberof": "member_of",
    "位于": "located_in", "地处": "located_in",
    "前置条件": "prerequisite", "前提": "prerequisite",
    "导致": "causes", "引起": "causes",
    "使用": "uses", "employ": "uses",
}


def canonicalize(pred: str) -> str:
    """把谓词归一成受控表中的写法；不认识的保持原样（小写、去空白）。"""
    p = (pred or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(p, p)


def is_knowledge_edge(pred: str) -> bool:
    """是否知识边（可进推理/出题）。"""
    return canonicalize(pred) in CANONICAL


def is_transitive(pred: str) -> bool:
    return canonicalize(pred) in TRANSITIVE


def classification(pred: str) -> str:
    """knowledge / non_knowledge / unknown —— 读数脚本用它分桶。"""
    p = canonicalize(pred)
    if p in CANONICAL:
        return "knowledge"
    if p in NON_KNOWLEDGE:
        return "non_knowledge"
    return "unknown"


def vocabulary() -> Dict[str, list]:
    return {
        "knowledge": sorted(CANONICAL),
        "transitive": sorted(TRANSITIVE),
        "non_knowledge": sorted(NON_KNOWLEDGE),
    }
