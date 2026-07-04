"""
metacore.server.routes_task — Learning task management endpoints.

Routes:
    POST /v1/task           — Create a new learning task
    GET  /v1/task/{task_id} — Get task status
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from .routes import _verify_key

router = APIRouter()


@router.post("/task")
async def create_task(
    body: dict,
    user: dict = Depends(_verify_key),
):
    """Create a new learning task. Body: {\"goal\": \"...\", \"task_type\": \"learn\"}"""
    from ..api import submit_task
    goal = body.get("goal", "").strip()
    task_type = body.get("task_type", "learn")
    if not goal:
        raise HTTPException(400, detail="goal is required")
    task_id = submit_task(goal, task_type)
    return {"task_id": task_id, "status": "created"}


@router.get("/task/{task_id}")
async def get_task(task_id: str, user: dict = Depends(_verify_key)):
    """Get the status of a learning task."""
    from ..api import get_task_status
    status = get_task_status(task_id)
    if not status:
        raise HTTPException(404, detail="task not found")
    return status
