from functools import wraps

from django.db.models.signals import m2m_changed, post_delete, pre_save, pre_delete
from django.dispatch import receiver

from block_api.settings import MAX_HISTORY
from .models import Block
from django.db.models.signals import post_save


@receiver(post_save, sender=Block)
def limit_history_records(sender, instance, **kwargs):
    history = instance.history.all().order_by('-history_date')

    if history.count() > MAX_HISTORY:
        old_history = history[MAX_HISTORY:]

        # Удаляем каждую запись по отдельности
        for record in old_history:
            record.delete()
