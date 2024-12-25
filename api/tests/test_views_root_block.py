from django.contrib.auth.models import User
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from unittest.mock import patch

class RootBlockViewTest(APITestCase):
    def setUp(self):
        # Создаем пользователей для тестирования
        self.authenticated_user = User.objects.create_user(username="auth_user", password="password")
        self.main_page_user = User.objects.create_user(username="main_page", password="password")

        # Добавляем блоки к пользователям
        self.authenticated_user.blocks.create(id=1, name="Block 1")
        self.main_page_user.blocks.create(id=2, name="Main Page Block")

        # Устанавливаем клиент
        self.client = APIClient()

    @patch('app_name.views.get_flat_map_blocks')
    def test_authenticated_user(self, mock_get_flat_map_blocks):
        # Мокаем get_flat_map_blocks
        mock_get_flat_map_blocks.return_value = {1: {"name": "Block 1"}}

        # Аутентификация пользователя
        self.client.force_authenticate(user=self.authenticated_user)

        # Выполняем GET запрос
        response = self.client.get('/api/root-block/')

        # Проверяем статус и данные
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('root', response.data)
        self.assertEqual(response.data['root'], {"name": "Block 1"})

        # Проверяем вызов get_flat_map_blocks
        mock_get_flat_map_blocks.assert_called_once_with(self.authenticated_user.id, [1])

    @patch('app_name.views.get_flat_map_blocks')
    def test_unauthenticated_user(self, mock_get_flat_map_blocks):
        # Мокаем get_flat_map_blocks
        mock_get_flat_map_blocks.return_value = {2: {"name": "Main Page Block"}}

        # Выполняем GET запрос без аутентификации
        response = self.client.get('/api/root-block/')

        # Проверяем статус и данные
        self.assertEqual(response.status_code, status.HTTP_203_NON_AUTHORITATIVE_INFORMATION)
        self.assertIn('root', response.data)
        self.assertEqual(response.data['root'], {"name": "Main Page Block"})

        # Проверяем вызов get_flat_map_blocks
        mock_get_flat_map_blocks.assert_called_once_with(self.main_page_user.id, [2])

    def tearDown(self):
        # Очистка после каждого теста
        User.objects.all().delete()