import json
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import Block

class Command(BaseCommand):
    help = 'Экспорт данных блоков для указанного пользователя в JSON файл с поддержкой UUID'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Имя пользователя для экспорта блоков')
        parser.add_argument('output_file', type=str, help='Путь к файлу для сохранения JSON данных')

    def handle(self, *args, **kwargs):
        username = kwargs['username']
        output_file = kwargs['output_file']

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Пользователь с именем {username} не найден.'))
            return

        # Получаем все блоки, созданные пользователем
        blocks = Block.objects.filter(creator=user)

        if not blocks.exists():
            self.stdout.write(self.style.WARNING(f'У пользователя {username} нет блоков для экспорта.'))
            return

        blocks_data = []
        for block in blocks:
            # Сохраняем все связи с блоками, включая ManyToMany поля, используя UUID
            block_data = {
                'uuid': str(block.pk),  # UUID блока
                'title': block.title,
                'access_type': block.access_type,
                'creator': block.creator.username,
                'data': block.data,
                'children': [str(child.pk) for child in block.children.all()],  # UUID дочерних блоков
                'visible_to_users': list(block.visible_to_users.values_list('pk', flat=True)),
                'editable_by_users': list(block.editable_by_users.values_list('pk', flat=True)),
            }
            blocks_data.append(block_data)

        # Записываем данные в JSON файл
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(blocks_data, f, indent=4, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(f'Данные блоков успешно экспортированы в файл {output_file}'))
