# gen-service

Сервис генерации: принимает заказ, списывает токены, отдаёт задачу провайдеру,
при отказе идёт к следующему, если отказали все — возвращает токены.

> Статус: инфраструктура и заглушки API. Бизнес-логика эндпоинтов пока отвечает 501.

Документы: [что делает сервис простыми словами](docs/01-overview.md),
[первичный разбор задания и открытые вопросы](docs/02-initial-analysis.md).

## Запуск

```bash
docker compose up -d --build
```

Поднимаются:

| Сервис    | Что это                                     | Порт на хосте |
|-----------|---------------------------------------------|---------------|
| `db`      | PostgreSQL 16                               | 5432          |
| `migrate` | `alembic upgrade head`, затем завершается   | —             |
| `api`     | сервис, `uvicorn --workers 4`               | 8000          |
| `alpha`   | фейковый провайдер                          | 8001          |
| `beta`    | фейковый провайдер                          | 8002          |

`api` стартует только после успешного `migrate`, поэтому 4 воркера не мигрируют
базу наперегонки. Swagger: http://localhost:8000/docs

## Тесты

Тесты ходят в живой стек из compose (API, провайдеры, база):

```bash
docker compose run --rm tests
```

## Миграции

Схема создаётся только миграциями Alembic ([migrations/versions](migrations/versions)),
ORM-модели в [app/models.py](app/models.py) обязаны с ними совпадать.

| Ревизия | Что делает                                        |
|---------|---------------------------------------------------|
| 0001    | таблицы: users, generation_models, model_legs, tasks, task_attempts, ledger_entries |
| 0002    | каталог: image-fast и video-long с их ногами      |
| 0003    | демо-пользователи 1, 2, 3 с нулевым балансом      |

Применить, откатить, посмотреть текущую:

```bash
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic downgrade -1
docker compose run --rm migrate alembic current
```

Добавить следующую:

1. Поменять модели в `app/models.py`.
2. Сгенерировать черновик со следующим номером:
   ```bash
   docker compose run --rm -v "$PWD/migrations:/srv/migrations" -u "$(id -u)" \
     migrate alembic revision --autogenerate --rev-id 0004 -m "add something"
   ```
3. Вычитать и поправить файл руками: autogenerate не видит изменения CHECK-ограничений
   и не пишет миграции данных.
4. Проверить, что модели и миграции совпадают: `alembic check` должен ответить
   `No new upgrade operations detected.`
5. `alembic upgrade head` и `alembic downgrade -1` должны проходить оба.

## Схема данных

- `users.balance` — кэш суммы журнала. Меняется только в одной транзакции со вставкой
  строки в `ledger_entries`; `CHECK (balance >= 0)`.
- `ledger_entries.idempotency_key` — `UNIQUE`; ключи `charge:<task_id>`,
  `refund:<task_id>`, `payment:<payment_id>`. На этом строится «ровно один раз».
- `tasks.price` — цена на момент заказа, чтобы вернуть ровно списанное.
- `task_attempts` — история попыток по ногам; `UNIQUE (provider, external_id)`
  для поиска попытки по вебхуку; частичный индекс по `deadline_at` для сторожа.
- Статусы — `text` + `CHECK`, а не PG `ENUM`: расширяется обычной миграцией с откатом.
