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
def send_message_block_update(self, block_id, data):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'update',
                'block': str(block_id),
                'data': data,
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
def send_message_subscribe_user(self, block_ids, user):
    try:
        with Connection(RABBITMQ_URL) as conn:
            producer = Producer(conn)
            message = {
                'action': 'subscribe',
                'block_ids': block_ids,
                'user': str(user)
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
