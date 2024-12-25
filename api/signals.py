from functools import wraps

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from block_api.settings import MAX_HISTORY
from .models import Block
from django.db.models.signals import post_save
import threading
from contextlib import contextmanager

_thread_locals = threading.local()


def disable_signals():
    _thread_locals.signals_disabled = True


def enable_signals():
    _thread_locals.signals_disabled = False


def signals_disabled():
    return getattr(_thread_locals, 'signals_disabled', False)


@contextmanager
def signal_disabled_context():
    disable_signals()
    try:
        yield
    finally:
        enable_signals()


def disable_signals_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with signal_disabled_context():
            return func(*args, **kwargs)

    return wrapper


@receiver(m2m_changed, sender=Block.children.through)
def update_data_on_children_change(sender, instance, action, reverse, pk_set, **kwargs):
    if signals_disabled():
        return
    if action == 'post_add':
        for child_id in pk_set:
            instance.update_child_order_on_add(str(child_id))
    elif action == 'post_remove':
        for child_id in pk_set:
            instance.update_child_order_on_remove(str(child_id))
    elif action == 'post_clear':
        instance.data['childOrder'] = []
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
