"""aelvoxim.core.self_review — 对话自我审查模块

当前是规则引擎实现（关键词匹配 + 阈值打分），后续可替换为 LLM 评估。
在 chat_pipeline 的 Phase 13（memory storage）之后插入 hook_review() 即可激活。

hook_review() 接口:
    参数:
        conversation_id: str       — 会话 ID
        user_question: str         — 用户提问原文
        assistant_response: str    — AI 回答原文
        store_fn: callable         — 存储函数，接收 dict，可选
        user_feedback: str | None  — 用户反馈（如点赞/踩）
    返回:
        dict — {"scores": {...}, "overall_score": float, "weaknesses": [...], "improvement_plan": [...]}
"""
from typing import Callable, Optional

from .self_review_system import SelfReviewSystem

# 模块级单例
_reviewer: Optional[SelfReviewSystem] = None


def _get_reviewer(store_fn: Optional[Callable] = None) -> SelfReviewSystem:
    global _reviewer
    if _reviewer is None:
        class _MemAdapter:
            """将 store_fn 包装成 SelfReviewSystem 需要的 memory_interface"""
            def __init__(self, fn):
                self._fn = fn
            def store(self, data):
                if self._fn:
                    self._fn(data)
            def query(self, **kwargs):
                return []
        _reviewer = SelfReviewSystem(memory_interface=_MemAdapter(store_fn))
    return _reviewer


def hook_review(
    conversation_id: str,
    user_question: str,
    assistant_response: str,
    store_fn: Optional[Callable] = None,
    user_feedback: Optional[str] = None,
) -> dict:
    """对话自我审查 —— 评估回答质量，返回评分和改进建议。

    调用方只需传入对话内容，结果可存可不存。
    """
    reviewer = _get_reviewer(store_fn)
    return reviewer.review_conversation(
        conversation_id=conversation_id,
        user_question=user_question,
        assistant_responses=[assistant_response],
        user_feedback=user_feedback,
    )
