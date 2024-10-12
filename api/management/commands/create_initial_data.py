import os

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import Block  # ваш модель для данных
from api.management.commands.import_blocks import Command as ImportBloksCommand


class Command(BaseCommand):
    help = 'Создание суперпользователя и начальных данных'

    def handle(self, *args, **kwargs):
        # Создание суперпользователя
        if not User.objects.filter(username='admin').exists():
            admin = User.objects.create_superuser('admin', 'admin@example.com',
                                                  os.environ.get('DJANGO_ADMIN_PASS', 'admin'))
            main_page_user = User.objects.create_superuser('main_page', 'main_page@example.com',
                                                           os.environ.get('DJANGO_ADMIN_PASS', 'admin'))
            self.stdout.write(self.style.SUCCESS('Суперпользователь создан.'))

            command = ImportBloksCommand()
            command.handle(username=main_page_user.username, input_file='main_page.json')

