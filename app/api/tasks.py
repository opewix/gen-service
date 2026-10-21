from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.schemas import TaskOut

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", response_model=TaskOut, responses={404: {}})
async def get_task(task_id: UUID) -> TaskOut:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")
