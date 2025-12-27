# Задачи для omnimap-back

## Улучшения загрузки изображений

Рекомендации из code review PR #5.

### 1. Проверка максимальных размеров изображения
**Приоритет:** Средний

Добавить валидацию максимальных размеров (ширина/высота) для предотвращения загрузки очень больших изображений.

```python
# В settings.py
MAX_IMAGE_DIMENSIONS = (4096, 4096)  # максимум 4K

# В validate_image()
if width > settings.MAX_IMAGE_DIMENSIONS[0] or height > settings.MAX_IMAGE_DIMENSIONS[1]:
    return False, f'Image too large. Max dimensions: {settings.MAX_IMAGE_DIMENSIONS}', None
```

---

### 2. Асинхронная генерация превью через Celery
**Приоритет:** Низкий (для больших нагрузок)

Для больших изображений генерировать превью в фоновом режиме.

```python
# api/tasks.py
@shared_task
def generate_thumbnail_task(block_file_id):
    block_file = BlockFile.objects.get(id=block_file_id)
    thumbnail_content = create_thumbnail(block_file.file)
    if thumbnail_content:
        thumb_filename = f"thumb_{block_file.id}.jpg"
        block_file.thumbnail.save(thumb_filename, thumbnail_content, save=True)
```

---

### 3. Сжатие изображений при загрузке
**Приоритет:** Средний

Автоматическое сжатие JPEG изображений для экономии места.

```python
# В settings.py
JPEG_QUALITY = 85
OPTIMIZE_IMAGES = True

# В views_files.py
def optimize_image(file, quality=85):
    img = Image.open(file)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    output = io.BytesIO()
    img.save(output, format='JPEG', quality=quality, optimize=True)
    output.seek(0)
    return ContentFile(output.read())
```

---

### 4. Type hints для лучшей типизации
**Приоритет:** Низкий

Добавить type hints в `views_files.py`:

```python
from typing import Tuple, Optional, Dict, Any
from django.core.files.uploadedfile import UploadedFile

def validate_image(file: UploadedFile) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    ...

def create_thumbnail(image_file: UploadedFile, max_size: Tuple[int, int] = None) -> Optional[ContentFile]:
    ...
```

---

### 5. Централизованная конфигурация MIME-типов
**Приоритет:** Низкий

Вынести маппинг типов в отдельный конфиг:

```python
# api/constants.py
CONTENT_TYPE_MAP = {
    'image/jpeg': {'extensions': ['jpg', 'jpeg'], 'pillow_format': 'JPEG'},
    'image/png': {'extensions': ['png'], 'pillow_format': 'PNG'},
    'image/gif': {'extensions': ['gif'], 'pillow_format': 'GIF'},
    'image/webp': {'extensions': ['webp'], 'pillow_format': 'WEBP'},
}
```

---

## Чеклист

