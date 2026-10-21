import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import dispatcher, watchdog
from app.api import catalog, generate, tasks, users, webhooks
from app.db import engine, get_session


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    dispatcher.start()
    # Сторож в каждом воркере: дублирования нет за счёт SKIP LOCKED (см. app/watchdog.py),
    # а падение одного процесса не останавливает присмотр за задачами.
    watchdog_task = asyncio.create_task(watchdog.run_forever())
    yield
    watchdog_task.cancel()
    await dispatcher.stop()
    await engine.dispose()


app = FastAPI(title="gen-service", lifespan=lifespan)
app.include_router(generate.router)
app.include_router(webhooks.router)
app.include_router(tasks.router)
app.include_router(users.router)
app.include_router(catalog.router)
# Простой UI без сборки: статика с того же адреса, CORS не нужен.
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "ui", html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


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
