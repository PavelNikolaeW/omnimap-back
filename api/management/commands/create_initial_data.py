import os

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import Block, BlockPermission  # ваш модель для данных
from api.management.commands.import_blocks import Command as ImportBloksCommand


class Command(BaseCommand):
    help = 'Создание суперпользователя и начальных данных'

    def handle(self, *args, **kwargs):
        # Создание суперпользователя
        if not  User.objects.filter(username='admin').exists():
            admin = User.objects.create_superuser('admin', 'admin@example.com',
                                                  os.environ.get('DJANGO_ADMIN_PASS', 'admin'))
            admin_block = Block.objects.create(title='admin', creator=admin)
            BlockPermission.objects.create(block=admin_block, user=admin, permission='delete')
            self.stdout.write(self.style.SUCCESS('Суперпользователь andin создан.'))

        if not User.objects.filter(username='main_page').exists():
            main_page_user = User.objects.create_superuser('main_page', 'main_page@example.com',
                                                           os.environ.get('DJANGO_ADMIN_PASS', 'admin'))
            self.stdout.write(self.style.SUCCESS('Суперпользователь main_page создан.'))



            main_block = Block.objects.create(title='omniMap', creator=main_page_user)
            auth_block = Block.objects.create(title='authBlock', creator=main_page_user)
            login_block = Block.objects.create(title='login', data={'view': 'auth'}, creator=main_page_user)
            reg_block = Block.objects.create(title='registration', data={'view': 'registration'}, creator=main_page_user)
            BlockPermission.objects.create(block=main_block, user=main_page_user, permission='delete')
            BlockPermission.objects.create(block=auth_block, user=main_page_user, permission='delete')
            BlockPermission.objects.create(block=login_block, user=main_page_user, permission='delete')
            BlockPermission.objects.create(block=reg_block, user=main_page_user, permission='delete')

            main_block.add_child(auth_block)
            auth_block.add_children([login_block, reg_block])

