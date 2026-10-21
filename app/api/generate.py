from fastapi import APIRouter, HTTPException, status

from app.schemas import GenerateRequest, GenerateResponse

router = APIRouter(tags=["generate"])


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=GenerateResponse,
    responses={400: {}, 402: {}, 404: {}, 429: {}},
)
async def generate(payload: GenerateRequest) -> GenerateResponse:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")
