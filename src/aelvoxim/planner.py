"""
metacore.planner — Long-term planning engine.

Design (from AEL.txt):
1. Goal decomposition layer — break long-term goals into milestones + sub-tasks
2. Progress tracking layer — track completion state per sub-task
3. Adaptive adjustment layer — adjust plan based on progress and feedback

Planner runs in 9703 (cortex), reads learner status from 9701 via /v1/status/planner.
Persistence: ~/.metacore/plans/{plan_id}.json

Edge cases:
- Empty plan list → return no action
- All milestones completed → plan is "done"
- Milestone stuck (no progress for 7 days) → flag for user review
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import DATA_DIR

PLANS_DIR = DATA_DIR / "plans"


def _ensure_dirs():
    PLANS_DIR.mkdir(parents=True, exist_ok=True)


# ── Data structures ──


class Milestone:
    """A single milestone within a plan."""

    def __init__(self, milestone_id: str, description: str,
                 status: str = "pending",  # pending / active / done / stuck
                 created_at: Optional[str] = None,
                 completed_at: Optional[str] = None,
                 tasks: Optional[List[str]] = None):
        self.id = milestone_id
        self.description = description
        self.status = status
        self.created_at = created_at or datetime.now().isoformat()
        self.completed_at = completed_at
        self.tasks = tasks or []

    def to_dict(self) -> dict:
        return {"id": self.id, "description": self.description,
                "status": self.status, "created_at": self.created_at,
                "completed_at": self.completed_at, "tasks": self.tasks}

    @staticmethod
    def from_dict(d: dict) -> "Milestone":
        return Milestone(d.get("id", ""), d.get("description", ""),
                         status=d.get("status", "pending"),
                         created_at=d.get("created_at"),
                         completed_at=d.get("completed_at"),
                         tasks=d.get("tasks", []))


class Plan:
    """A long-term learning plan with milestones."""

    def __init__(self, plan_id: str, goal: str,
                 milestones: Optional[List[Milestone]] = None,
                 status: str = "active",  # active / paused / done
                 created_at: Optional[str] = None,
                 source: str = "user"):  # user / auto_detect
        self.id = plan_id
        self.goal = goal
        self.milestones = milestones or []
        self.status = status
        self.created_at = created_at or datetime.now().isoformat()
        self.source = source

    def add_milestone(self, milestone: Milestone):
        self.milestones.append(milestone)
        self._save()

    def next_milestone(self) -> Optional[Milestone]:
        """Get the first pending or active milestone."""
        for m in self.milestones:
            if m.status in ("pending", "active"):
                return m
        return None

    def next_action(self) -> Optional[Dict[str, Any]]:
        """Derive the next actionable step from the current milestone.

        Returns {"type": "learn"|"search"|"review", "goal": "...", "id": "...",
                 "plan_id": "...", "milestone_id": "..."} or None.
        """
        ms = self.next_milestone()
        if not ms:
            return None

        first_task = ms.tasks[0] if ms.tasks else ms.description

        return {
            "type": "learn",
            "goal": first_task,
            "id": f"{ms.id}:0",
            "milestone_id": ms.id,
            "plan_id": self.id,
        }

    def update_progress(self, learner_status: dict) -> List[str]:
        """Update milestone statuses based on learner progress.

        Associates milestones with learner directions via plan_id tag.
        Returns list of status changes (for logging).
        """
        changes = []
        plan_tag = self.id
        # Find directions that belong to this plan
        plan_entries = 0
        for topic, info in learner_status.get("directions", {}).items():
            if info.get("source_plan") == plan_tag or plan_tag in str(info.get("plan_ids", [])):
                plan_entries += info.get("entries_created", 0)
        # Fallback: if no associated directions found, keep 0 (don't use global)
        # — using global total_entries would falsely activate milestones
        #   from other plans' progress.
        if plan_entries == 0:
            plan_entries = 0

        for ms in self.milestones:
            if ms.status == "done":
                continue

            # Check if any learner direction tagged with this milestone is completed
            ms_tag = ms.id
            ms_done = False
            for topic, info in learner_status.get("directions", {}).items():
                if info.get("source_milestone") == ms_tag and info.get("status") == "completed":
                    ms_done = True
                    break

            if ms_done:
                ms.status = "done"
                ms.completed_at = datetime.now().isoformat()
                changes.append(f"  {ms.id}: → done (direction completed)")
                continue

            # Simple heuristic: if learner produced entries, mark first pending as active
            if ms.status == "pending" and plan_entries > 0:
                ms.status = "active"
                changes.append(f"  {ms.id}: pending → active")
            # If no activity for 7 days, mark as stuck
            if ms.status == "active":
                created = datetime.fromisoformat(ms.created_at) if ms.created_at else datetime.now()
                if (datetime.now() - created).days > 7:
                    ms.status = "stuck"
                    changes.append(f"  {ms.id}: active → stuck (7d no progress)")
        self._save()
        return changes

    def to_dict(self) -> dict:
        return {"id": self.id, "goal": self.goal,
                "milestones": [m.to_dict() for m in self.milestones],
                "status": self.status, "created_at": self.created_at,
                "source": self.source}

    @staticmethod
    def from_dict(d: dict) -> "Plan":
        milestones = [Milestone.from_dict(m) for m in d.get("milestones", [])]
        return Plan(d.get("id", ""), d.get("goal", ""),
                    milestones=milestones,
                    status=d.get("status", "active"),
                    created_at=d.get("created_at"),
                    source=d.get("source", "user"))

    def _path(self) -> Path:
        return PLANS_DIR / f"{self.id}.json"

    def _save(self):
        _ensure_dirs()
        self._path().write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))


# ── LongTermPlanner ──


class LongTermPlanner:
    """Manages multiple plans and derives next actions.

    Serves as the "长期规划引擎" from AEL.txt.
    Called by Scheduler every 5 minutes.
    """

    def __init__(self):
        _ensure_dirs()
        self._plans: Dict[str, Plan] = {}
        self._load_all()

    def _load_all(self):
        """Load all saved plans from ~/.metacore/plans/."""
        if not PLANS_DIR.exists():
            return
        for f in sorted(PLANS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                plan = Plan.from_dict(data)
                self._plans[plan.id] = plan
            except Exception:
                pass

    def create_plan(self, goal: str, source: str = "user") -> Plan:
        """Create a new plan from a user goal.

        Auto-decomposes into milestones using decompose_direction
        for meaningful sub-tasks, with template fallback.
        """
        plan_id = f"plan:{int(time.time())}:{abs(hash(goal)) & 0xFFFFFF:06x}"
        plan = Plan(plan_id, goal, source=source)

        # Try topic-aware decomposition
        milestones = []
        try:
            from .learn.decompose import decompose_direction
            sub_tasks = decompose_direction(goal)
            if sub_tasks and len(sub_tasks) >= 2:
                for i, task in enumerate(sub_tasks[:5]):
                    milestones.append(Milestone(
                        f"{plan_id}:ms:{i}", task,
                    ))
        except Exception:
            pass

        if not milestones:
            # Fallback: template milestones (rarely reached — decompose_direction
            # has a generic category fallback at strategy #3 before returning)
            milestones = [
                Milestone(f"{plan_id}:ms:0", f"Research and understand {goal[:40]}"),
                Milestone(f"{plan_id}:ms:1", f"Core concepts and fundamental knowledge of {goal[:40]}"),
                Milestone(f"{plan_id}:ms:2", f"Practical application and implementation of {goal[:40]}"),
                Milestone(f"{plan_id}:ms:3", f"Review and consolidate knowledge of {goal[:40]}"),
            ]

        plan.milestones = milestones
        plan._save()
        self._plans[plan.id] = plan
        return plan

    def get_plan(self, plan_id: str) -> Optional[Plan]:
        return self._plans.get(plan_id)

    def list_plans(self) -> List[dict]:
        return [p.to_dict() for p in self._plans.values()]

    def next_action(self) -> Optional[Dict[str, Any]]:
        """Get the next actionable action across all active plans."""
        for plan in self._plans.values():
            if plan.status != "active":
                continue
            action = plan.next_action()
            if action:
                return action
        return None

    def mark_dispatched(self, action_id: str):
        """Called by Scheduler after dispatching an action. Updates plan state."""
        _plog = logging.getLogger("aelvoxim.planner")
        for plan in self._plans.values():
            if plan.status != "active":
                continue
            for ms in plan.milestones:
                if ms.id in action_id:
                    _plog.info("mark_dispatched matched plan=%.30s ms=%s -> %s",
                               plan.goal[:30], ms.id, "done" if not ms.tasks else "task_popped")
                    if ms.tasks:
                        ms.tasks.pop(0)
                    if not ms.tasks:
                        ms.status = "done"
                        ms.completed_at = datetime.now().isoformat()
                    plan._save()
                    # Check if plan is fully completed
                    if all(m.status == "done" for m in plan.milestones):
                        plan.status = "done"
                        plan._save()
                        _plog.info("  🎉 Plan completed: %.40s", plan.goal[:40])
                        # Auto-summary: store a knowledge entry about completed plan
                        self._summarize_plan(plan)
                    return

    def _summarize_plan(self, plan: Plan) -> None:
        """Store a summary of completed plan as knowledge + suggest next goal."""
        try:
            from .learn.knowledge import KnowledgeBase
            summary = (
                f"已完成学习计划: {plan.goal}\n"
                f"里程碑 ({len(plan.milestones)} 个):\n"
            )
            for m in plan.milestones:
                summary += f"  - {m.description} [{m.status}]\n"
            KnowledgeBase.store_pending(
                topic=f"已完成计划: {plan.goal[:40]}",
                title=plan.goal[:80],
                content=summary[:500],
                source="plan_complete",
            )
            # Auto-suggest next goal: if there are other unfinished plans, suggest continuing one
            _unfinished = [p for p in self._plans.values() if p.status == "active" and p.id != plan.id]
            if not _unfinished:
                # No other plans — suggest expanding in a related direction
                _related = self._suggest_next_goal(plan.goal)
                if _related:
                    self.create_plan(f"深入学习 {plan.goal[:30]} - {_related}", source="auto_suggest")
        except Exception:
            pass

    @staticmethod
    def _suggest_next_goal(completed_goal: str) -> str:
            """Generate a next-goal suggestion based on completed goal topic."""
            _suggestions = {
                "python": "Web 框架 (FastAPI/Flask), 数据分析 (Pandas/NumPy), 自动化脚本",
                "fastapi": "数据库集成 (SQLAlchemy), 测试 (pytest), 部署 (Docker/Cloud)",
                "docker": "Kubernetes, CI/CD 管道, 微服务架构",
                "sql": "查询优化 (索引/EXPLAIN), NoSQL 对比, 数据库设计范式",
                "前端": "React/Vue 框架, TypeScript, 响应式设计",
                "javascript": "TypeScript, React 框架, Node.js 后端",
                "react": "Next.js, 状态管理 (Zustand/Redux), 组件测试",
                "go": "并发模型 (goroutine/channel), Web 服务, 性能优化",
                "rust": "所有权/借用系统, 并发编程, WebAssembly",
                "机器学习": "深度学习 (PyTorch), NLP, 模型部署 (ONNX/vLLM)",
                "深度学习": "Transformer 架构, 模型量化/蒸馏, 分布式训练",
            }
            goal_lower = completed_goal.lower()
            for key, suggestion in _suggestions.items():
                if key in goal_lower:
                    return f"进阶方向: {suggestion}"
            return "进阶方向: 实践项目, 性能优化, 深入底层原理"

    def update_from_learner(self, status: dict):
        """Update all plans with latest learner status."""
        for plan in self._plans.values():
            if plan.status == "active":
                changes = plan.update_progress(status)
                for c in changes:
                    import logging
                    logging.getLogger("aelvoxim.planner").info("Plan update: %s", c)

    def delete_plan(self, plan_id: str) -> bool:
        path = PLANS_DIR / f"{plan_id}.json"
        if path.exists():
            path.unlink()
            self._plans.pop(plan_id, None)
            return True
        return False
