from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import GenerationModel, ModelLeg, Task, TaskAttempt, User
from app.schemas import AttemptOut, LegOut, TaskOut

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskOut, responses={404: {}})
async def get_task(task_id: UUID, session: AsyncSession = Depends(get_session)) -> TaskOut:
    tasks = await load_tasks(session, Task.id == task_id, limit=1)
    if not tasks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return tasks[0]


@router.get("/users/{user_id}/tasks", response_model=list[TaskOut], responses={404: {}})
async def list_user_tasks(
    user_id: int,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> list[TaskOut]:
    tasks = await load_tasks(session, Task.user_id == user_id, limit=limit)
    if not tasks and await session.get(User, user_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return tasks


async def load_tasks(
    session: AsyncSession, where: ColumnElement[bool], *, limit: int
) -> list[TaskOut]:
    # Задачи и их попытки из одного снимка, чтобы статус не расходился с историей.
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    rows = (
        await session.execute(
            select(Task, GenerationModel.slug, ModelLeg)
            .join(GenerationModel, GenerationModel.id == Task.model_id)
            .outerjoin(ModelLeg, ModelLeg.id == Task.current_leg_id)
            .where(where)
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return []

    attempts: dict[UUID, list[AttemptOut]] = defaultdict(list)
    for a in await session.scalars(
        select(TaskAttempt)
        .where(TaskAttempt.task_id.in_([task.id for task, _, _ in rows]))
        .order_by(TaskAttempt.id)
    ):
        attempts[a.task_id].append(
            AttemptOut(
                provider=a.provider,
                status=a.status,
                error_type=a.error_type,
                error_detail=a.error_detail,
                result_url=a.result_url,
                late_result_at=a.late_result_at,
                created_at=a.created_at,
                finished_at=a.finished_at,
            )
        )
    return [
        TaskOut(
            id=task.id,
            status=task.status,
            model_slug=model_slug,
            prompt=task.prompt,
            ref_images=task.ref_images,
            created_at=task.created_at,
            current_provider=leg.provider if leg else None,
            current_leg=LegOut(
                provider=leg.provider,
                priority=leg.priority,
                max_ref_images=leg.max_ref_images,
                skip_on_moderation=leg.skip_on_moderation,
            )
            if leg
            else None,
            result_url=task.result_url,
            error_type=task.error_type,
            attempts=attempts[task.id],
        )
        for task, model_slug, leg in rows
    ]
