from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from pprint import pprint
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import Q, Count
from kombu import Connection, Exchange, Producer
from django.conf import settings
import json
from django.db import connection
from django.utils import timezone

from api.models import (
    Group, BlockLink, Block, BlockReminder, BlockChangeSubscription,
    UserNotificationSettings, TelegramLinkToken, PendingNotification
)
from api.serializers import get_object_for_block
from api.services.import_blocks import import_blocks
from api.utils.query import recursive_set_block_access_query, recursive_set_block_group_access_query
from django.contrib.postgres.aggregates import ArrayAgg

logger = logging.getLogger(__name__)

# Настройки RabbitMQ
RABBITMQ_URL = settings.CELERY_BROKER_URL
EXCHANGE_NAME = settings.RABBITMQ_EXCHANGES
QUEUE_NAME = settings.RABBITMQ_QUEUE
ROUTING_KEY = settings.RABBITMQ_ROUTING_KEY

# Создаем Exchange и Producer
exchange = Exchange(EXCHANGE_NAME, type='direct')
User = get_user_model()

# celery -A block_api worker --loglevel=info
# TODO очищать redis от id старых задач
@shared_task(bind=True, max_retries=3)
def send_message_block_update(self, block_uuid, block_data):
    block_data = {
        'id': str(block_data['id']),
        'title': block_data['title'] or '',
        'data': json.dumps(block_data['data']),
        'parent_id': block_data['parent_id'],
        'updated_at': int(block_data['updated_at'].timestamp()),
        'children': json.dumps(block_data['children'])
    }
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'update_block',
                'block_uuid': str(block_uuid),
                'block_data': block_data,
            }
            producer.publish(
                message,
                exchange=exchange,
                routing_key=ROUTING_KEY,
                serializer='json',
                declare=[exchange],
            )
    except Exception as e:
        # Обработка ошибок: логирование, повторные попытки и т.д.
        print(f'Error sending: {e}')
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def send_message_blocks_update(self, block_uuids):
    rows = (
        Block.objects
        .filter(id__in=block_uuids)
        .annotate(child_ids=ArrayAgg('children__id', distinct=True))
        .values('id', 'title', 'data', 'parent_id', 'updated_at', 'child_ids')
    )

    blocks = {
        str(r['id']): {
            'id': str(r['id']),
            'title': r['title'] if r['title'] else '',
            'data': json.dumps(r['data']),
            'parent_id': str(r['parent_id']) if r['parent_id'] else json.dumps(False),
            'updated_at': int(r['updated_at'].timestamp()),
            'children': json.dumps([str(cid) for cid in r['child_ids'] if cid])
        }
        for r in rows
    }
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'update_blocks',
                'blocks': blocks
            }
            producer.publish(
                message,
                exchange=exchange,
                routing_key=ROUTING_KEY,
                serializer='json',
                declare=[exchange],
            )
    except Exception as e:
        # Обработка ошибок: логирование, повторные попытки и т.д.
        print(f'Error sending: {e}')
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def send_message_subscribe_user(self, block_uuids, user_ids):
    """
    Отправляет уведомления о подписке пользователям.

    :param block_uuids: список UUID блоков
    :param user_ids: список ID пользователей
    """
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)

            # Подготовка списка сообщений
            messages = [
                {
                    'action': 'subscribe',
                    'block_uuids': block_uuids,
                    'user_id': user_id
                }
                for user_id in user_ids
            ]

            for message in messages:
                producer.publish(
                    message,
                    exchange=exchange,
                    routing_key=ROUTING_KEY,
                    serializer='json',
                    declare=[exchange]
                )

    except Exception as e:
        print(f'Error sending: {e}')
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def send_message_unsubscribe_user(self, block_uuids):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            producer.publish(
                {
                    'action': 'unsubscribe',
                    'block_uuids': block_uuids,
                },
                exchange=exchange,
                routing_key=ROUTING_KEY,
                serializer='json',
                declare=[exchange]
            )

    except Exception as e:
        print(f'Error sending: {e}')
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def send_message_access_update(self, block_uuids, user_id, permission, start_block_ids, group_id=0):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            ids = [user_id]
            if user_id == 0:
                ids = Group.objects.filter(id=group_id).values_list('users', flat=True)
            for user in ids:
                message = {
                    'action': 'update_access',
                    'block_uuids': block_uuids,
                    'user_id': user,
                    'permission': permission,
                    'start_block_ids': [str(id) for id in start_block_ids]
                }
                producer.publish(
                    message,
                    exchange=exchange,
                    routing_key=ROUTING_KEY,
                    serializer='json',
                    declare=[exchange]
                )
    except Exception as e:
        print(f'Error sending: {e}')
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def set_block_permissions_task(self, initiator_id, target_user_id, block_id, new_permission):
    try:
        with (connection.cursor() as cursor):
            start_block_ids = [block_id]
            while True:
                cursor.execute(
                    recursive_set_block_access_query,
                    {
                        'target_user_id': target_user_id,
                        'start_block_ids': start_block_ids,
                        'initiator_id': initiator_id,
                        'new_permission': new_permission
                    }
                )
                changed_block_ids = [str(row[0]) for row in cursor.fetchall()]
                send_message_access_update.delay(
                    block_uuids=changed_block_ids,
                    user_id=target_user_id,
                    permission=new_permission,
                    start_block_ids=start_block_ids
                )
                links = BlockLink.objects.filter(target__id__in=changed_block_ids)
                start_block_ids = list(links.values_list('source_id', flat=True))
                if not start_block_ids:
                    break

    except Exception as e:
        print(f"Error in set_block_permissions_task: {e}")
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def set_block_group_permissions_task(self, initiator_id, group_id, block_id, new_permission):
    try:
        with connection.cursor() as cursor:
            start_block_ids = [block_id]
            while True:
                cursor.execute(
                    recursive_set_block_group_access_query,
                    {
                        'group_id': group_id,
                        'start_block_ids': start_block_ids,
                        'initiator_id': initiator_id,
                        'new_permission': new_permission
                    }
                )
                changed_block_ids = [str(row[0]) for row in cursor.fetchall()]
                send_message_access_update.delay(
                    block_uuids=changed_block_ids,
                    user_id=0,
                    permission=new_permission,
                    start_block_ids=start_block_ids,
                    group_id=group_id
                )
                links = BlockLink.objects.filter(target__id__in=changed_block_ids)
                start_block_ids = list(links.values_list('source_id', flat=True))
                if not start_block_ids:
                    break

    except Exception as e:
        print(f"Error in set_block_group_permissions_task: {e}")
        self.retry(exc=e, countdown=5)


