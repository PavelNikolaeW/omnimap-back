from pprint import pprint

import uuid
from django.contrib.auth import get_user_model
from django.db import models
from psqlextra.manager import PostgresManager
from simple_history.models import HistoricalRecords
from api.utils.calc_custom_grid import custom_grid_update
from django.utils.text import slugify

User = get_user_model()

PERMISSION_CHOICES = [
    ('view', 'View'),
    ('edit', 'Edit'),
    ('deny', 'Deny'),
    ('edit_ac', 'Edit access'),
    ('delete', 'Delete block')
]
ALLOWED_SHOW_PERMISSIONS = ['view', 'edit', 'edit_ac', 'delete']
CHANGE_PERMISSION_CHOICES = ['edit_ac', 'delete']


class Block(models.Model):
    """
    Блок (узел дерева), хранящийся в структуре Adjacency List.
    У каждого блока есть uuid в качестве первичного ключа.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children')
    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocks')
    title = models.CharField(max_length=255, blank=True, null=True)
    data = models.JSONField(blank=True, null=True, default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    def __init__(self, *args, **kwargs):
        kwarg = {}
        for field, value in kwargs.items():
            if field in ['id', 'parent', 'creator', 'title', 'data', 'parent_id']:
                kwarg[field] = value
        super().__init__(*args, **kwarg)

    class Meta:
        indexes = [
            models.Index(fields=['parent']),
            models.Index(fields=['id']),
        ]

    def __str__(self):
        return f"{self.title or 'Block'} ({self.id})"

    def add_child(self, child):
        self.children.add(child)
        self.data.setdefault('childOrder', []).append(str(child.id))
        if custom_grid := self.data.get('customGrid'):
            custom_grid_update(custom_grid, str(child.id))
        self.save()

    def add_child_and_set_order(self, child, new_order):
        """
        Добавляет дочерний блок `child` и сразу выставляет порядок `new_order`
        """
        self.children.add(child)

        self.data.setdefault('childOrder', [])
        if str(child.id) not in new_order:
            new_order.append(str(child.id))
        self.data['childOrder'] = new_order
        self.save()

    def add_children(self, children):
        for child in children:
            self.add_child(child)

    def remove_child(self, child):
        if child.id in list(self.children.values_list('id', flat=True)):
            self.children.remove(child)
            self.data['childOrder'].remove(str(child.id))
            if children_positions := self.data.get('customGrid', {}).get('childrenPositions', {}):
                if children_positions.pop(str(child.id), None):
                    self.data['customGrid']['childrenPositions'] = children_positions
            self.save(update_fields=['data'])

    def set_child_order(self, new_order):
        """
        Устанавливает новый порядок дочерних блоков.
        :param new_order: Список ID блоков в новом порядке.
        """
        current_children_ids = [str(uuid) for uuid in list(self.children.values_list('id', flat=True))]
        if set(new_order) != set(current_children_ids):
            raise ValueError("Новый порядок должен содержать все текущие дочерние блоки и только их.")
        self.data['childOrder'] = new_order
        self.save(update_fields=['data'])

    def is_my_child(self, child_id):
        list_child_id = list(self.children.values_list('id', flat=True))
        if isinstance(child_id, str):
            return uuid.UUID(child_id) in list_child_id
        elif isinstance(child_id, uuid.UUID):
            return child_id in list_child_id


class Group(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.CharField(max_length=255)
    users = models.ManyToManyField(User, related_name='custom_groups')
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='owned_groups'
    )

    def __str__(self):
        return f"{self.name} (owner: {self.owner.username})"


# Разрешения для отдельного пользователя
class BlockPermission(models.Model):
    objects = PostgresManager()

    id = models.BigAutoField(primary_key=True)
    block = models.ForeignKey('Block', on_delete=models.CASCADE, related_name='permissions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='block_permissions')
    permission = models.CharField(max_length=10, choices=PERMISSION_CHOICES)

    class Meta:
        unique_together = ('block', 'user',)

    def __str__(self):
        return f"{self.block} | {self.user} => {self.permission}"


class BlockLink(models.Model):
    """
    Модель для хранения ссылок между блоками.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        Block,
        on_delete=models.CASCADE,
        related_name='outgoing_links'
    )
    target = models.ForeignKey(
        Block,
        on_delete=models.CASCADE,
        related_name='incoming_links'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('source', 'target')
        verbose_name = 'Ссылка блока'
        verbose_name_plural = 'Ссылки блоков'

    def __str__(self):
        return f"{self.source} → {self.target}"


class BlockUrlLinkModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(
        Block,
        on_delete=models.CASCADE,
        related_name='url_links',
        verbose_name='Блок',
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='url_links',
        verbose_name='Создатель ссылки',
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True,
        verbose_name='Слаг (ЧПУ)',
        help_text="Уникальная часть URL-адреса для ссылки",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Ссылка на блок'
        verbose_name_plural = 'Ссылки на блоки'
        ordering = ['-created_at']

    def __str__(self):
        return f"Ссылка на блок: {self.source.title} (ID: {self.id})"

    def save(self, *args, **kwargs):
        if not self.slug:
            # Генерация slug, используя название блока + уникальный идентификатор
            self.slug = slugify(f"{self.id}")
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('api:block-url', kwargs={'slug': self.slug})


def block_file_upload_path(instance, filename):
    """Генерирует путь для загрузки файла: blocks/{block_id}/{filename}"""
    return f'blocks/{instance.block_id}/{filename}'


def block_thumbnail_upload_path(instance, filename):
    """Генерирует путь для превью: blocks/{block_id}/thumbs/{filename}"""
    return f'blocks/{instance.block_id}/thumbs/{filename}'


class BlockFile(models.Model):
    """
    Файл (изображение), прикреплённый к блоку.
    Один блок — один файл.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    block = models.OneToOneField(
        'Block',
        on_delete=models.CASCADE,
        related_name='file',
        verbose_name='Блок'
    )
    file = models.ImageField(
        upload_to=block_file_upload_path,
        verbose_name='Файл изображения'
    )
    thumbnail = models.ImageField(
        upload_to=block_thumbnail_upload_path,
        blank=True,
        null=True,
        verbose_name='Превью'
    )
    filename = models.CharField(max_length=255, verbose_name='Имя файла')
    content_type = models.CharField(max_length=100, verbose_name='MIME-тип')
    size = models.PositiveIntegerField(verbose_name='Размер в байтах')
    width = models.PositiveIntegerField(null=True, blank=True, verbose_name='Ширина')
    height = models.PositiveIntegerField(null=True, blank=True, verbose_name='Высота')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_files',
        verbose_name='Загрузил'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Файл блока'
        verbose_name_plural = 'Файлы блоков'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.filename} ({self.block_id})"

    def delete(self, *args, **kwargs):
        # Удаляем файлы при удалении записи
        if self.file:
            self.file.delete(save=False)
        if self.thumbnail:
            self.thumbnail.delete(save=False)
        super().delete(*args, **kwargs)


# ============================================================================
# Модели для напоминаний и уведомлений
# ============================================================================

REPEAT_CHOICES = [
    ('none', 'Однократно'),
    ('daily', 'Ежедневно'),
    ('weekly', 'Еженедельно'),
    ('monthly', 'Ежемесячно'),
]

EMAIL_MODE_CHOICES = [
    ('off', 'Выключено'),
    ('fallback', 'Если Telegram недоступен'),
    ('always', 'Всегда дублировать'),
]

CHANGE_TYPE_CHOICES = [
    ('text_change', 'Изменён текст'),
    ('data_change', 'Изменены свойства'),
    ('move', 'Блок перемещён'),
    ('child_add', 'Добавлен дочерний блок'),
    ('child_delete', 'Удалён дочерний блок'),
]


class BlockReminder(models.Model):
    """Напоминание о блоке (1 блок = 1 напоминание)"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    block = models.OneToOneField(
        Block,
        on_delete=models.CASCADE,
        related_name='reminder'
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reminders')

    remind_at = models.DateTimeField(db_index=True)
    timezone = models.CharField(max_length=50, default='UTC')
    message = models.TextField(blank=True)

    repeat = models.CharField(max_length=20, choices=REPEAT_CHOICES, default='none')

    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['remind_at', 'is_sent']),
            models.Index(fields=['user', 'is_sent']),
        ]
        verbose_name = 'Напоминание'
        verbose_name_plural = 'Напоминания'

    def __str__(self):
        return f"Напоминание для {self.block_id} на {self.remind_at}"


