from django.urls import path

from .view_delete_tree import delete_tree
from .views import (RegisterView, load_trees, create_block, create_link_on_block,
                    CopyBlockView, edit_block, load_empty_blocks, AccessBlockView, BlockSearchAPIView, move_block,
                    TaskStatusView, create_new_tree, ImportBlocksView, UserListView)
from .views_group import MyGroupsView, GroupCreateView, GroupDeleteView, GroupAddMemberView, GroupRemoveMemberView, \
    GroupMembersView
from .views_history import BlockHistoryListView, BlockHistoryUndoView
from .views_url import create_url, check_slug, get_urls, delete_url, block_url, load_tree, load_nodes, export_blocks
from .views_files import BlockFileView
from .views_notifications import (
    ReminderListCreateView, ReminderDetailView, ReminderSnoozeView, BlockReminderView,
    SubscriptionListCreateView, SubscriptionDetailView, BlockSubscriptionView,
    NotificationSettingsView,
    TelegramStatusView, TelegramLinkView, TelegramUnlinkView, TelegramTestView,
    PushSubscribeView, PushUnsubscribeView, PushTestView,
    InternalTelegramLinkView, InternalTelegramUnlinkView, InternalTelegramStatusView,
    InternalReminderSnoozeView, InternalReminderDeleteView
)

app_name = 'api'

urlpatterns = [
    path('load-trees/', load_trees, name='root-block'),
    path('load-empty/', load_empty_blocks, name='load-empty'),
    path('load-tree/', load_tree, name='load-tree'),
    path('load-nodes/', load_nodes, name='load-nodes'),
    path('import/', ImportBlocksView.as_view(), name='import-json'),
    path('export/', export_blocks, name='export-blocks'),
    path('delete-tree/<uuid:tree_id>/', delete_tree, name='delete-tree'),
    path('new-block/<uuid:parent_id>/', create_block, name='new-block'),
    path('new-tree/', create_new_tree, name='new-tree'),
    path('create-link-block/<uuid:parent_id>/<uuid:source_id>/', create_link_on_block, name='create-link-block'),
    path('move-block/<uuid:old_parent_id>/<uuid:new_parent_id>/<uuid:child_id>/', move_block, name='move-block'),
    path('copy-block/', CopyBlockView.as_view(), name='copy-block'),
    path('edit-block/<uuid:block_id>/', edit_block, name='edit-block'),

    path('create-url/<uuid:block_id>/', create_url, name='create-url'),
    path('check-url/<slug:slug>/', check_slug, name='check-url'),
    path('get-urls/<uuid:block_id>/', get_urls, name='get-urls'),
    path('delete-url/<uuid:block_id>/<slug:slug>/', delete_url, name='delete-url'),
    path('block/<slug:slug>/', block_url, name='block-url'),

    path('access/<uuid:block_id>/', AccessBlockView.as_view(), name='access-list'),
    path('groups/', MyGroupsView.as_view(), name='my_groups'),
    path('groups/<int:group_id>/members/', GroupMembersView.as_view(), name='group_members'),
    path('groups/create/', GroupCreateView.as_view(), name='create_group'),
    path('groups/<int:group_id>/delete/', GroupDeleteView.as_view(), name='delete_group'),
    path('groups/<int:group_id>/add_member/', GroupAddMemberView.as_view(), name='add_member'),
    path('groups/<int:group_id>/remove_member/<str:username>', GroupRemoveMemberView.as_view(), name='remove_member'),

    path('tasks/<str:task_id>/', TaskStatusView.as_view(), name='task_status'),
    path('register/', RegisterView.as_view(), name='register'),
    path('search-block/', BlockSearchAPIView.as_view(), name='search-block'),

    path('blocks/<uuid:block_id>/history/', BlockHistoryListView.as_view(), name='block-history-list'),
    path('blocks/<uuid:block_id>/file/', BlockFileView.as_view(), name='block-file'),
    path('undo/', BlockHistoryUndoView.as_view(), name='block-history-undo'),

    path('users/', UserListView.as_view(), name='users-list'),

    # Напоминания
    path('reminders/', ReminderListCreateView.as_view(), name='reminder-list-create'),
    path('reminders/<uuid:reminder_id>/', ReminderDetailView.as_view(), name='reminder-detail'),
    path('reminders/<uuid:reminder_id>/snooze/', ReminderSnoozeView.as_view(), name='reminder-snooze'),
    path('blocks/<uuid:block_id>/reminder/', BlockReminderView.as_view(), name='block-reminder'),

    # Подписки на изменения
    path('subscriptions/', SubscriptionListCreateView.as_view(), name='subscription-list-create'),
    path('subscriptions/<uuid:subscription_id>/', SubscriptionDetailView.as_view(), name='subscription-detail'),
    path('blocks/<uuid:block_id>/subscription/', BlockSubscriptionView.as_view(), name='block-subscription'),

    # Настройки уведомлений
    path('notifications/settings/', NotificationSettingsView.as_view(), name='notification-settings'),

    # Telegram
    path('notifications/telegram/status/', TelegramStatusView.as_view(), name='telegram-status'),
    path('notifications/telegram/link/', TelegramLinkView.as_view(), name='telegram-link'),
    path('notifications/telegram/unlink/', TelegramUnlinkView.as_view(), name='telegram-unlink'),
    path('notifications/telegram/test/', TelegramTestView.as_view(), name='telegram-test'),

    # Push
    path('notifications/push/subscribe/', PushSubscribeView.as_view(), name='push-subscribe'),
    path('notifications/push/unsubscribe/', PushUnsubscribeView.as_view(), name='push-unsubscribe'),
    path('notifications/push/test/', PushTestView.as_view(), name='push-test'),

    # Internal API для Telegram бота
    path('internal/telegram/link/', InternalTelegramLinkView.as_view(), name='internal-telegram-link'),
    path('internal/telegram/unlink/', InternalTelegramUnlinkView.as_view(), name='internal-telegram-unlink'),
    path('internal/telegram/status/', InternalTelegramStatusView.as_view(), name='internal-telegram-status'),
    path('internal/reminders/<uuid:reminder_id>/snooze/', InternalReminderSnoozeView.as_view(), name='internal-reminder-snooze'),
    path('internal/reminders/<uuid:reminder_id>/', InternalReminderDeleteView.as_view(), name='internal-reminder-delete'),
]
