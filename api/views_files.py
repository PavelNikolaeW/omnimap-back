"""Эндпоинты для загрузки и управления файлами блоков."""

import io
import uuid
from typing import Tuple, Optional, Dict, Any, List

from PIL import Image
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.request import Request

from .models import Block, BlockFile, BlockPermission
from .constants import CONTENT_TYPE_MAP, get_extension_for_content_type


def validate_image(file: UploadedFile) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Валидирует загруженное изображение.

    Args:
        file: Загруженный файл

    Returns:
        Tuple (is_valid, error_message, image_info)
        image_info содержит width и height при успехе
    """
    # Проверка размера файла
    max_size: int = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)
    if file.size > max_size:
        return False, f'File too large. Max size: {max_size // (1024 * 1024)} MB', None

    # Проверка MIME-типа
    allowed_types: List[str] = getattr(settings, 'ALLOWED_IMAGE_TYPES',
                                        list(CONTENT_TYPE_MAP.keys()))
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

        # Проверка максимальных размеров изображения
        max_dimensions: Tuple[int, int] = getattr(settings, 'MAX_IMAGE_DIMENSIONS', (4096, 4096))
        if width > max_dimensions[0] or height > max_dimensions[1]:
            return False, f'Image dimensions too large. Max: {max_dimensions[0]}x{max_dimensions[1]}', None

        return True, None, {'width': width, 'height': height}
    except Exception as e:
        return False, f'Invalid image file: {str(e)}', None


def optimize_image(file: UploadedFile, content_type: str) -> Tuple[ContentFile, int]:
    """
    Оптимизирует изображение (сжимает JPEG, оптимизирует PNG).

    Args:
        file: Загруженный файл
        content_type: MIME-тип файла

    Returns:
        Tuple (optimized_content, new_size)
    """
    quality: int = getattr(settings, 'JPEG_QUALITY', 85)

    try:
        file.seek(0)
        img = Image.open(file)

        output = io.BytesIO()

        if content_type == 'image/jpeg':
            # Конвертируем в RGB если нужно
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.save(output, format='JPEG', quality=quality, optimize=True)
        elif content_type == 'image/png':
            img.save(output, format='PNG', optimize=True)
        elif content_type == 'image/webp':
            img.save(output, format='WEBP', quality=quality)
        else:
            # Для других форматов просто копируем
            file.seek(0)
            return ContentFile(file.read()), file.size

        output.seek(0)
        content = ContentFile(output.read())
        return content, len(content)
    except Exception:
        # При ошибке возвращаем оригинал
        file.seek(0)
        return ContentFile(file.read()), file.size


def create_thumbnail(
    image_file: UploadedFile,
    max_size: Optional[Tuple[int, int]] = None
) -> Optional[ContentFile]:
    """
    Создаёт превью изображения.

    Args:
        image_file: Исходный файл изображения
        max_size: Максимальный размер превью (ширина, высота)

    Returns:
        ContentFile с превью или None при ошибке
    """
    if max_size is None:
        max_size = getattr(settings, 'THUMBNAIL_SIZE', (300, 300))

    quality: int = getattr(settings, 'JPEG_QUALITY', 85)

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
        img.save(thumb_io, format='JPEG', quality=quality, optimize=True)
        thumb_io.seek(0)

        return ContentFile(thumb_io.read())
    except Exception:
        return None


def check_block_permission(
    user: Any,
    block_id: uuid.UUID,
    required_permissions: List[str]
) -> bool:
    """
    Проверяет права пользователя на блок.

    Args:
        user: Пользователь Django
        block_id: UUID блока
        required_permissions: Список допустимых прав

    Returns:
        True если у пользователя есть одно из требуемых прав
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
    Управление файлами блоков.

    GET /api/v1/blocks/{block_id}/file/ — получить информацию о файле
    POST /api/v1/blocks/{block_id}/file/ — загрузить файл
    DELETE /api/v1/blocks/{block_id}/file/ — удалить файл
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request: Request, block_id: uuid.UUID) -> Response:
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

    def post(self, request: Request, block_id: uuid.UUID) -> Response:
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

        uploaded_file: UploadedFile = request.FILES['file']

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

        # Оптимизируем изображение если включено
        optimize_uploads: bool = getattr(settings, 'OPTIMIZE_UPLOADS', True)
        content_type: str = uploaded_file.content_type
        file_size: int = uploaded_file.size

        if optimize_uploads and content_type in ['image/jpeg', 'image/png', 'image/webp']:
            optimized_content, file_size = optimize_image(uploaded_file, content_type)
            file_to_save = optimized_content
        else:
            uploaded_file.seek(0)
            file_to_save = ContentFile(uploaded_file.read())

        # Генерируем уникальное имя файла
        ext = get_extension_for_content_type(content_type)
        new_filename = f"{uuid.uuid4()}.{ext}"

        # Создаём превью
        uploaded_file.seek(0)
        thumbnail_content = create_thumbnail(uploaded_file)

        # Создаём запись
        block_file = BlockFile(
            block=block,
            filename=uploaded_file.name,
            content_type=content_type,
            size=file_size,
            width=image_info['width'] if image_info else None,
            height=image_info['height'] if image_info else None,
            uploaded_by=request.user,
        )

        # Сохраняем файл
        block_file.file.save(new_filename, file_to_save, save=False)

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

    def delete(self, request: Request, block_id: uuid.UUID) -> Response:
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
