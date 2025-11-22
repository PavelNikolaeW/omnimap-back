from datetime import datetime
from pprint import pprint

from celery import shared_task
from django.contrib.auth import get_user_model
from django.db.models import Q
from kombu import Connection, Exchange, Producer
from django.conf import settings
import json
from django.db import connection

from api.models import Group, BlockLink, Block
from api.serializers import get_object_for_block
from api.services.import_blocks import import_blocks
from api.utils.query import recursive_set_block_access_query, recursive_set_block_group_access_query
from django.contrib.postgres.aggregates import ArrayAgg

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
    try:
        user = User.objects.get(id=user_id)
        rep = import_blocks(payload_blocks=payload, user=user, default_permissions=default_perms, task=self)
        if not rep:
            return False

        blocks_update = set()
        blocks_update.update(rep.created, rep.updated)

        if blocks_update:
            send_message_blocks_update.delay(block_uuids=list(blocks_update))
        if rep.deleted:
            send_message_unsubscribe_user.delay(block_uuids=[str(bid) for bid in rep.deleted])
        if rep.permissions_upserted:
            for user_id, perm_bids in rep.permissions_upserted.items():
                for permission, bids in perm_bids.items():
                    send_message_access_update.delay(
                        block_uuids=bids,
                        user_id=user_id,
                        permission=permission,
                        start_block_ids=[],
                    )


    #     todo тут же вызываем таски и подписываем пользователей на блоки
    except Exception as e:
        print(f"Error in import_blocks_task: {e}")
        self.retry(exc=e, countdown=5)
        self.update_state(status='ERROR', meta={'result': e})
