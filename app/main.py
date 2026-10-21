from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import dispatcher
from app.api import generate, tasks, users, webhooks
from app.db import engine, get_session


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    dispatcher.start()
    yield
    await dispatcher.stop()
    await engine.dispose()


app = FastAPI(title="gen-service", lifespan=lifespan)
app.include_router(generate.router)
app.include_router(webhooks.router)
app.include_router(tasks.router)
app.include_router(users.router)


@app.exception_handler(ValidationError)
async def validation_error(request: Request, exc: ValidationError):
    # Вебхуки валидируются вручную после проверки подписи; отвечаем 422, как сам FastAPI.
    return await request_validation_exception_handler(
        request, RequestValidationError(exc.errors(include_url=False))
    )


@app.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
