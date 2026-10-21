"""Жизненный цикл задачи: создание, переход по ногам, завершение и возврат.

Все переходы задачи делаются под блокировкой её строки (SELECT … FOR UPDATE):
конкурирующие вебхуки, сторож и отправщик выстраиваются в очередь на одной задаче,
и каждый следующий видит уже обновлённое состояние.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ledger import apply_entry
from app.models import (
    ACTIVE_ATTEMPT_STATUSES,
    ACTIVE_TASK_STATUSES,
    GenerationModel,
    ModelLeg,
    Task,
    TaskAttempt,
    User,
)
from app.schemas import GenerateRequest, ProviderWebhook


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


async def lock_attempt(session: AsyncSession, attempt_id: int) -> TaskAttempt | None:
    # Вызывать после lock_task: порядок блокировок везде «задача, затем попытка».
    return await session.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_submission(
    session: AsyncSession, task_id: UUID, attempt_id: int, external_id: str
) -> None:
    """Провайдер принял задачу (202): ждём вебхук до PROVIDER_TIMEOUT."""
    async with session.begin():
        task = await lock_task(session, task_id)
        attempt = await lock_attempt(session, attempt_id)
        assert task is not None and attempt is not None
        # external_id сохраняем всегда: если попытку уже сняли (аренда истекла,
        # задача завершилась), поздний вебхук по нему узнаем как устаревший.
        attempt.external_id = external_id
        if attempt.status != "dispatching":
            return
        attempt.status = "submitted"
        attempt.deadline_at = utcnow() + timedelta(seconds=get_settings().provider_timeout_seconds)
        if task.status == "pending":
            task.status = "running"
            task.updated_at = utcnow()


async def fail_attempt(
    session: AsyncSession,
    task_id: UUID,
    attempt_id: int,
    *,
    error_type: str,
    detail: str | None = None,
    final_status: str = "failed",
) -> int | None:
    """Отказ провайдера: вебхук failed или синхронная ошибка. Возвращает id
    следующей попытки, которую надо отправить, или None."""
    async with session.begin():
        task = await lock_task(session, task_id)
        attempt = await lock_attempt(session, attempt_id)
        assert task is not None and attempt is not None
        return await fail_locked(
            session, task, attempt, error_type=error_type, detail=detail, final_status=final_status
        )


async def fail_locked(
    session: AsyncSession,
    task: Task,
    attempt: TaskAttempt,
    *,
    error_type: str,
    detail: str | None,
    final_status: str,
) -> int | None:
    """Единая точка для всех отказов: вебхук failed, синхронная ошибка, сторож.
    Ведёт к следующей ноге или к единственному возврату. Задача уже заблокирована.

    Повторный вызов по той же попытке (дубль вебхука, гонка со сторожем) видит,
    что попытка уже не активна, и ничего не делает: так 10 одинаковых failed
    дают один переход и один возврат.
    """
    if attempt.status not in ACTIVE_ATTEMPT_STATUSES or task.status not in ACTIVE_TASK_STATUSES:
        return None

    now = utcnow()
    attempt.status = final_status
    attempt.error_type = error_type
    attempt.error_detail = detail
    attempt.finished_at = now
    task.updated_at = now

    # payload — неверная форма запроса, её отвергнет любая нога.
    if error_type == "payload":
        await finish_failed(session, task, error_type)
        return None

    leg = await next_leg(session, task, attempt)
    if leg is None:
        await finish_failed(session, task, error_type)
        return None

    task.status = "pending"
    task.current_leg_id = leg.id
    new_attempt = TaskAttempt(task_id=task.id, leg_id=leg.id, provider=leg.provider)
    session.add(new_attempt)
    await session.flush()
    return new_attempt.id


async def next_leg(session: AsyncSession, task: Task, failed: TaskAttempt) -> ModelLeg | None:
    current = await session.get(ModelLeg, failed.leg_id)
    assert current is not None
    # «Предыдущая отказала по модерации» трактуем как любой отказ moderation в истории
    # задачи: контент уже признан проблемным, и нога со skip_on_moderation его не получит.
    moderated = await session.scalar(
        select(
            exists().where(
                TaskAttempt.task_id == task.id, TaskAttempt.error_type == "moderation"
            )
        )
    )
    for leg in await model_legs(session, task.model_id):
        if leg.priority <= current.priority or not leg_accepts(leg, task.ref_images):
            continue
        if moderated and leg.skip_on_moderation:
            continue
        return leg
    return None


async def finish_failed(session: AsyncSession, task: Task, error_type: str) -> None:
    task.status = "failed"
    task.error_type = error_type
    task.updated_at = utcnow()
    # Ключ refund:<task_id> — вторая линия защиты: даже если переход сюда случится
    # дважды, журнал примет только один возврат.
    await apply_entry(
        session,
        user_id=task.user_id,
        amount=task.price,
        kind="refund",
        idempotency_key=f"refund:{task.id}",
        task_id=task.id,
    )


async def process_provider_webhook(
    session: AsyncSession, provider: str, webhook: ProviderWebhook
) -> tuple[str, int | None]:
    """Возвращает (результат, id следующей попытки для отправки).

    unknown  — такой external_id нам неизвестен: возможно, вебхук обогнал запись
               ответа на POST /tasks. Отвечаем 404, провайдер повторит.
    accepted — вебхук изменил состояние задачи.
    ignored  — дубль или вебхук по уже снятой попытке.
    """
    async with session.begin():
        ref = (
            await session.execute(
                select(TaskAttempt.id, TaskAttempt.task_id).where(
                    TaskAttempt.provider == provider, TaskAttempt.external_id == webhook.external_id
                )
            )
        ).one_or_none()
        if ref is None:
            return "unknown", None

        task = await lock_task(session, ref.task_id)
        attempt = await lock_attempt(session, ref.id)
        assert task is not None and attempt is not None

        if webhook.status == "failed":
            if attempt.status not in ACTIVE_ATTEMPT_STATUSES or task.status not in ACTIVE_TASK_STATUSES:
                return "ignored", None
            assert webhook.error_type is not None
            next_attempt = await fail_locked(
                session, task, attempt, error_type=webhook.error_type, detail=None, final_status="failed"
            )
            return "accepted", next_attempt

        assert webhook.result_url is not None
        return await complete_locked(session, task, attempt, webhook.result_url), None


async def complete_locked(
    session: AsyncSession, task: Task, attempt: TaskAttempt, result_url: str
) -> str:
    if task.status == "completed":
        return "ignored"

    if attempt.status == "timed_out":
        return await complete_late(session, task, attempt, result_url)

    if attempt.status not in ACTIVE_ATTEMPT_STATUSES or task.status not in ACTIVE_TASK_STATUSES:
        return "ignored"
    now = utcnow()
    attempt.status = "completed"
    attempt.result_url = result_url
    attempt.finished_at = now
    task.status = "completed"
    task.result_url = result_url
    task.updated_at = now
    return "accepted"


async def complete_late(
    session: AsyncSession, task: Task, attempt: TaskAttempt, result_url: str
) -> str:
    """Правило 6: completed пришёл по попытке, которую сторож уже признал неудачной.

    Задача не должна получить и результат, и возврат, поэтому решает то, был ли возврат:
    - задача ещё в работе на другой ноге — возврата не было и уже не будет: отдаём
      готовый результат, а текущую попытку снимаем (её ответ потом проигнорируем);
    - задача уже failed — токены вернули, результат не выдаём. Списать заново нельзя:
      денег может не хватить, а пользователь уже видел отказ. Результат остаётся
      в истории попытки, чтобы было видно, что он пришёл.
    """
    now = utcnow()
    attempt.result_url = result_url
    attempt.late_result_at = now
    if task.status not in ACTIVE_TASK_STATUSES:
        return "ignored"

    in_flight = await session.scalars(
        select(TaskAttempt)
        .where(TaskAttempt.task_id == task.id, TaskAttempt.status.in_(ACTIVE_ATTEMPT_STATUSES))
        .with_for_update()
    )
    for other in in_flight:
        other.status = "cancelled"
        other.error_detail = "superseded by late result of an earlier attempt"
        other.finished_at = now
    task.status = "completed"
    task.result_url = result_url
    task.current_leg_id = attempt.leg_id
    task.updated_at = now
    return "accepted"
