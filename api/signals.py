from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from block_api.settings import MAX_HISTORY
from .models import Block
from django.db.models.signals import post_save


@receiver(m2m_changed, sender=Block.children.through)
def update_data_on_children_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action == 'post_add':
        for child_id in pk_set:
            instance.update_child_order_on_add(str(child_id))
    elif action == 'post_remove':
        for child_id in pk_set:
            instance.update_child_order_on_remove(str(child_id))
    elif action == 'post_clear':
        instance.data['childOrder'] = []
        if instance.data.get('customGrid'):
            instance.data.pop('customGrid')
        instance.save(update_fields=['data'])


@receiver(post_save, sender=Block)
def limit_history_records(sender, instance, **kwargs):
    history = instance.history.all().order_by('-history_date')

    if history.count() > MAX_HISTORY:
        old_history = history[MAX_HISTORY:]

        # Удаляем каждую запись по отдельности
        for record in old_history:
            record.delete()
