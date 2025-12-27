"""
Push уведомления через Web Push API (pywebpush)
"""
import json
import logging
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def get_vapid_keys() -> tuple[str, str]:
    """Получить VAPID ключи из настроек"""
    public_key = getattr(settings, 'VAPID_PUBLIC_KEY', '')
    private_key = getattr(settings, 'VAPID_PRIVATE_KEY', '')
    return public_key, private_key


def send_push_notification(
    subscription_info: dict,
    title: str,
    body: str,
    url: Optional[str] = None,
    icon: Optional[str] = None,
    tag: Optional[str] = None,
) -> bool:
    """
    Отправить Push уведомление через Web Push API.

    Args:
        subscription_info: Объект подписки от браузера (endpoint, keys.p256dh, keys.auth)
        title: Заголовок уведомления
        body: Текст уведомления
        url: URL для перехода при клике
        icon: URL иконки
        tag: Тег для группировки уведомлений

    Returns:
        True если отправлено успешно, False при ошибке
    """
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error("pywebpush not installed. Run: pip install pywebpush")
        return False

    public_key, private_key = get_vapid_keys()

    if not public_key or not private_key:
        logger.error("VAPID keys not configured. Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY in settings")
        return False

    if not subscription_info:
        logger.warning("Empty subscription_info provided")
        return False

    # Формируем payload для уведомления
    payload = {
        'title': title,
        'body': body,
        'icon': icon or '/static/icons/notification-icon.png',
        'badge': '/static/icons/badge-icon.png',
        'data': {
            'url': url,
        }
    }

    if tag:
        payload['tag'] = tag

    vapid_claims = {
        'sub': f"mailto:{getattr(settings, 'VAPID_CONTACT_EMAIL', 'admin@omnimap.ru')}"
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims=vapid_claims,
        )
        logger.info(f"Push notification sent successfully: {title}")
        return True
    except WebPushException as e:
        if e.response and e.response.status_code == 410:
            # Подписка больше не действительна
            logger.warning(f"Push subscription expired or unsubscribed: {e}")
            return False
        logger.error(f"Failed to send push notification: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending push notification: {e}")
        return False


def send_push_reminder(
    subscription_info: dict,
    reminder_id: str,
    block_text: str,
    message: str,
    block_url: Optional[str] = None,
) -> bool:
    """Отправить напоминание через Push"""
    title = "⏰ Напоминание"
    body = f"{block_text[:100]}"
    if message:
        body += f"\n{message[:100]}"

    return send_push_notification(
        subscription_info=subscription_info,
        title=title,
        body=body,
        url=block_url,
        tag=f"reminder-{reminder_id}",
    )


def send_push_change_notification(
    subscription_info: dict,
    block_text: str,
    change_type: str,
    changed_by: str,
    block_url: Optional[str] = None,
) -> bool:
    """Отправить уведомление об изменении блока через Push"""
    change_labels = {
        'text_change': 'Изменён текст',
        'data_change': 'Изменены данные',
        'move': 'Перемещён',
        'child_add': 'Добавлен дочерний блок',
        'child_delete': 'Удалён дочерний блок',
    }

    change_label = change_labels.get(change_type, change_type)
    title = f"📝 {change_label}"
    body = f"{block_text[:100]}\nАвтор: {changed_by}"

    return send_push_notification(
        subscription_info=subscription_info,
        title=title,
        body=body,
        url=block_url,
        tag=f"change-{change_type}",
    )


def send_push_test_message(subscription_info: dict) -> bool:
    """Отправить тестовое Push уведомление"""
    return send_push_notification(
        subscription_info=subscription_info,
        title="🔔 OmniMap",
        body="Тестовое уведомление. Push-уведомления работают!",
        tag="test",
    )


def generate_vapid_keys() -> tuple[str, str]:
    """
    Генерация VAPID ключей для настройки сервера.
    Запустить один раз и сохранить ключи в settings.

    Usage:
        python -c "from api.services.push import generate_vapid_keys; print(generate_vapid_keys())"
    """
    try:
        from py_vapid import Vapid

        vapid = Vapid()
        vapid.generate_keys()

        public_key = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint
        )
        private_key = vapid.private_pem.decode('utf-8')

        import base64
        public_key_b64 = base64.urlsafe_b64encode(public_key).decode('utf-8').rstrip('=')

        return public_key_b64, private_key
    except ImportError:
        # Альтернативный способ через pywebpush
        from pywebpush import webpush
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization
        import base64

        private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        public_key = private_key.public_key()

        # Экспорт приватного ключа в PEM
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode('utf-8')

        # Экспорт публичного ключа в uncompressed point format
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        )
        public_b64 = base64.urlsafe_b64encode(public_bytes).decode('utf-8').rstrip('=')

        return public_b64, private_pem
