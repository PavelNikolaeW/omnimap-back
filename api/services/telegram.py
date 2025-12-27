"""
Telegram сервис для отправки уведомлений.
"""
import logging
from datetime import datetime
from typing import Optional

import httpx
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TELEGRAM_API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


def send_telegram_message(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Базовая функция отправки сообщения в Telegram."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not configured")
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
            if response.status_code != 200:
                logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")
        return False


def send_telegram_reminder(
    chat_id: str,
    reminder_id: str,
    block_text: str,
    message: str,
    block_url: str
) -> bool:
    """Отправляет напоминание в Telegram."""
    text = f"⏰ <b>Напоминание</b>\n\n{block_text[:200]}"
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

    return send_telegram_message(chat_id, text, keyboard)


def send_telegram_change_notification(
    chat_id: str,
    block_text: str,
    change_type: str,
    changed_by: str,
    block_url: str
) -> bool:
    """Уведомление об изменении блока."""
    change_labels = {
        'text_change': 'Изменён текст',
        'data_change': 'Изменены свойства',
        'move': 'Блок перемещён',
        'child_add': 'Добавлен дочерний блок',
        'child_delete': 'Удалён дочерний блок',
    }

    text = f"📝 <b>{change_labels.get(change_type, 'Изменение')}</b>\n\n"
    text += f"«{block_text[:200]}»\n\n"
    text += f"👤 {changed_by}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Открыть блок", "url": block_url}]
        ]
    }

    return send_telegram_message(chat_id, text, keyboard)


def send_telegram_reminder_created(
    chat_id: str,
    reminder_id: str,
    block_text: str,
    remind_at: datetime,
    repeat: str,
    block_url: str
) -> bool:
    """Подтверждение создания напоминания."""
    now = timezone.now()
    delta = remind_at - now
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60

    time_str = remind_at.strftime("%d.%m.%Y в %H:%M")

    if hours > 0:
        delta_str = f"{hours}ч {minutes}мин"
    else:
        delta_str = f"{minutes} мин"

    text = f"🔔 <b>Напоминание создано</b>\n\n"
    text += f"📝 {block_text[:200]}\n\n"
    text += f"⏰ Сработает: {time_str}\n"
    text += f"   (через {delta_str})"

    if repeat != 'none':
        repeat_labels = {
            'daily': 'ежедневно',
            'weekly': 'еженедельно',
            'monthly': 'ежемесячно'
        }
        text += f"\n🔄 Повтор: {repeat_labels.get(repeat, repeat)}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Отменить", "callback_data": f"cancel:{reminder_id}"}]
        ]
    }

    return send_telegram_message(chat_id, text, keyboard)


def send_telegram_aggregated_notification(
    chat_id: str,
    block_text: str,
    changes_count: int,
    block_url: str
) -> bool:
    """Агрегированное уведомление о множественных изменениях."""
    text = f"📋 <b>Изменения в блоке</b>\n\n"
    text += f"«{block_text[:200]}»\n\n"
    text += f"📊 Изменений за последнее время: {changes_count}"

    keyboard = {
        "inline_keyboard": [
            [{"text": "Открыть блок", "url": block_url}]
        ]
    }

    return send_telegram_message(chat_id, text, keyboard)


def send_telegram_test_message(chat_id: str) -> bool:
    """Отправляет тестовое сообщение."""
    text = "✅ <b>Тестовое уведомление</b>\n\nTelegram успешно подключён к OmniMap!"
    return send_telegram_message(chat_id, text)
