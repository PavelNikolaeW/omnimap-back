"""Утилиты для работы с Celery-задачами."""

import redis
from django.conf import settings

# TTL для хранения владельца задачи (1 час)
TASK_OWNER_TTL = 3600


def get_redis_client():
    """Возвращает Redis-клиент для работы с задачами."""
    return redis.from_url(settings.CELERY_RESULT_BACKEND)


def save_task_owner(task_id: str, user_id: int) -> None:
    """
    Сохраняет связь task_id -> user_id в Redis.
    Используется для проверки прав доступа к статусу задачи.
    """
    redis_client = get_redis_client()
    task_owner_key = f'task_owner:{task_id}'
    redis_client.setex(task_owner_key, TASK_OWNER_TTL, str(user_id))


def get_task_owner(task_id: str) -> int | None:
    """
    Получает user_id владельца задачи из Redis.
    Возвращает None, если владелец не найден.
    """
    redis_client = get_redis_client()
    task_owner_key = f'task_owner:{task_id}'
    task_owner = redis_client.get(task_owner_key)
    if task_owner is not None:
        return int(task_owner.decode('utf-8'))
    return None
