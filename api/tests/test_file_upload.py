"""Тесты для загрузки файлов в блоки."""

import io
import pytest
from PIL import Image
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from api.models import Block, BlockPermission, BlockFile

User = get_user_model()


def create_test_image(width=100, height=100, format='PNG'):
    """Создаёт тестовое изображение в памяти."""
    file = io.BytesIO()
    image = Image.new('RGB', (width, height), color='red')
    image.save(file, format=format)
    file.name = f'test.{format.lower()}'
    file.seek(0)
    return file


def create_test_image_file(width=100, height=100, format='PNG', name=None):
    """Создаёт тестовое изображение как InMemoryUploadedFile."""
    from django.core.files.uploadedfile import InMemoryUploadedFile

    file = io.BytesIO()
    image = Image.new('RGB', (width, height), color='red')
    image.save(file, format=format)
    file.seek(0)

    if name is None:
        name = f'test.{format.lower()}'

    content_type = {
        'PNG': 'image/png',
        'JPEG': 'image/jpeg',
        'GIF': 'image/gif',
        'WEBP': 'image/webp',
    }.get(format.upper(), 'image/png')

    return InMemoryUploadedFile(
        file=file,
        field_name='file',
        name=name,
        content_type=content_type,
        size=file.getbuffer().nbytes,
        charset=None
    )


@pytest.fixture
def user(db):
    """Создаёт тестового пользователя."""
    return User.objects.create_user(
        username='testuser',
        email='test@test.com',
        password='testpass123'
    )


@pytest.fixture
def other_user(db):
    """Создаёт другого пользователя."""
    return User.objects.create_user(
        username='otheruser',
        email='other@test.com',
        password='testpass123'
    )


@pytest.fixture
def block(db, user):
    """Создаёт тестовый блок."""
    block = Block.objects.create(
        title='Test Block',
        creator=user,
        data={}
    )
    # Даём пользователю права на редактирование
    BlockPermission.objects.create(
        block=block,
        user=user,
        permission='edit'
    )
    return block


@pytest.fixture
def block_view_only(db, user):
    """Создаёт блок только для просмотра."""
    block = Block.objects.create(
        title='View Only Block',
        creator=user,
        data={}
    )
    BlockPermission.objects.create(
        block=block,
        user=user,
        permission='view'
    )
    return block


