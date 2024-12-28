# myapp/tasks.py

from celery import shared_task
from kombu import Connection, Exchange, Producer
from django.conf import settings
import uuid
import json

# Настройки RabbitMQ
RABBITMQ_URL = settings.CELERY_BROKER_URL
EXCHANGE_NAME = settings.RABBITMQ_EXCHANGES
QUEUE_NAME = settings.RABBITMQ_QUEUE
ROUTING_KEY = settings.RABBITMQ_ROUTING_KEY

# Создаем Exchange и Producer
exchange = Exchange(EXCHANGE_NAME, type='direct')


# celery -A block_api worker --loglevel=info

@shared_task(bind=True, max_retries=3)
def send_message_block_update(self, block_uuid, block_data):
    block_data = {
        'id': str(block_data['id']),
        'title': block_data['title'],
        'data': json.dumps(block_data['data']),
        'updated_at': str(block_data['updated_at']),
        'children': json.dumps(block_data['children'])
    }
    print(block_data)
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
def send_message_subscribe_user(self, block_uuids, user_id):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'subscribe',
                'block_uuids': block_uuids,
                'user_id': user_id
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
def send_message_access_update(self, block_uuid, user_ids):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'update_access',
                'block_uuid': str(block_uuid),
                'user_ids': user_ids
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
