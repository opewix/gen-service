from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ErrorType = Literal["technical", "moderation", "payload"]


class GenerateRequest(BaseModel):
    user_id: int
    model_slug: str
    prompt: str = Field(min_length=1)
    ref_images: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    task_id: UUID


class ProviderWebhook(BaseModel):
    external_id: str
    status: Literal["completed", "failed"]
    result_url: str | None = None
    error_type: ErrorType | None = None


class PaymentWebhook(BaseModel):
    payment_id: str
    user_id: int
    tokens: int = Field(gt=0)
    status: str


class AttemptOut(BaseModel):
    provider: str
    status: str
    error_type: ErrorType | None
    created_at: datetime
    finished_at: datetime | None


class TaskOut(BaseModel):
    id: UUID
    status: str
    model_slug: str
    current_provider: str | None
    result_url: str | None
    attempts: list[AttemptOut]


class LedgerEntryOut(BaseModel):
    amount: int
    kind: str
    task_id: UUID | None
    payment_id: str | None
    created_at: datetime


class BalanceOut(BaseModel):
    user_id: int
    balance: int
    ledger: list[LedgerEntryOut]
