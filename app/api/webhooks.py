from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import signed_body
from app.schemas import PaymentWebhook, ProviderWebhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/providers/{name}")
async def provider_webhook(name: str, body: bytes = Depends(signed_body)) -> None:
    ProviderWebhook.model_validate_json(body)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")


@router.post("/payments")
async def payment_webhook(body: bytes = Depends(signed_body)) -> None:
    PaymentWebhook.model_validate_json(body)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")
