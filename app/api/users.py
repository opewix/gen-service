from fastapi import APIRouter, HTTPException, status

from app.schemas import BalanceOut

router = APIRouter(tags=["users"])


@router.get("/users/{user_id}/balance", response_model=BalanceOut, responses={404: {}})
async def get_balance(user_id: int) -> BalanceOut:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")
