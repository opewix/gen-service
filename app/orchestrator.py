"""Жизненный цикл задачи: создание, переход по ногам, завершение и возврат.

Все переходы задачи делаются под блокировкой её строки (SELECT … FOR UPDATE):
конкурирующие вебхуки, сторож и отправщик выстраиваются в очередь на одной задаче,
и каждый следующий видит уже обновлённое состояние.
"""

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ledger import apply_entry
from app.models import ACTIVE_TASK_STATUSES, GenerationModel, ModelLeg, Task, TaskAttempt, User
from app.schemas import GenerateRequest


class GenerateError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def leg_accepts(leg: ModelLeg, ref_images: list[str]) -> bool:
    return len(ref_images) <= leg.max_ref_images


async def create_task(session: AsyncSession, request: GenerateRequest) -> TaskAttempt:
    """Списывает цену и создаёт задачу с первой попыткой в статусе pending.

    Отправка провайдеру — после коммита: держать транзакцию и блокировку
    пользователя на время HTTP-запроса нельзя, а упавшую до отправки попытку
    подберёт сторож.
    """
    async with session.begin():
        model = await session.scalar(
            select(GenerationModel).where(GenerationModel.slug == request.model_slug)
        )
        if model is None:
            raise GenerateError(404, "model not found")

        legs = await model_legs(session, model.id)
        first_leg = next((leg for leg in legs if leg_accepts(leg, request.ref_images)), None)
        if first_leg is None:
            raise GenerateError(400, "no provider accepts this many reference images")

        # Блокировка строки пользователя сериализует его параллельные /generate:
        # иначе два запроса оба увидят 2 активные задачи и оба создадут третью.
        user = await session.scalar(select(User).where(User.id == request.user_id).with_for_update())
        if user is None:
            raise GenerateError(404, "user not found")

        active = await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user.id, Task.status.in_(ACTIVE_TASK_STATUSES))
        )
        if active >= get_settings().max_active_tasks_per_user:
            raise GenerateError(429, "too many active tasks")
        if user.balance < model.price:
            raise GenerateError(402, "insufficient tokens")

        task = Task(
            id=uuid4(),
            user_id=user.id,
            model_id=model.id,
            prompt=request.prompt,
            ref_images=request.ref_images,
            price=model.price,
            status="pending",
            current_leg_id=first_leg.id,
        )
        session.add(task)
        await session.flush()
        attempt = TaskAttempt(task_id=task.id, leg_id=first_leg.id, provider=first_leg.provider)
        session.add(attempt)
        await apply_entry(
            session,
            user_id=user.id,
            amount=-model.price,
            kind="charge",
            idempotency_key=f"charge:{task.id}",
            task_id=task.id,
        )
    return attempt


async def model_legs(session: AsyncSession, model_id: int) -> list[ModelLeg]:
    return list(
        await session.scalars(
            select(ModelLeg).where(ModelLeg.model_id == model_id).order_by(ModelLeg.priority)
        )
    )


async def lock_task(session: AsyncSession, task_id: UUID) -> Task | None:
    return await session.scalar(
        select(Task)
        .where(Task.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
