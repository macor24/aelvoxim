"""aelvoxim.learn.cleanup — Memory layer + knowledge base cleanup

Split from learner.py (1969-line monolith).
Responsibility: memory layer cleanup, KB quality cleanup (low-confidence entries).
"""

from __future__ import annotations


import logging
_log = logging.getLogger("aelvoxim.learn.cleanup")

def memory_layer_cleanup() -> None:
    """Clean up old/expired memory entries in all three layers.

    Runs every cognition tick.
    """
    try:
        from ..memory.forget import cleanup_all as _cl
        from ..memory import _fusion as _fus
        _cl({"working": _fus.working, "episodic": _fus.episodic, "semantic": _fus.semantic})
    except Exception:
        _log.exception("cleanup error")


def cleanup_knowledge_base(log_func) -> None:
    """Remove or flag low-confidence knowledge entries.

    Runs every 6 hours.
    - conf < 0.3: auto-delete
    - conf 0.3-0.5 AND age > 7 days: auto-archive (more aggressive than flag)
    - conf 0.3-0.5 AND age > 30 days: flag for review (original behavior)
    """
    try:
        from ..learn.knowledge import KnowledgeBase
        from datetime import datetime

        kb = KnowledgeBase()
        entries = list(kb.get_all_active())
        now = datetime.now()

        deleted = 0
        archived = 0
        flagged = 0

        for e in entries:
            conf = e.get("confidence", 0.5)
            created = e.get("created_at", "")
            age_days = 0
            if created:
                try:
                    if isinstance(created, (int, float)):
                        age_days = (now.timestamp() - created) / 86400
                    else:
                        age_days = (now - datetime.fromisoformat(created.replace("Z", ""))).days
                except (ValueError, TypeError):
                    age_days = 0

            if conf < 0.3:
                kb.delete(e.get("id", ""))
                deleted += 1
            elif conf >= 0.7:
                continue  # high-confidence entries (≥0.7) are whitelisted
            elif conf < 0.5 and age_days > 7:
                # Auto-archive stale low-confidence entries (7 days → archive)
                eid = e.get("id", "")
                try:
                    e["_status"] = "archived"
                    e["_archived_reason"] = f"auto: low confidence ({conf:.2f}, {age_days}d old)"
                    from ..learn.knowledge import _write_entry
                    _write_entry(e)
                    archived += 1
                except Exception:
                    pass
            elif conf < 0.5 and age_days > 30:
                if hasattr(kb, 'flag_for_review'):
                    kb.flag_for_review(e.get("id", ""))
                flagged += 1

        report_parts = []
        if deleted:
            report_parts.append(f"deleted {deleted} low-confidence")
        if archived:
            report_parts.append(f"archived {archived} stale low-confidence")
        if flagged:
            report_parts.append(f"flagged {flagged} for review")
        if report_parts:
            log_func(f"  🧹 KB cleanup: {', '.join(report_parts)}")
        elif not any([deleted, archived, flagged]):
            log_func("  🧹 KB cleanup: nothing to clean")
    except Exception as ex:
        log_func(f"  ⚠️ KB cleanup failed: {ex}")