@shared_task(bind=True, max_retries=3)
def import_blocks_task(self, payload, user_id, default_perms):
    """
    Асинхронный импорт блоков.

    Возвращает словарь с результатами:
    - created: список UUID созданных блоков
    - updated: список UUID обновлённых блоков
    - unchanged: список UUID без изменений
    - deleted: список UUID удалённых блоков
    - errors: список кодов ошибок
    - problem_blocks: список проблемных блоков с кодами ошибок
    """
    def build_result(rep):
        """Формирует сериализуемый результат из ImportReport."""
        return {
            'created': [str(bid) for bid in rep.created],
            'updated': [str(bid) for bid in rep.updated],
            'unchanged': [str(bid) for bid in rep.unchanged],
            'deleted': [str(bid) for bid in rep.deleted],
            'permissions_upserted': {
                str(uid): {perm: bids for perm, bids in perms.items()}
                for uid, perms in rep.permissions_upserted.items()
            },
            'links_upserted': rep.links_upserted,
            'errors': list(rep.errors),
            'problem_blocks': [
                {'block_id': str(item.block_id), 'code': item.code}
                for item in rep.problem_blocks
            ]
        }

    try:
        # Обновляем статус: начало выполнения
        self.update_state(
            state='PROGRESS',
            meta={'stage': 'starting', 'progress': 0}
        )

        user = User.objects.get(id=user_id)

        # Обновляем статус: импорт блоков
        self.update_state(
            state='PROGRESS',
            meta={'stage': 'importing', 'progress': 10, 'total_blocks': len(payload)}
        )

        rep = import_blocks(
            payload_blocks=payload,
            user=user,
            default_permissions=default_perms,
            task=self
        )

        if not rep:
            return {
                'success': False,
                'error': 'Import returned empty result',
                'created': [],
                'updated': [],
                'unchanged': [],
                'deleted': [],
                'errors': ['import_failed'],
                'problem_blocks': []
            }

        # Обновляем статус: отправка уведомлений
        self.update_state(
            state='PROGRESS',
            meta={'stage': 'notifications', 'progress': 80}
        )

        blocks_update = set()
        blocks_update.update(rep.created, rep.updated)

        if blocks_update:
            send_message_blocks_update.delay(block_uuids=list(blocks_update))
        if rep.deleted:
            send_message_unsubscribe_user.delay(block_uuids=[str(bid) for bid in rep.deleted])
        if rep.permissions_upserted:
            for uid, perm_bids in rep.permissions_upserted.items():
                for permission, bids in perm_bids.items():
                    send_message_access_update.delay(
                        block_uuids=bids,
                        user_id=uid,
                        permission=permission,
                        start_block_ids=[],
                    )

        # Формируем результат
        result = build_result(rep)
        result['success'] = len(rep.problem_blocks) == 0

        # Возвращаем результат — Celery сохранит его и установит статус SUCCESS
        return result

    except User.DoesNotExist:
        return {
            'success': False,
            'error': f'User with id={user_id} not found',
            'created': [],
            'updated': [],
            'unchanged': [],
            'deleted': [],
            'errors': ['user_not_found'],
            'problem_blocks': []
        }

    except Exception as e:
        # При ошибке пробуем повторить
        if self.request.retries < self.max_retries:
            self.retry(exc=e, countdown=5)

        # Если все попытки исчерпаны, возвращаем ошибку
        return {
            'success': False,
            'error': str(e),
            'created': [],
            'updated': [],
            'unchanged': [],
            'deleted': [],
            'errors': ['exception'],
            'problem_blocks': []
        }


