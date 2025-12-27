"""
Views для напоминаний, подписок и настроек уведомлений.
"""
import secrets
from datetime import timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Block, BlockReminder, BlockChangeSubscription,
    UserNotificationSettings, TelegramLinkToken, BlockPermission,
    ALLOWED_SHOW_PERMISSIONS
)
from .serializers import (
    BlockReminderSerializer, BlockReminderUpdateSerializer, ReminderSnoozeSerializer,
    BlockChangeSubscriptionSerializer, BlockChangeSubscriptionUpdateSerializer,
    UserNotificationSettingsSerializer, TelegramLinkResponseSerializer,
    TelegramStatusSerializer, PushSubscriptionSerializer
)
from .services.telegram import send_telegram_test_message
from .tasks import send_notification_event


def send_ws_event(event_type: str, user_id: int, data: dict):
    """Отправляет WebSocket событие через RabbitMQ."""
    send_notification_event.delay({
        'type': event_type,
        'user_id': user_id,
        'data': data
    })


def can_access_block(user, block) -> bool:
    """Проверяет, может ли пользователь получить доступ к блоку."""
    return BlockPermission.objects.filter(
        block=block,
        user=user,
        permission__in=ALLOWED_SHOW_PERMISSIONS
    ).exists()


# ============================================================================
# API для напоминаний
# ============================================================================

class ReminderListCreateView(APIView):
    """
    GET  /api/v1/reminders/      - Список своих напоминаний
    POST /api/v1/reminders/      - Создать напоминание
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status_filter = request.query_params.get('status')
        queryset = BlockReminder.objects.filter(user=request.user).select_related('block')

        if status_filter == 'pending':
            queryset = queryset.filter(is_sent=False)
        elif status_filter == 'sent':
            queryset = queryset.filter(is_sent=True)

        queryset = queryset.order_by('remind_at')
        serializer = BlockReminderSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = BlockReminderSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            reminder = serializer.save()

            # WebSocket событие
            send_ws_event('reminder_created', request.user.id, {
                'id': str(reminder.id),
                'block_id': str(reminder.block_id),
                'remind_at': reminder.remind_at.isoformat(),
                'timezone': reminder.timezone,
                'message': reminder.message,
                'repeat': reminder.repeat
            })

            # Отправляем подтверждение в Telegram, если подключён
            try:
                notif_settings = request.user.notification_settings
                if notif_settings.telegram_enabled and notif_settings.telegram_chat_id:
                    from .services.telegram import send_telegram_reminder_created
                    block_text = reminder.block.data.get('text', reminder.block.title or '')
                    block_url = f"{settings.FRONTEND_HOST}/block/{reminder.block.id}"
                    send_telegram_reminder_created(
                        chat_id=notif_settings.telegram_chat_id,
                        reminder_id=str(reminder.id),
                        block_text=block_text,
                        remind_at=reminder.remind_at,
                        repeat=reminder.repeat,
                        block_url=block_url
                    )
            except UserNotificationSettings.DoesNotExist:
                pass

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ReminderDetailView(APIView):
    """
    GET    /api/v1/reminders/{id}/   - Получить напоминание
    PATCH  /api/v1/reminders/{id}/   - Обновить напоминание
    DELETE /api/v1/reminders/{id}/   - Удалить напоминание
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, request, reminder_id):
        return get_object_or_404(BlockReminder, id=reminder_id, user=request.user)

    def get(self, request, reminder_id):
        reminder = self.get_object(request, reminder_id)
        serializer = BlockReminderSerializer(reminder)
        return Response(serializer.data)

    def patch(self, request, reminder_id):
        reminder = self.get_object(request, reminder_id)
        serializer = BlockReminderUpdateSerializer(reminder, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            reminder.refresh_from_db()

            # WebSocket событие
            send_ws_event('reminder_updated', request.user.id, {
                'id': str(reminder.id),
                'block_id': str(reminder.block_id),
                'remind_at': reminder.remind_at.isoformat(),
                'timezone': reminder.timezone,
                'message': reminder.message,
                'repeat': reminder.repeat
            })

            return Response(BlockReminderSerializer(reminder).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, reminder_id):
        reminder = self.get_object(request, reminder_id)
        block_id = str(reminder.block_id)
        reminder_id_str = str(reminder.id)
        reminder.delete()

        # WebSocket событие
        send_ws_event('reminder_deleted', request.user.id, {
            'id': reminder_id_str,
            'block_id': block_id
        })

        return Response(status=status.HTTP_204_NO_CONTENT)


class ReminderSnoozeView(APIView):
    """
    POST /api/v1/reminders/{id}/snooze/   - Отложить напоминание
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, reminder_id):
        reminder = get_object_or_404(BlockReminder, id=reminder_id, user=request.user)
        serializer = ReminderSnoozeSerializer(data=request.data)

        if serializer.is_valid():
            minutes = serializer.validated_data['minutes']
            reminder.snoozed_until = timezone.now() + timedelta(minutes=minutes)
            reminder.is_sent = False
            reminder.save()

            # WebSocket событие
            send_ws_event('reminder_snoozed', request.user.id, {
                'id': str(reminder.id),
                'block_id': str(reminder.block_id),
                'snoozed_until': reminder.snoozed_until.isoformat()
            })

            return Response(BlockReminderSerializer(reminder).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BlockReminderView(APIView):
    """
    GET /api/v1/blocks/{block_id}/reminder/   - Получить напоминание блока
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, block_id):
        block = get_object_or_404(Block, id=block_id)
        if not can_access_block(request.user, block):
            return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        try:
            reminder = BlockReminder.objects.get(block=block)
            serializer = BlockReminderSerializer(reminder)
            return Response(serializer.data)
        except BlockReminder.DoesNotExist:
            return Response({'error': 'Reminder not found'}, status=status.HTTP_404_NOT_FOUND)


