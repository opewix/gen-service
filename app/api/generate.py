from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import dispatcher
from app.db import get_session
from app.orchestrator import GenerateError, create_task
from app.schemas import GenerateRequest, GenerateResponse

router = APIRouter(tags=["generate"])


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GenerateResponse,
    responses={400: {}, 402: {}, 404: {}, 429: {}},
)
async def generate(
    payload: GenerateRequest, session: AsyncSession = Depends(get_session)
) -> GenerateResponse:
    try:
        attempt = await create_task(session, payload)
    except GenerateError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    dispatcher.schedule(attempt.id)
    return GenerateResponse(task_id=attempt.task_id)
