"""Эндпоинты для загрузки и управления файлами блоков."""

import io
import uuid
from PIL import Image
from django.conf import settings
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser

from .models import Block, BlockFile, BlockPermission


def validate_image(file):
    """
    Валидирует загруженное изображение.
    Возвращает (is_valid, error_message, image_info).
    """
    # Проверка размера
    max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
    if file.size > max_size:
        return False, f'File too large. Max size: {max_size // (1024 * 1024)} MB', None

    # Проверка MIME-типа
    allowed_types = getattr(settings, 'ALLOWED_IMAGE_TYPES',
                           ['image/jpeg', 'image/png', 'image/gif', 'image/webp'])
    if file.content_type not in allowed_types:
        return False, f'Invalid file type. Allowed: {", ".join(allowed_types)}', None

    # Проверка что это реальное изображение через Pillow
    try:
        file.seek(0)
        img = Image.open(file)
        img.verify()
        file.seek(0)

        # Получаем размеры
        img = Image.open(file)
        width, height = img.size
        file.seek(0)

        return True, None, {'width': width, 'height': height}
    except Exception as e:
        return False, f'Invalid image file: {str(e)}', None


def create_thumbnail(image_file, max_size=None):
    """
    Создаёт превью изображения.
    Возвращает ContentFile с превью или None при ошибке.
    """
    if max_size is None:
        max_size = getattr(settings, 'THUMBNAIL_SIZE', (300, 300))

    try:
        image_file.seek(0)
        img = Image.open(image_file)

        # Конвертируем в RGB если нужно (для JPEG)
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')

        # Создаём превью с сохранением пропорций
        img.thumbnail(max_size, Image.Resampling.LANCZOS)

        # Сохраняем в буфер
        thumb_io = io.BytesIO()
        img.save(thumb_io, format='JPEG', quality=85)
        thumb_io.seek(0)

        return ContentFile(thumb_io.read())
    except Exception:
        return None


def check_block_permission(user, block_id, required_permissions):
    """
    Проверяет права пользователя на блок.
    required_permissions — список допустимых прав (например, ['edit', 'delete']).
    """
    permission = BlockPermission.objects.filter(
        block_id=block_id,
        user=user
    ).first()

    if not permission:
        return False

    return permission.permission in required_permissions


class BlockFileView(APIView):
    """
    GET /api/v1/blocks/{block_id}/file/ — получить информацию о файле
    POST /api/v1/blocks/{block_id}/file/ — загрузить файл
    DELETE /api/v1/blocks/{block_id}/file/ — удалить файл
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, block_id):
        """Получить информацию о файле блока."""
        block = get_object_or_404(Block, id=block_id)

        # Проверяем право на просмотр
        if not check_block_permission(request.user, block_id, ['view', 'edit', 'edit_ac', 'delete']):
            return Response(
                {'detail': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            block_file = block.file
        except BlockFile.DoesNotExist:
            return Response(
                {'detail': 'No file attached to this block'},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            'id': str(block_file.id),
            'filename': block_file.filename,
            'content_type': block_file.content_type,
            'size': block_file.size,
            'width': block_file.width,
            'height': block_file.height,
            'url': request.build_absolute_uri(block_file.file.url),
            'thumbnail_url': request.build_absolute_uri(block_file.thumbnail.url) if block_file.thumbnail else None,
            'created_at': block_file.created_at.isoformat(),
        })

    def post(self, request, block_id):
        """Загрузить файл в блок."""
        block = get_object_or_404(Block, id=block_id)

        # Проверяем право на редактирование
        if not check_block_permission(request.user, block_id, ['edit', 'edit_ac', 'delete']):
            return Response(
                {'detail': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Проверяем наличие файла в запросе
        if 'file' not in request.FILES:
            return Response(
                {'detail': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )

        uploaded_file = request.FILES['file']

        # Валидация изображения
        is_valid, error_msg, image_info = validate_image(uploaded_file)
        if not is_valid:
            return Response(
                {'detail': error_msg},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Удаляем старый файл если есть
        try:
            old_file = block.file
            old_file.delete()
        except BlockFile.DoesNotExist:
            pass

        # Генерируем уникальное имя файла
        ext = uploaded_file.name.split('.')[-1].lower() if '.' in uploaded_file.name else 'jpg'
        new_filename = f"{uuid.uuid4()}.{ext}"

        # Создаём превью
        thumbnail_content = create_thumbnail(uploaded_file)

        # Создаём запись
        block_file = BlockFile(
            block=block,
            filename=uploaded_file.name,
            content_type=uploaded_file.content_type,
            size=uploaded_file.size,
            width=image_info['width'] if image_info else None,
            height=image_info['height'] if image_info else None,
            uploaded_by=request.user,
        )

        # Сохраняем файл
        uploaded_file.seek(0)
        block_file.file.save(new_filename, uploaded_file, save=False)

        # Сохраняем превью
        if thumbnail_content:
            thumb_filename = f"thumb_{new_filename.rsplit('.', 1)[0]}.jpg"
            block_file.thumbnail.save(thumb_filename, thumbnail_content, save=False)

        block_file.save()

        return Response({
            'id': str(block_file.id),
            'filename': block_file.filename,
            'content_type': block_file.content_type,
            'size': block_file.size,
            'width': block_file.width,
            'height': block_file.height,
            'url': request.build_absolute_uri(block_file.file.url),
            'thumbnail_url': request.build_absolute_uri(block_file.thumbnail.url) if block_file.thumbnail else None,
            'created_at': block_file.created_at.isoformat(),
        }, status=status.HTTP_201_CREATED)

    def delete(self, request, block_id):
        """Удалить файл блока."""
        block = get_object_or_404(Block, id=block_id)

        # Проверяем право на редактирование/удаление
        if not check_block_permission(request.user, block_id, ['edit', 'edit_ac', 'delete']):
            return Response(
                {'detail': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            block_file = block.file
            block_file.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except BlockFile.DoesNotExist:
            return Response(
                {'detail': 'No file attached to this block'},
                status=status.HTTP_404_NOT_FOUND
            )