# ============================================================================
# API для подписок
# ============================================================================

class SubscriptionListCreateView(APIView):
    """
    GET  /api/v1/subscriptions/      - Список своих подписок
    POST /api/v1/subscriptions/      - Создать подписку
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = BlockChangeSubscription.objects.filter(
            user=request.user
        ).select_related('block').order_by('-created_at')
        serializer = BlockChangeSubscriptionSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        # Проверяем права доступа к блоку
        block_id = request.data.get('block_id')
        if block_id:
            try:
                block = Block.objects.get(id=block_id)
                if not can_access_block(request.user, block):
                    return Response(
                        {'error': 'Access denied to this block'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Block.DoesNotExist:
                return Response(
                    {'error': 'Block not found'},
                    status=status.HTTP_404_NOT_FOUND
                )

        serializer = BlockChangeSubscriptionSerializer(
            data=request.data, context={'request': request}
        )
        if serializer.is_valid():
            subscription = serializer.save()

            # WebSocket событие
            send_ws_event('subscription_created', request.user.id, {
                'id': str(subscription.id),
                'block_id': str(subscription.block_id),
                'depth': subscription.depth,
                'on_text_change': subscription.on_text_change,
                'on_data_change': subscription.on_data_change,
                'on_move': subscription.on_move,
                'on_child_add': subscription.on_child_add,
                'on_child_delete': subscription.on_child_delete
            })

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SubscriptionDetailView(APIView):
    """
    GET    /api/v1/subscriptions/{id}/   - Получить подписку
    PATCH  /api/v1/subscriptions/{id}/   - Обновить подписку
    DELETE /api/v1/subscriptions/{id}/   - Удалить подписку
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, request, subscription_id):
        return get_object_or_404(
            BlockChangeSubscription, id=subscription_id, user=request.user
        )

    def get(self, request, subscription_id):
        subscription = self.get_object(request, subscription_id)
        serializer = BlockChangeSubscriptionSerializer(subscription)
        return Response(serializer.data)

    def patch(self, request, subscription_id):
        subscription = self.get_object(request, subscription_id)
        serializer = BlockChangeSubscriptionUpdateSerializer(
            subscription, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            subscription.refresh_from_db()

            # WebSocket событие
            send_ws_event('subscription_updated', request.user.id, {
                'id': str(subscription.id),
                'block_id': str(subscription.block_id),
                'depth': subscription.depth,
                'on_text_change': subscription.on_text_change,
                'on_data_change': subscription.on_data_change,
                'on_move': subscription.on_move,
                'on_child_add': subscription.on_child_add,
                'on_child_delete': subscription.on_child_delete
            })

            return Response(BlockChangeSubscriptionSerializer(subscription).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, subscription_id):
        subscription = self.get_object(request, subscription_id)
        block_id = str(subscription.block_id)
        subscription_id_str = str(subscription.id)
        subscription.delete()

        # WebSocket событие
        send_ws_event('subscription_deleted', request.user.id, {
            'id': subscription_id_str,
            'block_id': block_id
        })

        return Response(status=status.HTTP_204_NO_CONTENT)


class BlockSubscriptionView(APIView):
    """
    GET /api/v1/blocks/{block_id}/subscription/   - Получить подписку на блок
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, block_id):
        block = get_object_or_404(Block, id=block_id)
        if not can_access_block(request.user, block):
            return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

        try:
            subscription = BlockChangeSubscription.objects.get(
                block=block, user=request.user
            )
            serializer = BlockChangeSubscriptionSerializer(subscription)
            return Response(serializer.data)
        except BlockChangeSubscription.DoesNotExist:
            return Response(
                {'error': 'Subscription not found'},
                status=status.HTTP_404_NOT_FOUND
            )


# ============================================================================
# API для настроек уведомлений
# ============================================================================

class NotificationSettingsView(APIView):
    """
    GET   /api/v1/notifications/settings/   - Получить настройки
    PATCH /api/v1/notifications/settings/   - Обновить настройки
    """
    permission_classes = [IsAuthenticated]

    def get_or_create_settings(self, user):
        settings_obj, _ = UserNotificationSettings.objects.get_or_create(user=user)
        return settings_obj

    def get(self, request):
        settings_obj = self.get_or_create_settings(request.user)
        serializer = UserNotificationSettingsSerializer(settings_obj)
        return Response(serializer.data)

    def patch(self, request):
        settings_obj = self.get_or_create_settings(request.user)
        serializer = UserNotificationSettingsSerializer(
            settings_obj, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ============================================================================
# Telegram API
# ============================================================================

class TelegramStatusView(APIView):
    """
    GET /api/v1/notifications/telegram/status/   - Статус привязки Telegram
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            settings_obj = request.user.notification_settings
            data = {
                'linked': bool(settings_obj.telegram_chat_id),
                'username': settings_obj.telegram_username,
                'linked_at': settings_obj.telegram_linked_at
            }
        except UserNotificationSettings.DoesNotExist:
            data = {'linked': False, 'username': None, 'linked_at': None}

        return Response(data)


class TelegramLinkView(APIView):
    """
    POST /api/v1/notifications/telegram/link/   - Получить ссылку для привязки
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Генерируем токен
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + timedelta(
            minutes=settings.TELEGRAM_LINK_TOKEN_EXPIRY_MINUTES
        )

        # Удаляем старые неиспользованные токены
        TelegramLinkToken.objects.filter(user=request.user, used=False).delete()

        # Создаём новый токен
        TelegramLinkToken.objects.create(
            token=token,
            user=request.user,
            expires_at=expires_at
        )

        # Формируем ссылку на бота
        bot_username = settings.TELEGRAM_BOT_USERNAME
        link = f"https://t.me/{bot_username}?start={token}"

        return Response({
            'link': link,
            'token': token,
            'expires_at': expires_at
        })


class TelegramUnlinkView(APIView):
    """
    POST /api/v1/notifications/telegram/unlink/   - Отвязать Telegram
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            settings_obj = request.user.notification_settings
            settings_obj.telegram_chat_id = None
            settings_obj.telegram_username = None
            settings_obj.telegram_enabled = False
            settings_obj.telegram_linked_at = None
            settings_obj.save()
            return Response({'status': 'unlinked'})
        except UserNotificationSettings.DoesNotExist:
            return Response({'status': 'not_linked'})


class TelegramTestView(APIView):
    """
    POST /api/v1/notifications/telegram/test/   - Тестовое сообщение
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            settings_obj = request.user.notification_settings
            if not settings_obj.telegram_chat_id:
                return Response(
                    {'error': 'Telegram not linked'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            success = send_telegram_test_message(settings_obj.telegram_chat_id)
            if success:
                return Response({'status': 'sent'})
            return Response(
                {'error': 'Failed to send message'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except UserNotificationSettings.DoesNotExist:
            return Response(
                {'error': 'Telegram not linked'},
                status=status.HTTP_400_BAD_REQUEST
            )


# ============================================================================
# Push API
# ============================================================================

class PushSubscribeView(APIView):
    """
    POST /api/v1/notifications/push/subscribe/   - Подписаться на push
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PushSubscriptionSerializer(data=request.data)
        if serializer.is_valid():
            settings_obj, _ = UserNotificationSettings.objects.get_or_create(
                user=request.user
            )
            settings_obj.push_subscription = serializer.validated_data
            settings_obj.push_enabled = True
            settings_obj.save()
            return Response({'status': 'subscribed'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PushUnsubscribeView(APIView):
    """
    POST /api/v1/notifications/push/unsubscribe/   - Отписаться от push
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            settings_obj = request.user.notification_settings
            settings_obj.push_subscription = None
            settings_obj.push_enabled = False
            settings_obj.save()
            return Response({'status': 'unsubscribed'})
        except UserNotificationSettings.DoesNotExist:
            return Response({'status': 'not_subscribed'})


class PushTestView(APIView):
    """
    POST /api/v1/notifications/push/test/   - Тестовое push-уведомление
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            settings_obj = request.user.notification_settings
            if not settings_obj.push_subscription:
                return Response(
                    {'error': 'Push not configured'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            from api.services.push import send_push_test_message
            success = send_push_test_message(settings_obj.push_subscription)

            if success:
                return Response({'status': 'sent'})
            else:
                return Response(
                    {'error': 'Failed to send push notification'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
        except UserNotificationSettings.DoesNotExist:
            return Response(
                {'error': 'Push not configured'},
                status=status.HTTP_400_BAD_REQUEST
            )


# ============================================================================
# Internal API для Telegram бота (с X-Bot-Secret)
# ============================================================================

class InternalTelegramLinkView(APIView):
    """
    POST /api/v1/internal/telegram/link/   - Привязка chat_id по токену
    """
    permission_classes = []  # Проверяем секрет вручную

    def post(self, request):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != settings.TELEGRAM_BOT_SECRET:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token = request.data.get('token')
        chat_id = request.data.get('chat_id')
        username = request.data.get('username', '')

        if not token or not chat_id:
            return Response(
                {'error': 'token and chat_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            link_token = TelegramLinkToken.objects.get(
                token=token,
                used=False,
                expires_at__gt=timezone.now()
            )
        except TelegramLinkToken.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired token'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Обновляем настройки пользователя
        settings_obj, _ = UserNotificationSettings.objects.get_or_create(
            user=link_token.user
        )
        settings_obj.telegram_chat_id = chat_id
        settings_obj.telegram_username = username
        settings_obj.telegram_enabled = True
        settings_obj.telegram_linked_at = timezone.now()
        settings_obj.save()

        # Помечаем токен как использованный
        link_token.used = True
        link_token.save()

        return Response({
            'status': 'linked',
            'user_id': link_token.user.id,
            'username': link_token.user.username
        })


class InternalTelegramUnlinkView(APIView):
    """
    POST /api/v1/internal/telegram/unlink/   - Отвязка по chat_id
    """
    permission_classes = []

    def post(self, request):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != settings.TELEGRAM_BOT_SECRET:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        chat_id = request.data.get('chat_id')
        if not chat_id:
            return Response(
                {'error': 'chat_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            settings_obj = UserNotificationSettings.objects.get(telegram_chat_id=chat_id)
            settings_obj.telegram_chat_id = None
            settings_obj.telegram_username = None
            settings_obj.telegram_enabled = False
            settings_obj.telegram_linked_at = None
            settings_obj.save()
            return Response({'status': 'unlinked'})
        except UserNotificationSettings.DoesNotExist:
            return Response({'status': 'not_found'})


class InternalTelegramStatusView(APIView):
    """
    GET /api/v1/internal/telegram/status/   - Проверка привязки по chat_id
    """
    permission_classes = []

    def get(self, request):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != settings.TELEGRAM_BOT_SECRET:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        chat_id = request.query_params.get('chat_id')
        if not chat_id:
            return Response(
                {'error': 'chat_id required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            settings_obj = UserNotificationSettings.objects.get(telegram_chat_id=chat_id)
            return Response({
                'linked': True,
                'user_id': settings_obj.user_id,
                'username': settings_obj.user.username
            })
        except UserNotificationSettings.DoesNotExist:
            return Response({'linked': False})


class InternalReminderSnoozeView(APIView):
    """
    POST /api/v1/internal/reminders/{id}/snooze/   - Отложить напоминание (для бота)
    """
    permission_classes = []

    def post(self, request, reminder_id):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != settings.TELEGRAM_BOT_SECRET:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        minutes = request.data.get('minutes', 5)
        try:
            reminder = BlockReminder.objects.get(id=reminder_id)
            reminder.snoozed_until = timezone.now() + timedelta(minutes=int(minutes))
            reminder.is_sent = False
            reminder.save()
            return Response({'status': 'snoozed', 'until': reminder.snoozed_until})
        except BlockReminder.DoesNotExist:
            return Response(
                {'error': 'Reminder not found'},
                status=status.HTTP_404_NOT_FOUND
            )


class InternalReminderDeleteView(APIView):
    """
    DELETE /api/v1/internal/reminders/{id}/   - Отменить напоминание (для бота)
    """
    permission_classes = []

    def delete(self, request, reminder_id):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != settings.TELEGRAM_BOT_SECRET:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        try:
            reminder = BlockReminder.objects.get(id=reminder_id)
            reminder.delete()
            return Response({'status': 'deleted'})
        except BlockReminder.DoesNotExist:
            return Response(
                {'error': 'Reminder not found'},
                status=status.HTTP_404_NOT_FOUND
            )
