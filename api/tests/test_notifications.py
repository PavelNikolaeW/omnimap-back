"""
Тесты для API напоминаний и уведомлений.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.models import (
    Block, BlockPermission, BlockReminder, BlockChangeSubscription,
    UserNotificationSettings, TelegramLinkToken
)

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='testpass123'
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        username='otheruser',
        email='other@example.com',
        password='testpass123'
    )


@pytest.fixture
def block(db, user):
    block = Block.objects.create(
        creator=user,
        title='Test Block',
        data={'text': 'Test content', 'childOrder': []}
    )
    BlockPermission.objects.create(
        block=block,
        user=user,
        permission='delete'
    )
    return block


@pytest.fixture
def authenticated_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


class TestReminderAPI:
    """Тесты для API напоминаний."""

    def test_create_reminder(self, authenticated_client, block, user):
        """Тест создания напоминания."""
        remind_at = timezone.now() + timedelta(hours=1)
        data = {
            'block_id': str(block.id),
            'remind_at': remind_at.isoformat(),
            'message': 'Test reminder',
            'repeat': 'none'
        }

        response = authenticated_client.post(
            reverse('api:reminder-list-create'),
            data,
            format='json'
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert BlockReminder.objects.filter(block=block, user=user).exists()

    def test_create_reminder_duplicate_block(self, authenticated_client, block, user):
        """Тест: нельзя создать два напоминания для одного блока."""
        remind_at = timezone.now() + timedelta(hours=1)
        BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at
        )

        data = {
            'block_id': str(block.id),
            'remind_at': remind_at.isoformat(),
        }

        response = authenticated_client.post(
            reverse('api:reminder-list-create'),
            data,
            format='json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_reminders(self, authenticated_client, block, user):
        """Тест получения списка напоминаний."""
        remind_at = timezone.now() + timedelta(hours=1)
        BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at
        )

        response = authenticated_client.get(reverse('api:reminder-list-create'))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_list_reminders_filter_pending(self, authenticated_client, block, user):
        """Тест фильтрации напоминаний по статусу."""
        remind_at = timezone.now() + timedelta(hours=1)
        BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at,
            is_sent=False
        )

        response = authenticated_client.get(
            reverse('api:reminder-list-create') + '?status=pending'
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_get_reminder(self, authenticated_client, block, user):
        """Тест получения напоминания по ID."""
        remind_at = timezone.now() + timedelta(hours=1)
        reminder = BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at
        )

        response = authenticated_client.get(
            reverse('api:reminder-detail', kwargs={'reminder_id': reminder.id})
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == str(reminder.id)

    def test_update_reminder(self, authenticated_client, block, user):
        """Тест обновления напоминания."""
        remind_at = timezone.now() + timedelta(hours=1)
        reminder = BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at
        )

        new_remind_at = remind_at + timedelta(hours=2)
        response = authenticated_client.patch(
            reverse('api:reminder-detail', kwargs={'reminder_id': reminder.id}),
            {'remind_at': new_remind_at.isoformat()},
            format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        reminder.refresh_from_db()
        assert reminder.remind_at.replace(microsecond=0) == new_remind_at.replace(microsecond=0)

    def test_delete_reminder(self, authenticated_client, block, user):
        """Тест удаления напоминания."""
        remind_at = timezone.now() + timedelta(hours=1)
        reminder = BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at
        )

        response = authenticated_client.delete(
            reverse('api:reminder-detail', kwargs={'reminder_id': reminder.id})
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not BlockReminder.objects.filter(id=reminder.id).exists()

    def test_snooze_reminder(self, authenticated_client, block, user):
        """Тест откладывания напоминания."""
        remind_at = timezone.now() - timedelta(minutes=5)
        reminder = BlockReminder.objects.create(
            block=block,
            user=user,
            remind_at=remind_at,
            is_sent=True
        )

        response = authenticated_client.post(
            reverse('api:reminder-snooze', kwargs={'reminder_id': reminder.id}),
            {'minutes': 30},
            format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        reminder.refresh_from_db()
        assert reminder.snoozed_until is not None
        assert reminder.is_sent is False


class TestSubscriptionAPI:
    """Тесты для API подписок."""

    def test_create_subscription(self, authenticated_client, block, user):
        """Тест создания подписки."""
        data = {
            'block_id': str(block.id),
            'depth': 2,
            'on_text_change': True,
            'on_data_change': False
        }

        response = authenticated_client.post(
            reverse('api:subscription-list-create'),
            data,
            format='json'
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert BlockChangeSubscription.objects.filter(block=block, user=user).exists()

    def test_create_subscription_duplicate(self, authenticated_client, block, user):
        """Тест: нельзя создать две подписки на один блок."""
        BlockChangeSubscription.objects.create(
            block=block,
            user=user,
            depth=1
        )

        data = {
            'block_id': str(block.id),
            'depth': 2
        }

        response = authenticated_client.post(
            reverse('api:subscription-list-create'),
            data,
            format='json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_subscriptions(self, authenticated_client, block, user):
        """Тест получения списка подписок."""
        BlockChangeSubscription.objects.create(
            block=block,
            user=user,
            depth=1
        )

        response = authenticated_client.get(reverse('api:subscription-list-create'))

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_update_subscription(self, authenticated_client, block, user):
        """Тест обновления подписки."""
        subscription = BlockChangeSubscription.objects.create(
            block=block,
            user=user,
            depth=1,
            on_text_change=True
        )

        response = authenticated_client.patch(
            reverse('api:subscription-detail', kwargs={'subscription_id': subscription.id}),
            {'depth': 3, 'on_text_change': False},
            format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        subscription.refresh_from_db()
        assert subscription.depth == 3
        assert subscription.on_text_change is False

    def test_delete_subscription(self, authenticated_client, block, user):
        """Тест удаления подписки."""
        subscription = BlockChangeSubscription.objects.create(
            block=block,
            user=user,
            depth=1
        )

        response = authenticated_client.delete(
            reverse('api:subscription-detail', kwargs={'subscription_id': subscription.id})
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not BlockChangeSubscription.objects.filter(id=subscription.id).exists()


class TestNotificationSettingsAPI:
    """Тесты для API настроек уведомлений."""

    def test_get_settings_creates_if_not_exists(self, authenticated_client, user):
        """Тест: настройки создаются автоматически при первом запросе."""
        assert not UserNotificationSettings.objects.filter(user=user).exists()

        response = authenticated_client.get(reverse('api:notification-settings'))

        assert response.status_code == status.HTTP_200_OK
        assert UserNotificationSettings.objects.filter(user=user).exists()

    def test_update_settings(self, authenticated_client, user):
        """Тест обновления настроек."""
        UserNotificationSettings.objects.create(user=user)

        response = authenticated_client.patch(
            reverse('api:notification-settings'),
            {
                'email_enabled': False,
                'quiet_hours_enabled': True,
                'quiet_hours_start': '23:00:00',
                'quiet_hours_end': '08:00:00'
            },
            format='json'
        )

        assert response.status_code == status.HTTP_200_OK
        settings = UserNotificationSettings.objects.get(user=user)
        assert settings.email_enabled is False
        assert settings.quiet_hours_enabled is True


class TestTelegramAPI:
    """Тесты для Telegram API."""

    def test_telegram_status_not_linked(self, authenticated_client, user):
        """Тест статуса Telegram (не привязан)."""
        response = authenticated_client.get(reverse('api:telegram-status'))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['linked'] is False

    def test_telegram_status_linked(self, authenticated_client, user):
        """Тест статуса Telegram (привязан)."""
        UserNotificationSettings.objects.create(
            user=user,
            telegram_chat_id='123456789',
            telegram_username='testuser',
            telegram_enabled=True,
            telegram_linked_at=timezone.now()
        )

        response = authenticated_client.get(reverse('api:telegram-status'))

        assert response.status_code == status.HTTP_200_OK
        assert response.data['linked'] is True
        assert response.data['username'] == 'testuser'

    def test_telegram_link_generates_token(self, authenticated_client, user):
        """Тест генерации ссылки для привязки Telegram."""
        response = authenticated_client.post(reverse('api:telegram-link'))

        assert response.status_code == status.HTTP_200_OK
        assert 'link' in response.data
        assert 'token' in response.data
        assert TelegramLinkToken.objects.filter(user=user).exists()

    def test_telegram_unlink(self, authenticated_client, user):
        """Тест отвязки Telegram."""
        UserNotificationSettings.objects.create(
            user=user,
            telegram_chat_id='123456789',
            telegram_enabled=True
        )

        response = authenticated_client.post(reverse('api:telegram-unlink'))

        assert response.status_code == status.HTTP_200_OK
        settings = UserNotificationSettings.objects.get(user=user)
        assert settings.telegram_chat_id is None
        assert settings.telegram_enabled is False


class TestInternalTelegramAPI:
    """Тесты для внутреннего API Telegram бота."""

    @pytest.fixture
    def bot_client(self, api_client):
        """Клиент с заголовком X-Bot-Secret."""
        api_client.credentials(HTTP_X_BOT_SECRET='test_secret')
        return api_client

    @patch('django.conf.settings.TELEGRAM_BOT_SECRET', 'test_secret')
    def test_internal_link(self, bot_client, user):
        """Тест внутреннего API привязки."""
        token = TelegramLinkToken.objects.create(
            user=user,
            token='test_token_123_abcdefghijklmnopqrstuvwxyz_1234',
            expires_at=timezone.now() + timedelta(minutes=15)
        )

        with patch('django.conf.settings.TELEGRAM_BOT_SECRET', 'test_secret'):
            response = bot_client.post(
                reverse('api:internal-telegram-link'),
                {
                    'token': 'test_token_123_abcdefghijklmnopqrstuvwxyz_1234',
                    'chat_id': '987654321',
                    'username': 'telegram_user'
                },
                format='json'
            )

        assert response.status_code == status.HTTP_200_OK
        settings = UserNotificationSettings.objects.get(user=user)
        assert settings.telegram_chat_id == '987654321'

    def test_internal_link_unauthorized(self, api_client, user):
        """Тест: внутренний API требует X-Bot-Secret."""
        response = api_client.post(
            reverse('api:internal-telegram-link'),
            {'token': 'test', 'chat_id': '123'},
            format='json'
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
