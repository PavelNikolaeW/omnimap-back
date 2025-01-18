from pprint import pprint

import uuid
from django.contrib.auth import get_user_model
from django.db import models
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


class BlockPermission(models.Model):
    id = models.BigAutoField(primary_key=True)
    block = models.ForeignKey(Block, on_delete=models.CASCADE, related_name='permissions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='block_permissions')
    permission = models.CharField(max_length=10, choices=PERMISSION_CHOICES)

    class Meta:
        # Запрещаем дублировать одну и ту же запись вида:
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
            self.slug = slugify(f"{self.source.title}-{self.id}")
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('api:block-url', kwargs={'slug': self.slug})
