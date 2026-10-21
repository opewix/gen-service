from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import GenerationModel, ModelLeg, Task, TaskAttempt
from app.schemas import AttemptOut, TaskOut

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskOut, responses={404: {}})
async def get_task(task_id: UUID, session: AsyncSession = Depends(get_session)) -> TaskOut:
    # Задача и её попытки из одного снимка, чтобы статус не расходился с историей.
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    row = (
        await session.execute(
            select(Task, GenerationModel.slug, ModelLeg.provider)
            .join(GenerationModel, GenerationModel.id == Task.model_id)
            .outerjoin(ModelLeg, ModelLeg.id == Task.current_leg_id)
            .where(Task.id == task_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    task, model_slug, current_provider = row

    attempts = await session.scalars(
        select(TaskAttempt).where(TaskAttempt.task_id == task_id).order_by(TaskAttempt.id)
    )
    return TaskOut(
        id=task.id,
        status=task.status,
        model_slug=model_slug,
        current_provider=current_provider,
        result_url=task.result_url,
        error_type=task.error_type,
        attempts=[
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
            for a in attempts
        ],
    )
