"""aelvoxim.core.self_review — Conversation self-review module.

Current implementation: rule-based engine (keyword matching + threshold scoring).
Replaceable with LLM-based evaluation in the future.
Inserted after chat_pipeline Phase 13 (memory storage) via hook_review().

hook_review() API:
    Args:
        conversation_id: str       — Session ID
        user_question: str         — Original user question
        assistant_response: str    — AI response text
        store_fn: callable         — Storage function, receives dict, optional
        user_feedback: str | None  — User feedback (like/dislike)
    Returns:
        dict — {"scores": {...}, "overall_score": float, "weaknesses": [...], "improvement_plan": [...]}
"""
from typing import Callable, Optional

from .self_review_system import SelfReviewSystem

# Module-level singleton
_reviewer: Optional[SelfReviewSystem] = None


def _get_reviewer(store_fn: Optional[Callable] = None) -> SelfReviewSystem:
    global _reviewer
    if _reviewer is None:
        class _MemAdapter:
            """Wrap store_fn into SelfReviewSystem's memory_interface"""
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
    """Conversation self-review — evaluate response quality, return scores and improvement suggestions.

    Caller provides conversation content; results can be stored or not.
    """
    reviewer = _get_reviewer(store_fn)
    return reviewer.review_conversation(
        conversation_id=conversation_id,
        user_question=user_question,
        assistant_responses=[assistant_response],
        user_feedback=user_feedback,
    )