@pytest.fixture
def auth_client(user):
    """Аутентифицированный клиент."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def other_client(other_user):
    """Клиент другого пользователя."""
    client = APIClient()
    client.force_authenticate(user=other_user)
    return client


@pytest.mark.django_db
class TestFileUpload:
    """Тесты загрузки файлов."""

    def test_upload_valid_image(self, auth_client, block):
        """Успешная загрузка валидного изображения."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file()

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 201
        assert 'id' in response.data
        assert 'url' in response.data
        assert 'thumbnail_url' in response.data
        assert response.data['content_type'] == 'image/png'
        assert response.data['width'] == 100
        assert response.data['height'] == 100

    def test_upload_jpeg_image(self, auth_client, block):
        """Загрузка JPEG изображения."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file(format='JPEG')

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 201
        assert response.data['content_type'] == 'image/jpeg'

    def test_upload_replaces_existing(self, auth_client, block):
        """Повторная загрузка заменяет существующий файл."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        # Загружаем первый файл
        image1 = create_test_image_file(width=100, height=100)
        response1 = auth_client.post(url, {'file': image1}, format='multipart')
        assert response1.status_code == 201
        first_file_id = response1.data['id']

        # Загружаем второй файл
        image2 = create_test_image_file(width=200, height=200)
        response2 = auth_client.post(url, {'file': image2}, format='multipart')
        assert response2.status_code == 201

        # Новый файл должен иметь другой ID и размеры
        assert response2.data['id'] != first_file_id
        assert response2.data['width'] == 200
        assert response2.data['height'] == 200

        # Должен быть только один файл
        assert BlockFile.objects.filter(block=block).count() == 1

    def test_upload_without_permission(self, other_client, block):
        """Загрузка без прав возвращает 403."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file()

        response = other_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 403

    def test_upload_view_only_permission(self, auth_client, block_view_only):
        """Загрузка с правами только на просмотр возвращает 403."""
        url = reverse('api:block-file', kwargs={'block_id': block_view_only.id})
        image = create_test_image_file()

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 403

    def test_upload_no_file(self, auth_client, block):
        """Запрос без файла возвращает 400."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        response = auth_client.post(url, {}, format='multipart')

        assert response.status_code == 400
        assert 'No file provided' in response.data['detail']

    def test_upload_too_large(self, auth_client, block, settings):
        """Слишком большой файл отклоняется."""
        settings.MAX_UPLOAD_SIZE = 1024  # 1 KB для теста

        url = reverse('api:block-file', kwargs={'block_id': block.id})
        # Создаём изображение больше лимита
        image = create_test_image_file(width=1000, height=1000)

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 400
        assert 'too large' in response.data['detail'].lower()

    def test_upload_dimensions_too_large(self, auth_client, block, settings):
        """Изображение с размерами больше MAX_IMAGE_DIMENSIONS отклоняется."""
        settings.MAX_IMAGE_DIMENSIONS = (500, 500)  # Маленький лимит для теста
        settings.MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # Увеличим лимит размера

        url = reverse('api:block-file', kwargs={'block_id': block.id})
        # Создаём изображение больше лимита размеров
        image = create_test_image_file(width=600, height=400)

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 400
        assert 'dimensions too large' in response.data['detail'].lower()

    def test_upload_invalid_type(self, auth_client, block):
        """Неподдерживаемый тип файла отклоняется."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        # Создаём текстовый файл, притворяющийся изображением
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake_image = SimpleUploadedFile(
            name='test.exe',
            content=b'not an image',
            content_type='application/octet-stream'
        )

        response = auth_client.post(url, {'file': fake_image}, format='multipart')

        assert response.status_code == 400

    def test_unauthenticated_upload(self, block):
        """Неаутентифицированный запрос возвращает 401."""
        client = APIClient()
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file()

        response = client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 401


@pytest.mark.django_db
class TestFileGet:
    """Тесты получения информации о файле."""

    def test_get_file_info(self, auth_client, block):
        """Получение информации о файле."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        # Загружаем файл
        image = create_test_image_file()
        auth_client.post(url, {'file': image}, format='multipart')

        # Получаем информацию
        response = auth_client.get(url)

        assert response.status_code == 200
        assert 'id' in response.data
        assert 'url' in response.data
        assert 'thumbnail_url' in response.data
        assert response.data['content_type'] == 'image/png'

    def test_get_file_not_found(self, auth_client, block):
        """Запрос файла для блока без файла возвращает 404."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        response = auth_client.get(url)

        assert response.status_code == 404

    def test_get_file_without_permission(self, other_client, block):
        """Получение файла без прав возвращает 403."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        response = other_client.get(url)

        assert response.status_code == 403


@pytest.mark.django_db
class TestFileDelete:
    """Тесты удаления файлов."""

    def test_delete_file(self, auth_client, block):
        """Успешное удаление файла."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        # Загружаем файл
        image = create_test_image_file()
        auth_client.post(url, {'file': image}, format='multipart')

        # Удаляем
        response = auth_client.delete(url)

        assert response.status_code == 204
        assert not BlockFile.objects.filter(block=block).exists()

    def test_delete_file_not_found(self, auth_client, block):
        """Удаление несуществующего файла возвращает 404."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        response = auth_client.delete(url)

        assert response.status_code == 404

    def test_delete_without_permission(self, other_client, block, auth_client):
        """Удаление без прав возвращает 403."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})

        # Загружаем файл от имени владельца
        image = create_test_image_file()
        auth_client.post(url, {'file': image}, format='multipart')

        # Пытаемся удалить от другого пользователя
        response = other_client.delete(url)

        assert response.status_code == 403

    def test_delete_view_only_permission(self, auth_client, block_view_only):
        """Удаление с правами только на просмотр возвращает 403."""
        url = reverse('api:block-file', kwargs={'block_id': block_view_only.id})

        response = auth_client.delete(url)

        # 403 потому что нет прав на редактирование
        assert response.status_code == 403


@pytest.mark.django_db
class TestThumbnailGeneration:
    """Тесты генерации превью."""

    def test_thumbnail_created(self, auth_client, block):
        """Превью создаётся при загрузке."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file(width=1000, height=1000)

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 201
        assert response.data['thumbnail_url'] is not None

        # Проверяем что превью существует в БД
        block_file = BlockFile.objects.get(block=block)
        assert block_file.thumbnail is not None

    def test_small_image_gets_thumbnail(self, auth_client, block):
        """Маленькое изображение тоже получает превью."""
        url = reverse('api:block-file', kwargs={'block_id': block.id})
        image = create_test_image_file(width=50, height=50)

        response = auth_client.post(url, {'file': image}, format='multipart')

        assert response.status_code == 201
        # Превью всё равно создаётся
        assert response.data['thumbnail_url'] is not None
