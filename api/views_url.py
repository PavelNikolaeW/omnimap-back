"""Эндпоинты для управления общими ссылками на блоки."""

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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'block_id': ['delete'], })
def create_url(request, block_id):
    """Создаёт публичный slug для блока, если он ещё не занят."""

    block = get_object_or_404(Block, id=block_id)
    slug = request.data.get('slug')
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
    send_message_subscribe_user(list(data.keys()), [-1])
    return Response(data, status=status.HTTP_200_OK)


@api_view(['GET'])
def load_tree(request):
    source = request.data['tree']
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
    source = request.data['tree']
    if not BlockPermission.objects.filter(block_id=source, user=request.user).exists():
        return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

    with connection.cursor() as cursor:
        cursor.execute(get_block_for_url, {'block_id': source, 'max_depth': settings.LINK_LOAD_DEPTH_LIMIT})
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    data = block_link_serializer(rows, settings.LINK_LOAD_DEPTH_LIMIT)
    return Response(data, status=status.HTTP_200_OK)