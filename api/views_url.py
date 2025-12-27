"""Эндпоинты для управления общими ссылками на блоки."""

import json
import re
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from django.conf import settings
from .models import Block, ALLOWED_SHOW_PERMISSIONS, BlockUrlLinkModel, BlockPermission
from .serializers import links_serializer, block_link_serializer
from api.utils.query import get_block_for_url
from .tasks import send_message_subscribe_user
from .utils.decorators import check_block_permissions

# Паттерн для валидации slug: только буквы, цифры, дефисы и подчёркивания
SLUG_PATTERN = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'block_id': ['delete'], })
def create_url(request, block_id):
    """Создаёт публичный slug для блока, если он ещё не занят."""

    block = get_object_or_404(Block, id=block_id)
    slug = request.data.get('slug')

    # Валидация slug
    if not slug or not isinstance(slug, str):
        return Response({'message': 'slug is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not SLUG_PATTERN.match(slug):
        return Response(
            {'message': 'Invalid slug format. Use only letters, numbers, hyphens and underscores (1-100 chars)'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not BlockUrlLinkModel.objects.filter(slug=slug).exists():
        link = BlockUrlLinkModel.objects.create(source=block, slug=slug, creator=request.user)
        return Response(links_serializer([link]), status=status.HTTP_200_OK)
    return Response({'message': 'Create link error, slug exist'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_slug(request, slug):
    """
    Проверяет, существует ли URL с таким slug.
    Если slug занят -> status: unavailable
    Если slug свободен -> status: available
    """
    if BlockUrlLinkModel.objects.filter(slug=slug).exists():
        return Response({'status': 'unavailable'}, status=status.HTTP_200_OK)
    return Response({'status': 'available'}, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, ])
@check_block_permissions({'block_id': ALLOWED_SHOW_PERMISSIONS, })
def get_urls(request, block_id):
    """Возвращает все slug'и, привязанные к указанному блоку."""
    links = BlockUrlLinkModel.objects.filter(source_id=block_id)
    return Response(links_serializer(links), status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, ])
@check_block_permissions({'block_id': ['delete']})
def delete_url(request, block_id, slug):
    """Удаляет slug, если запрос инициировал пользователь с правом удаления."""
    link = get_object_or_404(BlockUrlLinkModel, slug=slug)
    link.delete()
    return Response({'detail': 'Deleted successfully'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def block_url(request, slug):
    """Возвращает дерево и подписывает клиента на обновления по slug."""
    link = get_object_or_404(BlockUrlLinkModel, slug=slug)
    source = link.source

    with connection.cursor() as cursor:
        cursor.execute(get_block_for_url, {'block_id': str(source.id), 'max_depth': settings.LINK_LOAD_DEPTH_LIMIT})
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    data = block_link_serializer(rows, settings.LINK_LOAD_DEPTH_LIMIT)
    # Исправлено: используем .delay() для асинхронного вызова
    send_message_subscribe_user.delay(list(data.keys()), [-1])
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])  # Исправлено: POST вместо GET для запросов с body
def load_tree(request):
    """Загружает дерево по ID, если у него есть публичный URL."""
    source = request.data.get('tree')

    # Исправлено: проверка наличия параметра
    if not source:
        return Response({'detail': 'tree parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

    if not BlockUrlLinkModel.objects.filter(source=source).exists():
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    with connection.cursor() as cursor:
        cursor.execute(get_block_for_url, {'block_id': source, 'max_depth': settings.LINK_LOAD_DEPTH_LIMIT})
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    data = block_link_serializer(rows, settings.LINK_LOAD_DEPTH_LIMIT)
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def load_nodes(request):
    """Загружает узлы дерева для авторизованного пользователя."""
    source = request.data.get('tree')

    # Исправлено: проверка наличия параметра
    if not source:
        return Response({'detail': 'tree parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Исправлено: исключаем deny-право из проверки
    if not BlockPermission.objects.filter(
        block_id=source,
        user=request.user
    ).exclude(permission='deny').exists():
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    with connection.cursor() as cursor:
        cursor.execute(get_block_for_url, {'block_id': source, 'max_depth': settings.LINK_LOAD_DEPTH_LIMIT})
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    data = block_link_serializer(rows, settings.LINK_LOAD_DEPTH_LIMIT)
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def export_blocks(request):
    """
    Экспортирует блоки в формате, совместимом с /api/v1/import/.

    Request body:
    {
        "block_ids": ["uuid1", "uuid2", ...],  // корневые блоки для экспорта
        "include_children": true,               // включать дочерние (по умолчанию true)
        "max_depth": 10,                        // макс. глубина (по умолчанию LINK_LOAD_DEPTH_LIMIT)
        "include_permissions": false            // включать права (по умолчанию false)
    }

    Response:
    {
        "blocks": [
            {
                "id": "uuid",
                "title": "...",
                "data": {...},
                "parent_id": "uuid" | null,
                "permissions": {...}  // если include_permissions=true
            },
            ...
        ],
        "total": 42
    }
    """
    block_ids = request.data.get('block_ids', [])
    include_children = request.data.get('include_children', True)
    max_depth = request.data.get('max_depth', settings.LINK_LOAD_DEPTH_LIMIT)
    include_permissions = request.data.get('include_permissions', False)

    # Валидация
    if not block_ids:
        return Response(
            {'detail': 'block_ids is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if not isinstance(block_ids, list):
        return Response(
            {'detail': 'block_ids must be an array'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Ограничение глубины
    max_depth = min(max_depth, settings.LINK_LOAD_DEPTH_LIMIT)

    # Проверяем права на все запрошенные блоки
    user = request.user
    accessible_blocks = BlockPermission.objects.filter(
        block_id__in=block_ids,
        user=user
    ).exclude(permission='deny').values_list('block_id', flat=True)

    accessible_set = set(str(bid) for bid in accessible_blocks)
    requested_set = set(block_ids)

    forbidden_blocks = requested_set - accessible_set
    if forbidden_blocks:
        return Response(
            {'detail': f'Access denied for blocks: {list(forbidden_blocks)}'},
            status=status.HTTP_403_FORBIDDEN
        )

    # Собираем все блоки
    all_blocks = []

    for block_id in block_ids:
        if include_children:
            # Загружаем дерево рекурсивно
            with connection.cursor() as cursor:
                cursor.execute(
                    get_block_for_url,
                    {'block_id': block_id, 'max_depth': max_depth}
                )
                columns = [col[0] for col in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

            for row in rows:
                all_blocks.append(row)
        else:
            # Загружаем только указанный блок
            block = Block.objects.filter(id=block_id).values(
                'id', 'title', 'data', 'parent_id', 'creator_id', 'updated_at'
            ).first()
            if block:
                all_blocks.append(block)

    # Убираем дубликаты по id
    seen_ids = set()
    unique_blocks = []
    for block in all_blocks:
        block_id = str(block['id'])
        if block_id not in seen_ids:
            seen_ids.add(block_id)
            unique_blocks.append(block)

    # Форматируем в формат импорта
    export_data = []
    for block in unique_blocks:
        # Обрабатываем data — из raw SQL приходит строка JSON
        data = block.get('data') or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}

        block_export = {
            'id': str(block['id']),
            'title': block.get('title'),
            'data': data,
            'parent_id': str(block['parent_id']) if block.get('parent_id') else None,
        }

        if include_permissions:
            # Загружаем права для блока
            perms = BlockPermission.objects.filter(
                block_id=block['id']
            ).values('user_id', 'permission')

            block_export['permissions'] = {
                'users': [
                    {'user_id': p['user_id'], 'permission': p['permission']}
                    for p in perms
                ]
            }

        export_data.append(block_export)

    return Response({
        'blocks': export_data,
        'total': len(export_data)
    }, status=status.HTTP_200_OK)