# ============================================================================
# Tasks для напоминаний и уведомлений
# ============================================================================

def is_quiet_hours(user) -> bool:
    """Проверяет, находится ли пользователь в тихих часах."""
    try:
        settings_obj = user.notification_settings
        if not settings_obj.quiet_hours_enabled:
            return False

        if not settings_obj.quiet_hours_start or not settings_obj.quiet_hours_end:
            return False

        import pytz
        user_tz = pytz.timezone(settings_obj.timezone)
        now = timezone.now().astimezone(user_tz).time()

        start = settings_obj.quiet_hours_start
        end = settings_obj.quiet_hours_end

        if start <= end:
            return start <= now <= end
        else:
            # Ночной период (например, 23:00 - 08:00)
            return now >= start or now <= end
    except (UserNotificationSettings.DoesNotExist, Exception):
        return False


def should_send_email(settings_obj, sent_via: list) -> bool:
    """Определяет, нужно ли отправлять email."""
    if not settings_obj.email_enabled:
        return False

    if settings_obj.email_mode == 'off':
        return False
    elif settings_obj.email_mode == 'always':
        return True
    elif settings_obj.email_mode == 'fallback':
        return 'telegram' not in sent_via

    return False


def create_next_reminder(reminder):
    """Создаёт следующее напоминание для повторяющегося."""
    if reminder.repeat == 'daily':
        next_time = reminder.remind_at + timedelta(days=1)
    elif reminder.repeat == 'weekly':
        next_time = reminder.remind_at + timedelta(weeks=1)
    elif reminder.repeat == 'monthly':
        next_time = reminder.remind_at + relativedelta(months=1)
    else:
        return

    BlockReminder.objects.create(
        block=reminder.block,
        user=reminder.user,
        remind_at=next_time,
        timezone=reminder.timezone,
        message=reminder.message,
        repeat=reminder.repeat,
        is_sent=False
    )


@shared_task
def check_pending_reminders():
    """Проверяет и отправляет напоминания (запускается каждую минуту)."""
    now = timezone.now()

    # Напоминания, которые пора отправить
    reminders = BlockReminder.objects.filter(
        is_sent=False,
        remind_at__lte=now,
        snoozed_until__isnull=True
    ).select_related('block', 'user')

    # Отложенные напоминания, время которых вышло
    snoozed = BlockReminder.objects.filter(
        is_sent=False,
        snoozed_until__lte=now
    ).select_related('block', 'user')

    for reminder in list(reminders) | set(snoozed):
        send_reminder_notification.delay(str(reminder.id))


