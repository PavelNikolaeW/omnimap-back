import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE',
                      'block_api.settings')  # Замените 'block_api.settings' на путь к вашему settings.py

django.setup()

from django.db import transaction
from django.contrib.auth import get_user_model
from api.models import Block

User = get_user_model()


def save_dict_to_blocks(data, creator, parent_block=None):
    """
    Рекурсивно сохраняет вложенный словарь как иерархию блоков в базе данных с поддержкой служебных полей.

    :param data: Словарь, представляющий иерархию блоков с возможными служебными полями.
    :param creator: Объект пользователя, который будет назначен как создатель блоков.
    :param parent_block: Родительский блок для текущего уровня иерархии (используется рекурсивно).
    """
    if not isinstance(data, dict):
        raise ValueError("Входные данные должны быть словарём.")

    with transaction.atomic():
        for key, value in data.items():
            if not isinstance(key, str):
                raise ValueError("Ключи верхнего уровня должны быть строками, представляющими названия блоков.")

            if key.startswith('__'):
                raise ValueError("Служебные поля должны быть внутри блоков, а не на верхнем уровне.")

            # Если значение - это простое значение (не словарь), создаём блок с данными
            if not isinstance(value, dict):
                block = Block.objects.create(
                    creator=creator,
                    title=key,
                    data={'value': value}
                )
                if parent_block:
                    parent_block.children.add(block)
                continue

            # Инициализация полей блока
            block_fields = {}
            data_fields = {}
            visible_to_users = []
            editable_by_users = []
            access_type = 'inherited'  # Значение по умолчанию
            children = {}

            # Обработка вложенных ключей
            for sub_key, sub_value in value.items():
                if sub_key.startswith('__'):
                    # Обработка служебных полей
                    if sub_key == '__title__':
                        block_fields['title'] = sub_value
                    elif sub_key == '__access_type__':
                        block_fields['access_type'] = sub_value
                    elif sub_key == '__visible_to_users__':
                        visible_to_users = sub_value
                    elif sub_key == '__editable_by_users__':
                        editable_by_users = sub_value
                    elif sub_key == '__data__':
                        if not isinstance(sub_value, dict):
                            raise ValueError("__data__ должно быть словарём.")
                        data_fields.update(sub_value)
                    else:
                        raise ValueError(f"Неизвестное служебное поле: {sub_key}")
                else:
                    # Остальные ключи считаем дочерними блоками
                    children[sub_key] = sub_value

            # Установка заголовка (если не задано, используем ключ)
            title = block_fields.get('title', key)

            # Создаём блок
            block = Block.objects.create(
                creator=creator,
                title=title,
                data=data_fields,
                access_type=block_fields.get('access_type', 'inherited')
            )

            # Установка связей доступа
            if visible_to_users:
                users_visible = User.objects.filter(username__in=visible_to_users)
                existing_usernames = set(users_visible.values_list('username', flat=True))
                missing_users = set(visible_to_users) - existing_usernames
                if missing_users:
                    raise ValueError(f"Пользователи не найдены для visible_to_users: {', '.join(missing_users)}")
                block.visible_to_users.set(users_visible)

            if editable_by_users:
                users_editable = User.objects.filter(username__in=editable_by_users)
                existing_usernames = set(users_editable.values_list('username', flat=True))
                missing_users = set(editable_by_users) - existing_usernames
                if missing_users:
                    raise ValueError(f"Пользователи не найдены для editable_by_users: {', '.join(missing_users)}")
                block.editable_by_users.set(users_editable)

            # Установка связи с родительским блоком, если он указан
            if parent_block:
                parent_block.children.add(block)

            # Рекурсивная обработка дочерних блоков
            if children:
                save_dict_to_blocks(children, creator, parent_block=block)


if __name__ == "__main__":
    from data import data

    parent_block_id = Block.objects.get(pk='1efafe73-8926-64a2-bbf4-d1f6ea01a691')
    creator = User.objects.get(pk=3)
    save_dict_to_blocks(data, creator, parent_block_id)