class BlockChangeSubscription(models.Model):
    """Подписка на изменения блока"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    block = models.ForeignKey(Block, on_delete=models.CASCADE, related_name='subscriptions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='block_subscriptions')

    # Глубина отслеживания: 0=только блок, 1,2,3=уровни, -1=все потомки
    depth = models.SmallIntegerField(default=1)

    # Типы отслеживаемых изменений
    on_text_change = models.BooleanField(default=True)
    on_data_change = models.BooleanField(default=True)
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
        verbose_name = 'Подписка на изменения'
        verbose_name_plural = 'Подписки на изменения'

    def __str__(self):
        return f"Подписка {self.user} на {self.block_id}"


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
    email_mode = models.CharField(max_length=20, choices=EMAIL_MODE_CHOICES, default='fallback')

    # Push уведомления в браузере
    push_enabled = models.BooleanField(default=False)
    push_subscription = models.JSONField(null=True, blank=True)

    # Тихие часы
    quiet_hours_enabled = models.BooleanField(default=False)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default='UTC')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Настройки уведомлений'
        verbose_name_plural = 'Настройки уведомлений'

    def __str__(self):
        return f"Настройки уведомлений для {self.user.username}"


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
        verbose_name = 'Токен привязки Telegram'
        verbose_name_plural = 'Токены привязки Telegram'

    def __str__(self):
        return f"Token for {self.user.username} (expires: {self.expires_at})"


class PendingNotification(models.Model):
    """Очередь уведомлений для агрегации"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pending_notifications')
    subscription = models.ForeignKey(
        BlockChangeSubscription,
        on_delete=models.CASCADE,
        related_name='pending_notifications'
    )
    block = models.ForeignKey(Block, on_delete=models.CASCADE, related_name='pending_notifications')

    change_type = models.CharField(max_length=20, choices=CHANGE_TYPE_CHOICES)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='changes_made'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at']),
        ]
        verbose_name = 'Отложенное уведомление'
        verbose_name_plural = 'Отложенные уведомления'

    def __str__(self):
        return f"Pending: {self.change_type} on {self.block_id}"
