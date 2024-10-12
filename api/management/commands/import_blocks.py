import json
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import Block
from uuid import UUID


class Command(BaseCommand):
    help = 'Импорт данных блоков из JSON файла для указанного пользователя с поддержкой UUID'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Имя пользователя для импорта блоков')
        parser.add_argument('input_file', type=str, help='Путь к JSON файлу с данными блоков')

    def handle(self, *args, **kwargs):
        username = kwargs['username']
        input_file = kwargs['input_file']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Пользователь с именем {username} не найден.'))
            return

        # Открываем JSON файл и загружаем данные
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                blocks_data = json.load(f)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f'Файл {input_file} не найден.'))
            return

        # Словарь для отслеживания созданных блоков по их UUID
        created_blocks = {}

        # Создаем блоки с сохранением UUID
        for block_data in blocks_data:
            block_uuid = block_data['uuid']
            block, created = Block.objects.update_or_create(
                pk=UUID(block_uuid),  # Используем UUID при создании блока
                defaults={
                    'creator': user,
                    'title': block_data['title'],
                    'access_type': block_data['access_type'],
                    'data': block_data['data'],
                }
            )
            created_blocks[block_uuid] = block
            self.stdout.write(self.style.SUCCESS(f"Создан или обновлён блок '{block.title}' с UUID {block_uuid}"))

        # Восстанавливаем связи ManyToMany (children, visible_to_users, editable_by_users)
        for block_data in blocks_data:
            block = created_blocks[block_data['uuid']]

            # Восстанавливаем связи с детьми
            children_uuids = block_data['children']
            if children_uuids:
                block.children.set([created_blocks[uuid] for uuid in children_uuids if uuid in created_blocks])

            # Восстанавливаем видимых пользователей
            visible_user_pks = block_data['visible_to_users']
            if visible_user_pks:
                block.visible_to_users.set(User.objects.filter(pk__in=visible_user_pks))

            # Восстанавливаем редактируемых пользователей
            editable_user_pks = block_data['editable_by_users']
            if editable_user_pks:
                block.editable_by_users.set(User.objects.filter(pk__in=editable_user_pks))

        self.stdout.write(
            self.style.SUCCESS(f'Все блоки успешно импортированы и восстановлены для пользователя {username}.'))