- [x] Добавить проверку MAX_IMAGE_DIMENSIONS (PR #6)
- [ ] Реализовать асинхронную генерацию превью (опционально, низкий приоритет)
- [x] Добавить сжатие JPEG изображений (PR #6)
- [x] Добавить type hints в views_files.py (PR #6)
- [x] Вынести CONTENT_TYPE_MAP в constants.py (PR #6)

---
---

# Задачи: Напоминания и уведомления об изменениях

## Обзор

Реализовать систему напоминаний о блоках и уведомлений об изменениях с поддержкой Telegram, Email и Push.

---

## 1. Модели данных

### 1.1 BlockReminder — Напоминание о блоке

```python
class BlockReminder(models.Model):
    """Напоминание о блоке (1 блок = 1 напоминание)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    block = models.OneToOneField(  # Уникальное: 1 блок = 1 напоминание
        Block,
        on_delete=models.CASCADE,
        related_name='reminder'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reminders')

    remind_at = models.DateTimeField(db_index=True)
    timezone = models.CharField(max_length=50, default='UTC')  # Часовой пояс пользователя
    message = models.TextField(blank=True)  # Комментарий пользователя

    repeat = models.CharField(max_length=20, choices=[
        ('none', 'Однократно'),
        ('daily', 'Ежедневно'),
        ('weekly', 'Еженедельно'),
        ('monthly', 'Ежемесячно'),
    ], default='none')

    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)  # Отложено до

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['remind_at', 'is_sent']),
            models.Index(fields=['user', 'is_sent']),
        ]
```

### 1.2 BlockChangeSubscription — Подписка на изменения

```python
class BlockChangeSubscription(models.Model):
    """Подписка на изменения блока"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    block = models.ForeignKey(Block, on_delete=models.CASCADE, related_name='subscriptions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='block_subscriptions')

    # Глубина отслеживания: 0=только блок, 1,2,3=уровни, -1=все потомки
    depth = models.SmallIntegerField(default=1)

    # Типы отслеживаемых изменений
    on_text_change = models.BooleanField(default=True)
    on_data_change = models.BooleanField(default=True)  # стили, размеры
    on_move = models.BooleanField(default=True)
    on_child_add = models.BooleanField(default=True)
    on_child_delete = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # Rate limiting: последнее уведомление (не чаще 1 раз в минуту)
    last_notification_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['block', 'user']
        indexes = [
            models.Index(fields=['user']),
        ]
```

### 1.3 UserNotificationSettings — Настройки уведомлений

```python
class UserNotificationSettings(models.Model):
    """Настройки уведомлений пользователя"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_settings')

    # Telegram
    telegram_chat_id = models.CharField(max_length=50, blank=True, null=True)
    telegram_username = models.CharField(max_length=100, blank=True, null=True)
    telegram_enabled = models.BooleanField(default=False)
    telegram_linked_at = models.DateTimeField(null=True, blank=True)

    # Email
    email_enabled = models.BooleanField(default=True)
    email_mode = models.CharField(max_length=20, choices=[
        ('off', 'Выключено'),
        ('fallback', 'Если Telegram недоступен'),
        ('always', 'Всегда дублировать'),
    ], default='fallback')

    # Push уведомления в браузере
    push_enabled = models.BooleanField(default=False)
    push_subscription = models.JSONField(null=True, blank=True)  # PushSubscription object

    # Тихие часы
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(null=True, blank=True)  # 23:00
    quiet_hours_end = models.TimeField(null=True, blank=True)    # 08:00
    timezone = models.CharField(max_length=50, default='UTC')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 1.4 TelegramLinkToken — Токен для привязки Telegram

```python
class TelegramLinkToken(models.Model):
    """Временный токен для привязки Telegram аккаунта"""
    token = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['token', 'used']),
        ]
```

### 1.5 PendingNotification — Очередь уведомлений (для пакетной отправки)

```python
class PendingNotification(models.Model):
    """Очередь уведомлений для агрегации"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    subscription = models.ForeignKey(BlockChangeSubscription, on_delete=models.CASCADE)
    block = models.ForeignKey(Block, on_delete=models.CASCADE)

    change_type = models.CharField(max_length=20)  # text_change, data_change, move, child_add, child_delete
    changed_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='changes_made')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]
```

---

## 2. Лимиты

```python
# settings.py

# Лимиты на пользователя
MAX_REMINDERS_PER_USER = 100
MAX_SUBSCRIPTIONS_PER_USER = 50

# Rate limiting
MIN_NOTIFICATION_INTERVAL_SECONDS = 60  # Не чаще 1 раз в минуту на подписку

# Telegram link token
TELEGRAM_LINK_TOKEN_EXPIRY_MINUTES = 15

# Агрегация уведомлений
NOTIFICATION_AGGREGATION_WINDOW_SECONDS = 60  # Собираем изменения за 1 минуту
```

---

## 3. API Endpoints

### 3.1 Напоминания

```
POST   /api/v1/reminders/                    # Создать напоминание
GET    /api/v1/reminders/                    # Список своих напоминаний (?status=pending|sent)
GET    /api/v1/reminders/{id}/               # Получить напоминание
PATCH  /api/v1/reminders/{id}/               # Обновить
DELETE /api/v1/reminders/{id}/               # Удалить
POST   /api/v1/reminders/{id}/snooze/        # Отложить (5, 10, 30, 60 минут)

GET    /api/v1/blocks/{block_id}/reminder/   # Получить напоминание блока (или 404)
```

### 3.2 Подписки

```
POST   /api/v1/subscriptions/                # Создать подписку
GET    /api/v1/subscriptions/                # Список своих подписок
GET    /api/v1/subscriptions/{id}/           # Получить подписку
PATCH  /api/v1/subscriptions/{id}/           # Обновить
DELETE /api/v1/subscriptions/{id}/           # Удалить

GET    /api/v1/blocks/{block_id}/subscription/  # Получить подписку на блок (или 404)
```

### 3.3 Настройки уведомлений

```
GET    /api/v1/notifications/settings/       # Получить настройки
PATCH  /api/v1/notifications/settings/       # Обновить настройки

# Telegram
GET    /api/v1/notifications/telegram/status/   # Статус привязки
POST   /api/v1/notifications/telegram/link/     # Получить ссылку для привязки
POST   /api/v1/notifications/telegram/unlink/   # Отвязать
POST   /api/v1/notifications/telegram/test/     # Тестовое сообщение

# Push
POST   /api/v1/notifications/push/subscribe/    # Подписаться на push
POST   /api/v1/notifications/push/unsubscribe/  # Отписаться
POST   /api/v1/notifications/push/test/         # Тестовое уведомление
```

### 3.4 Эндпоинты для Telegram бота (внутренние, с X-Bot-Secret)

```
POST   /api/v1/internal/telegram/link/          # Привязка chat_id по токену
POST   /api/v1/internal/telegram/unlink/        # Отвязка по chat_id
GET    /api/v1/internal/telegram/status/        # Проверка привязки по chat_id
POST   /api/v1/internal/reminders/{id}/snooze/  # Отложить напоминание
DELETE /api/v1/internal/reminders/{id}/         # Отменить напоминание
```

---

## 4. Celery Tasks

### 4.1 Проверка напоминаний (каждую минуту)

```python
@shared_task
def check_pending_reminders():
    """Проверяет и отправляет напоминания"""
    now = timezone.now()

    reminders = BlockReminder.objects.filter(
        is_sent=False,
        remind_at__lte=now,
        snoozed_until__isnull=True  # Не отложенные
    ).select_related('block', 'user', 'user__notification_settings')

    # + отложенные, у которых время вышло
    snoozed = BlockReminder.objects.filter(
        is_sent=False,
        snoozed_until__lte=now
    ).select_related('block', 'user', 'user__notification_settings')

    for reminder in list(reminders) + list(snoozed):
        send_reminder_notification.delay(str(reminder.id))
```

### 4.2 Отправка напоминания

```python
@shared_task
def send_reminder_notification(reminder_id: str):
    """Отправляет напоминание через все каналы"""
    reminder = BlockReminder.objects.select_related(
        'block', 'user', 'user__notification_settings'
    ).get(id=reminder_id)

    # Проверить тихие часы
    if is_quiet_hours(reminder.user):
        # Перенести на конец тихих часов
        reschedule_for_after_quiet_hours(reminder)
        return

    settings = reminder.user.notification_settings
    block_text = reminder.block.data.get('text', '')[:200]
    block_url = f"{settings.FRONTEND_URL}/block/{reminder.block.id}"

    sent_via = []

    # 1. Telegram (приоритет)
    if settings.telegram_enabled and settings.telegram_chat_id:
        success = send_telegram_reminder(
            chat_id=settings.telegram_chat_id,
            reminder_id=str(reminder.id),
            block_text=block_text,
            message=reminder.message,
            block_url=block_url
        )
        if success:
            sent_via.append('telegram')

    # 2. Push
    if settings.push_enabled and settings.push_subscription:
        send_push_notification(
            subscription=settings.push_subscription,
            title="Напоминание",
            body=block_text[:100],
            url=block_url
        )
        sent_via.append('push')

    # 3. Email (fallback или always)
    if should_send_email(settings, sent_via):
        send_email_reminder(
            email=reminder.user.email,
            block_text=block_text,
            message=reminder.message,
            block_url=block_url
        )
        sent_via.append('email')

    # Пометить как отправленное
    reminder.is_sent = True
    reminder.sent_at = timezone.now()
    reminder.snoozed_until = None
    reminder.save()

    # Для повторяющихся — создать следующее
    if reminder.repeat != 'none':
        create_next_reminder(reminder)
```

### 4.3 Уведомление об изменении блока

```python
@shared_task
def notify_block_change(block_id: str, change_type: str, changed_by_user_id: int):
    """Собирает подписчиков и отправляет уведомления"""
    block = Block.objects.get(id=block_id)

    # Найти все подписки, которые покрывают этот блок
    # (включая родительские с глубиной > 0)
    subscriptions = find_subscriptions_for_block(block, change_type)

    for sub in subscriptions:
        # Не уведомлять автора изменения
        if sub.user_id == changed_by_user_id:
            continue

        # Rate limiting: не чаще 1 раз в минуту
        if not can_send_notification(sub):
            # Добавить в очередь для агрегации
            PendingNotification.objects.create(
                user=sub.user,
                subscription=sub,
                block=block,
                change_type=change_type,
                changed_by_id=changed_by_user_id
            )
            continue

        send_change_notification.delay(
            subscription_id=str(sub.id),
            block_id=str(block.id),
            change_type=change_type,
            changed_by_user_id=changed_by_user_id
        )
```

### 4.4 Агрегация уведомлений (каждую минуту)

```python
@shared_task
def process_pending_notifications():
    """Отправляет агрегированные уведомления"""
    # Группировать по (user, subscription)
    from django.db.models import Count

    groups = PendingNotification.objects.values(
        'user_id', 'subscription_id'
    ).annotate(count=Count('id'))

    for group in groups:
        pending = PendingNotification.objects.filter(
            user_id=group['user_id'],
            subscription_id=group['subscription_id']
        ).select_related('block', 'changed_by')

        if pending.count() == 1:
            # Одно изменение — отправить как обычно
            p = pending.first()
            send_change_notification.delay(...)
        else:
            # Несколько — агрегировать
            send_aggregated_notification.delay(
                user_id=group['user_id'],
                subscription_id=group['subscription_id'],
                notification_ids=[str(p.id) for p in pending]
            )

        pending.delete()
```

### 4.5 Celery Beat Schedule

```python
# block_api/celery.py

app.conf.beat_schedule = {
    'check-reminders-every-minute': {
        'task': 'api.tasks.check_pending_reminders',
        'schedule': crontab(minute='*'),
    },
    'process-pending-notifications': {
        'task': 'api.tasks.process_pending_notifications',
        'schedule': crontab(minute='*'),
    },
    'cleanup-expired-telegram-tokens': {
        'task': 'api.tasks.cleanup_expired_telegram_tokens',
        'schedule': crontab(hour='*/6'),  # каждые 6 часов
    },
}
```

---

## 5. Интеграция с существующими views

### 5.1 При изменении блока — триггерить уведомление

В `api/views.py` при обновлении блока:

```python
# После успешного сохранения блока
from api.tasks import notify_block_change

def update_block(request, block_id):
    # ... существующий код ...

    block.save()

    # Определить тип изменения
    if 'text' in changed_fields:
        notify_block_change.delay(str(block.id), 'text_change', request.user.id)
    elif changed_fields:  # другие поля в data
        notify_block_change.delay(str(block.id), 'data_change', request.user.id)
```

### 5.2 При создании/удалении дочерних блоков

```python
def create_block(request, parent_id):
    # ... создание блока ...

    if parent_block:
        notify_block_change.delay(str(parent_block.id), 'child_add', request.user.id)

def delete_block(request, block_id):
    parent_id = block.parent_id
    # ... удаление ...

    if parent_id:
        notify_block_change.delay(str(parent_id), 'child_delete', request.user.id)
```

### 5.3 При перемещении блока

```python
def move_block(request, block_id):
    # ... перемещение ...

    notify_block_change.delay(str(block.id), 'move', request.user.id)
```

---

## 6. Проверка прав при подписке

Подписаться может только пользователь с правами view, edit, edit_ac или delete на блок:

```python
# api/views_subscriptions.py

from api.models import BlockPermission

def can_subscribe_to_block(user, block):
    """Проверяет, может ли пользователь подписаться на блок"""
    # Будет рефакториться в будущем
    allowed_permissions = ['view', 'edit', 'edit_ac', 'delete']

    permission = BlockPermission.objects.filter(
        block=block,
        user=user,
        permission__in=allowed_permissions
    ).first()

    return permission is not None
```

---

## 7. Отправка в Telegram

```python
# api/services/telegram.py

import httpx
from django.conf import settings

TELEGRAM_API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

def send_telegram_reminder(chat_id: str, reminder_id: str, block_text: str,
                          message: str, block_url: str) -> bool:
    """Отправляет напоминание в Telegram"""
    text = f"⏰ <b>Напоминание</b>\n\n{block_text}"
    if message:
        text += f"\n\n💬 {message}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Открыть блок", "url": block_url}],
            [
                {"text": "5 мин", "callback_data": f"snooze:{reminder_id}:5"},
                {"text": "10 мин", "callback_data": f"snooze:{reminder_id}:10"},
                {"text": "30 мин", "callback_data": f"snooze:{reminder_id}:30"},
                {"text": "1 час", "callback_data": f"snooze:{reminder_id}:60"},
            ]
        ]
    }

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{TELEGRAM_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": keyboard
                }
            )
            return response.status_code == 200
    except Exception:
        return False


def send_telegram_change_notification(chat_id: str, block_text: str,
                                      change_type: str, changed_by: str,
                                      block_url: str) -> bool:
    """Уведомление об изменении блока"""
    change_labels = {
        'text_change': 'Изменён текст',
        'data_change': 'Изменены свойства',
        'move': 'Блок перемещён',
        'child_add': 'Добавлен дочерний блок',
        'child_delete': 'Удалён дочерний блок',
    }

    text = f"📝 <b>{change_labels.get(change_type, 'Изменение')}</b>\n\n"
    text += f"«{block_text}»\n\n"
    text += f"👤 {changed_by}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Открыть блок", "url": block_url}]
        ]
    }

    # ... отправка аналогично ...


def send_telegram_reminder_created(chat_id: str, reminder_id: str,
                                   block_text: str, remind_at: datetime,
                                   repeat: str, block_url: str) -> bool:
    """Подтверждение создания напоминания"""
    delta = remind_at - timezone.now()
    hours, remainder = divmod(delta.seconds, 3600)
    minutes = remainder // 60

    time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
    delta_str = f"{hours}ч {minutes}мин" if hours else f"{minutes} мин"

    text = f"🔔 <b>Напоминание создано</b>\n\n"
    text += f"📝 {block_text}\n\n"
    text += f"⏰ Сработает: {time_str}\n"
    text += f"   (через {delta_str})"

    if repeat != 'none':
        repeat_labels = {'daily': 'ежедневно', 'weekly': 'еженедельно', 'monthly': 'ежемесячно'}
        text += f"\n🔄 Повтор: {repeat_labels[repeat]}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Отменить", "callback_data": f"cancel:{reminder_id}"}]
        ]
    }

    # ... отправка ...
```

---

## 8. Переменные окружения

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_BOT_SECRET=random_secret_for_internal_api
TELEGRAM_BOT_USERNAME=OmniMapBot

# Email
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=noreply@omnimap.ru
EMAIL_HOST_PASSWORD=app_password
DEFAULT_FROM_EMAIL=OmniMap <noreply@omnimap.ru>

# Push (VAPID keys)
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_ADMIN_EMAIL=admin@omnimap.ru
```

---

## 9. Чеклист задач

### Этап 1: Модели и миграции
- [x] Создать модель BlockReminder
- [x] Создать модель BlockChangeSubscription
- [x] Создать модель UserNotificationSettings
- [x] Создать модель TelegramLinkToken
- [x] Создать модель PendingNotification
- [x] Добавить индексы
- [x] Создать и применить миграции

### Этап 2: API напоминаний
- [x] ReminderSerializer
- [x] ReminderViewSet (CRUD)
- [x] Endpoint POST /reminders/{id}/snooze/
- [x] Endpoint GET /blocks/{block_id}/reminder/
- [x] Валидация лимитов
- [x] Тесты

### Этап 3: API подписок
- [x] SubscriptionSerializer
- [x] SubscriptionViewSet (CRUD)
- [x] Endpoint GET /blocks/{block_id}/subscription/
- [x] Проверка прав на блок
- [x] Валидация лимитов
- [x] Тесты

### Этап 4: API настроек уведомлений
- [x] NotificationSettingsSerializer
- [x] GET/PATCH /notifications/settings/
- [x] Telegram endpoints (status, link, unlink, test)
- [x] Push endpoints (subscribe, unsubscribe, test)
- [x] Тесты

### Этап 5: Telegram интеграция
- [x] Сервис отправки сообщений (telegram.py)
- [x] Генерация link token
- [x] Internal API для бота
- [x] Отправка напоминаний
- [x] Отправка уведомлений об изменениях
- [x] Отправка подтверждения создания
- [x] Тесты

### Этап 6: Celery tasks
- [x] check_pending_reminders (каждую минуту)
- [x] send_reminder_notification
- [x] notify_block_change
- [x] process_pending_notifications
- [x] cleanup_expired_telegram_tokens
- [x] Настроить Celery Beat

### Этап 7: Интеграция с views
- [x] Вызов notify_block_change при update блока
- [x] Вызов при создании дочернего блока
- [x] Вызов при удалении блока
- [x] Вызов при перемещении блока

### Этап 8: Email
- [ ] Сервис отправки email
- [ ] Шаблоны писем
- [x] Логика fallback/always (реализована, но отправка email требует доработки)

### Этап 9: Push уведомления
- [ ] Генерация VAPID ключей
- [ ] Сервис отправки push (pywebpush)
- [x] Endpoint подписки
- [ ] Тесты push

### Этап 10: Дополнительно
- [x] Тихие часы (проверка и перенос)
- [x] Повторяющиеся напоминания (создание следующего)
- [x] Агрегация уведомлений
- [ ] Документация API