@shared_task(bind=True, max_retries=3)
def send_reminder_notification(self, reminder_id: str):
    """Отправляет напоминание через все настроенные каналы."""
    try:
        reminder = BlockReminder.objects.select_related(
            'block', 'user'
        ).get(id=reminder_id)
    except BlockReminder.DoesNotExist:
        logger.warning(f"Reminder {reminder_id} not found")
        return

    # Проверяем тихие часы
    if is_quiet_hours(reminder.user):
        # Перенести на конец тихих часов - упрощённая логика
        reminder.snoozed_until = timezone.now() + timedelta(hours=8)
        reminder.save()
        return

    try:
        settings_obj = reminder.user.notification_settings
    except UserNotificationSettings.DoesNotExist:
        settings_obj = None

    block_text = reminder.block.data.get('text', reminder.block.title or '')[:200]
    block_url = f"{settings.FRONTEND_HOST}/block/{reminder.block.id}"

    sent_via = []

    # 1. Telegram (приоритет)
    if settings_obj and settings_obj.telegram_enabled and settings_obj.telegram_chat_id:
        from api.services.telegram import send_telegram_reminder
        success = send_telegram_reminder(
            chat_id=settings_obj.telegram_chat_id,
            reminder_id=str(reminder.id),
            block_text=block_text,
            message=reminder.message,
            block_url=block_url
        )
        if success:
            sent_via.append('telegram')

    # 2. Push уведомление
    if settings_obj and settings_obj.push_enabled and settings_obj.push_subscription:
        # TODO: Реализовать pywebpush
        sent_via.append('push')

    # 3. Email (fallback или always)
    if settings_obj and should_send_email(settings_obj, sent_via):
        # TODO: Реализовать отправку email
        sent_via.append('email')

    # Пометить как отправленное
    reminder.is_sent = True
    reminder.sent_at = timezone.now()
    reminder.snoozed_until = None
    reminder.save()

    # Для повторяющихся — создать следующее
    if reminder.repeat != 'none':
        create_next_reminder(reminder)

    logger.info(f"Reminder {reminder_id} sent via: {sent_via}")


def find_subscriptions_for_block(block, change_type: str):
    """Находит все подписки, которые покрывают данный блок и тип изменения."""
    subscriptions = []

    # Фильтр по типу изменения
    type_filter = Q()
    if change_type == 'text_change':
        type_filter = Q(on_text_change=True)
    elif change_type == 'data_change':
        type_filter = Q(on_data_change=True)
    elif change_type == 'move':
        type_filter = Q(on_move=True)
    elif change_type == 'child_add':
        type_filter = Q(on_child_add=True)
    elif change_type == 'child_delete':
        type_filter = Q(on_child_delete=True)

    # Подписки непосредственно на этот блок
    direct_subs = BlockChangeSubscription.objects.filter(
        block=block
    ).filter(type_filter).select_related('user')
    subscriptions.extend(direct_subs)

    # Подписки на родительские блоки с нужной глубиной
    current = block
    depth = 1
    while current.parent:
        parent = current.parent
        parent_subs = BlockChangeSubscription.objects.filter(
            block=parent
        ).filter(type_filter).filter(
            Q(depth=-1) | Q(depth__gte=depth)
        ).select_related('user')
        subscriptions.extend(parent_subs)

        current = parent
        depth += 1
        if depth > 100:  # Защита от бесконечного цикла
            break

    return subscriptions


def can_send_notification(subscription) -> bool:
    """Проверяет rate limiting для подписки."""
    if not subscription.last_notification_at:
        return True

    min_interval = settings.MIN_NOTIFICATION_INTERVAL_SECONDS
    elapsed = (timezone.now() - subscription.last_notification_at).total_seconds()
    return elapsed >= min_interval


@shared_task(bind=True, max_retries=3)
def notify_block_change(self, block_id: str, change_type: str, changed_by_user_id: int):
    """Собирает подписчиков и отправляет уведомления."""
    try:
        block = Block.objects.get(id=block_id)
    except Block.DoesNotExist:
        logger.warning(f"Block {block_id} not found")
        return

    subscriptions = find_subscriptions_for_block(block, change_type)

    for sub in subscriptions:
        # Не уведомлять автора изменения
        if sub.user_id == changed_by_user_id:
            continue

        # Rate limiting
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

        # Обновляем время последнего уведомления
        sub.last_notification_at = timezone.now()
        sub.save(update_fields=['last_notification_at'])


