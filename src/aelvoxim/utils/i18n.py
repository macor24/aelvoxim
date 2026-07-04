"""aelvoxim.utils.i18n — Internationalization

English-first, multilingual ready.
Add new languages by extending _STRINGS with additional language keys.
"""

from typing import Dict

_STRINGS: Dict[str, Dict[str, str]] = {
    # ── General ──
    "initialized": {"en": "Initialized", "zh": "已初始化"},
    "started": {"en": "Started", "zh": "已启动"},
    "stopped": {"en": "Stopped", "zh": "Stopped"},
    "success": {"en": "Success", "zh": "Success"},
    "failed": {"en": "Failed", "zh": "Failure"},
    "unknown": {"en": "Unknown", "zh": "未知"},

    # ── Learner ──
    "learner.initialized": {"en": "Learner initialized", "zh": "学习引擎已初始化"},
    "learner.add_direction": {"en": "Added learning direction: {topic}", "zh": "添加学习方向: {topic}"},
    "learner.remove_direction": {"en": "Removed learning direction: {topic}", "zh": "移除学习方向: {topic}"},
    "learner.loop_started": {"en": "Learning loop started", "zh": "学习循环已启动"},
    "learner.loop_stopped": {"en": "Learning loop stopped", "zh": "学习循环Stopped"},
    "learner.idle": {"en": "No active directions, waiting...", "zh": "无活跃学习方向，Waiting中..."},
    "learner.all_completed": {"en": "All directions completed, submitting verification...", "zh": "所有学习方向已Done，提交验证..."},
    "learner.auto_discover": {"en": "Auto-discover added: {topic}", "zh": "自动发现添加: {topic}"},
    "learner.decompose": {"en": "Decomposed {topic} into {n} sub-tasks", "zh": "将「{topic}」分解为 {n} 个子任务"},

    # ── Knowledge ──
    "knowledge.stored": {"en": "Knowledge stored: {title}", "zh": "知识已存储: {title}"},
    "knowledge.verified": {"en": "Verified: {title} (score={score:.2f})", "zh": "已验证: {title} (评分={score:.2f})"},
    "knowledge.rejected": {"en": "Rejected: {title} (score={score:.2f})", "zh": "已拒绝: {title} (评分={score:.2f})"},

    # ── SelfModel ──
    "selfmodel.snapshot": {"en": "SelfModel snapshot taken", "zh": "SelfModel 快照已生成"},

    # ── Monitor ──
    "monitor.fix": {"en": "Self-heal: {action}", "zh": "自修复: {action}"},
    "monitor.search_fixed": {"en": "Search engine fixed: mock → {engine}", "zh": "搜索引擎已修复: mock → {engine}"},
    "monitor.directions_added": {"en": "Added {n} new directions from knowledge base", "zh": "从知识库添加了 {n} 个新方向"},

    # ── Search ──
    "search.no_results": {"en": "No results found for: {query}", "zh": "未找到搜索结果: {query}"},
    "search.error": {"en": "Search failed: {error}", "zh": "搜索Failure: {error}"},
}

_CURRENT_LANG: str = "en"


def set_lang(lang: str) -> None:
    """Set current language. Supported: en, zh."""
    global _CURRENT_LANG
    if lang in ("en", "zh"):
        _CURRENT_LANG = lang


def get_lang() -> str:
    return _CURRENT_LANG


def _(key: str, **kwargs) -> str:
    """Translate a key with optional format args. Falls back to key if not found."""
    entry = _STRINGS.get(key)
    if entry:
        text = entry.get(_CURRENT_LANG) or entry.get("en", key)
    else:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except KeyError:
            pass  # non-critical, continue
    return text
