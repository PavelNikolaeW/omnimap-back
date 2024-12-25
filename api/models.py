from pprint import pprint

import uuid6
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from simple_history.models import HistoricalRecords
from api.utils.calc_custom_grid import custom_grid_update

ACCESS_TYPE_CHOICES = (('private', 'Private'),
                       ('public', 'Public'),
                       ('inherited', 'Inherited'),
                       ('public_ed', 'Public Editable'))

User = get_user_model()
class UUIDModel(models.Model):
    id = models.CharField(
        primary_key=True,
        default=uuid6.uuid6,
        editable=False,
        max_length=36,
    )

    class Meta:
        abstract = True


class Block(UUIDModel):
    creator = models.ForeignKey('auth.User', related_name='blocks', on_delete=models.CASCADE)

    access_type = models.CharField(max_length=10, choices=ACCESS_TYPE_CHOICES, default='inherited')
    visible_to_users = models.ManyToManyField('auth.User', related_name='visible_blocks', blank=True)
    editable_by_users = models.ManyToManyField('auth.User', related_name='editable_blocks', blank=True)
    children = models.ManyToManyField('self', symmetrical=False, related_name='parent_blocks', blank=True)

    data = models.JSONField(blank=True, null=True, default=dict)
    title = models.CharField(max_length=255, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    def __str__(self):
        return self.title

    def update_child_order_on_add(self, child_id):
        child_order = self.data.get('childOrder', [])
        if child_id not in child_order:
            child_order.append(child_id)
            self.data['childOrder'] = child_order
        if custom_grid := self.data.get('customGrid'):
            custom_grid_update(custom_grid, child_id)
        self.save(update_fields=['data'])

    def update_child_order_on_remove(self, child_id):
        child_order = self.data.get('childOrder', [])
        if child_id in child_order:
            child_order.remove(child_id)
            self.data['childOrder'] = child_order
        if custom_grid := self.data.get('customGrid', {}).get('childrenPositions'):
            pprint(custom_grid)
            custom_grid.pop(child_id, None)
        self.save(update_fields=['data'])

    def set_child_order(self, new_order):
        """
        Устанавливает новый порядок дочерних блоков.
        :param new_order: Список ID блоков в новом порядке.
        """
        current_children_ids = list(self.children.values_list('id', flat=True))
        if set(new_order) != set(current_children_ids):
            raise ValueError("Новый порядок должен содержать все текущие дочерние блоки и только их.")
        self.data['childOrder'] = new_order
        self.save(update_fields=['data'])
        self.call_custom_grid_update()

    def call_custom_grid_update(self):
        """
        Вызывает функцию custom_grid_update, если в data присутствует customGrid.
        Обновляет поле customGrid в data после вызова функции.
        """
        if self.data.get('customGrid'):
            custom_grid = self.data['customGrid']
            children = list(self.children.values_list('id', flat=True))
            for child in children:
                custom_grid_update(custom_grid, child)
            self.save(update_fields=['data'])

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new:
            # Инициализация childOrder при создании нового блока
            self.data['childOrder'] = []

        # Сохраняем блок, чтобы получить ID
        super().save(*args, **kwargs)

        if is_new:
            # Устанавливаем видимость и редактируемость для создателя
            self.visible_to_users.add(self.creator)
            self.editable_by_users.add(self.creator)

        # Если title не передан или пустой, устанавливаем его в значение ID блока
        if not self.title:
            self.title = str(self.id)

            # Обновляем поле data с флагом title_visible
            self.data['titleIsVisible'] = False
            # Снова сохраняем блок, так как могли изменить title и data
            super().save()


class CustomJSONEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid6.UUID):
            return str(obj)
        return super().default(obj)