@shared_task(bind=True, max_retries=3)
def send_change_notification(self, subscription_id: str, block_id: str,
                             change_type: str, changed_by_user_id: int):
    """Отправляет уведомление об изменении блока."""
    try:
        subscription = BlockChangeSubscription.objects.select_related(
            'user', 'block'
        ).get(id=subscription_id)
        block = Block.objects.get(id=block_id)
        changed_by = User.objects.get(id=changed_by_user_id)
    except (BlockChangeSubscription.DoesNotExist, Block.DoesNotExist, User.DoesNotExist) as e:
        logger.warning(f"Objects not found: {e}")
        return

    user = subscription.user

    # Проверяем тихие часы
    if is_quiet_hours(user):
        # Добавляем в pending для отправки позже
        PendingNotification.objects.create(
            user=user,
            subscription=subscription,
            block=block,
            change_type=change_type,
            changed_by=changed_by
        )
        return

    try:
        settings_obj = user.notification_settings
    except UserNotificationSettings.DoesNotExist:
        return

    block_text = block.data.get('text', block.title or '')[:200]
    block_url = f"{settings.FRONTEND_HOST}/block/{block.id}"

    # Telegram
    if settings_obj.telegram_enabled and settings_obj.telegram_chat_id:
        from api.services.telegram import send_telegram_change_notification
        send_telegram_change_notification(
            chat_id=settings_obj.telegram_chat_id,
            block_text=block_text,
            change_type=change_type,
            changed_by=changed_by.username,
            block_url=block_url
        )


@shared_task
def process_pending_notifications():
    """Отправляет агрегированные уведомления (запускается каждую минуту)."""
    # Группируем по (user, subscription)
    groups = PendingNotification.objects.values(
        'user_id', 'subscription_id'
    ).annotate(count=Count('id'))

    for group in groups:
        user_id = group['user_id']
        subscription_id = group['subscription_id']
        count = group['count']

        pending = PendingNotification.objects.filter(
            user_id=user_id,
            subscription_id=subscription_id
        ).select_related('block', 'changed_by', 'subscription')

        if count == 1:
            # Одно изменение — отправить как обычно
            p = pending.first()
            if p:
                send_change_notification.delay(
                    subscription_id=str(p.subscription_id),
                    block_id=str(p.block_id),
                    change_type=p.change_type,
                    changed_by_user_id=p.changed_by_id
                )
        else:
            # Несколько — агрегировать
            first_pending = pending.first()
            if first_pending:
                send_aggregated_notification.delay(
                    user_id=user_id,
                    subscription_id=str(subscription_id),
                    block_id=str(first_pending.block_id),
                    changes_count=count
                )

        pending.delete()


@shared_task(bind=True, max_retries=3)
def send_aggregated_notification(self, user_id: int, subscription_id: str,
                                  block_id: str, changes_count: int):
    """Отправляет агрегированное уведомление о множественных изменениях."""
    try:
        user = User.objects.get(id=user_id)
        block = Block.objects.get(id=block_id)
    except (User.DoesNotExist, Block.DoesNotExist) as e:
        logger.warning(f"Objects not found: {e}")
        return

    try:
        settings_obj = user.notification_settings
    except UserNotificationSettings.DoesNotExist:
        return

    block_text = block.data.get('text', block.title or '')[:200]
    block_url = f"{settings.FRONTEND_HOST}/block/{block.id}"

    if settings_obj.telegram_enabled and settings_obj.telegram_chat_id:
        from api.services.telegram import send_telegram_aggregated_notification
        send_telegram_aggregated_notification(
            chat_id=settings_obj.telegram_chat_id,
            block_text=block_text,
            changes_count=changes_count,
            block_url=block_url
        )


@shared_task
def cleanup_expired_telegram_tokens():
    """Очищает просроченные токены привязки Telegram (запускается каждые 6 часов)."""
    expired = TelegramLinkToken.objects.filter(
        Q(expires_at__lt=timezone.now()) | Q(used=True)
    )
    count = expired.count()
    expired.delete()
    logger.info(f"Cleaned up {count} expired/used Telegram tokens")
