from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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

    @model_validator(mode="after")
    def outcome_has_details(self) -> "ProviderWebhook":
        if self.status == "completed" and not self.result_url:
            raise ValueError("completed webhook requires result_url")
        if self.status == "failed" and self.error_type is None:
            raise ValueError("failed webhook requires error_type")
        return self


class ProviderWebhookOut(BaseModel):
    result: Literal["accepted", "ignored"]


class PaymentWebhook(BaseModel):
    payment_id: str
    user_id: int
    tokens: int = Field(gt=0)
    status: str


class PaymentWebhookOut(BaseModel):
    result: Literal["credited", "duplicate", "ignored"]


class AttemptOut(BaseModel):
    provider: str
    status: str
    error_type: ErrorType | None
    error_detail: str | None
    result_url: str | None
    created_at: datetime
    finished_at: datetime | None


class TaskOut(BaseModel):
    id: UUID
    status: str
    model_slug: str
    current_provider: str | None
    result_url: str | None
    error_type: ErrorType | None
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
