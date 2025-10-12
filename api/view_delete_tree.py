import uuid

from django.conf import settings
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from api.models import Block, BlockLink
from api.serializers import get_object_for_block
from api.utils.decorators import check_block_permissions
from api.utils.query import delete_tree_query
from api.tasks import send_message_block_update, send_message_unsubscribe_user


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'tree_id': ['delete']})
def delete_tree(request, tree_id):
    """Удаление дерева блоков с проверкой ссылок и разрешений"""

    block = get_object_or_404(Block, id=tree_id)

    # Проверяем наличие исходящих ссылок
    if BlockLink.objects.filter(source=block).exists():
        return Response({'detail': 'You cannot delete a block. It has existing links.'},
                        status=status.HTTP_400_BAD_REQUEST)

    user_id = request.user.id

    # Если у блока есть source (он является частью связи)
    if (source := block.data.get('source')):
        return _delete_single_block(block, source)

    return _delete_tree(block, user_id)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'tree_id': ['delete']})
def force_delete_tree(request, tree_id):
    """Удаляет блок или дерево, переназначая ссылки на служебный блок."""

    block = get_object_or_404(Block, id=tree_id)

    if (source := block.data.get('source')):
        return _delete_single_block(block, source)

    user_id = request.user.id
    block_ids = _get_deletable_block_ids(block, user_id)

    if not block_ids:
        return Response({'detail': 'Forbidden. Some sub-branches are not available for deletion.'},
                        status=status.HTTP_403_FORBIDDEN)

    try:
        service_block = _get_service_block()
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if block.id == service_block.id or service_block.id in block_ids:
        return Response({'detail': 'You cannot delete the service block.'},
                        status=status.HTTP_400_BAD_REQUEST)

    updated_link_targets = _reassign_links_to_service_block(block_ids, service_block)

    response = _delete_tree(block, user_id, block_ids=block_ids)

    for target_id in updated_link_targets:
        try:
            target_block = Block.objects.get(id=target_id)
        except Block.DoesNotExist:
            continue
        send_message_block_update.delay(str(target_block.id), get_object_for_block(target_block))

    return response


def _delete_single_block(block, source):
    """Удаление одиночного блока с обновлением родительского элемента"""

    link = BlockLink.objects.filter(target=block, source=source)
    parent_block = block.parent

    with transaction.atomic():
        if parent_block:
            parent_block.remove_child(block)
            send_message_block_update.delay(str(parent_block.id), get_object_for_block(parent_block))
        link.delete()
        block.delete()
    send_message_unsubscribe_user.delay([str(block.id)])
    return Response({'parent': get_object_for_block(parent_block)}, status=status.HTTP_200_OK)


def _delete_tree(block, user_id, block_ids=None):
    """Удаление дерева блоков"""

    if block_ids is None:
        block_ids = _get_deletable_block_ids(block, user_id)

    if not block_ids:
        return Response({'detail': 'Forbidden. Some sub-branches are not available for deletion.'},
                        status=status.HTTP_403_FORBIDDEN)

    block_ids_str = [str(block_id) for block_id in block_ids]

    # Проверка наличия ссылок на удаляемые блоки
    # if BlockLink.objects.filter(source__id__in=block_ids).exists():
    #     return Response({'detail': "You can't delete a tree. It has blocks - which are referenced."},
    #                     status=status.HTTP_400_BAD_REQUEST)

    parent_block = block.parent
    parent_data = {}

    with transaction.atomic():
        if parent_block:
            parent_block.remove_child(block)
            parent_data = get_object_for_block(parent_block)
            send_message_block_update.delay(str(parent_block.id), parent_data)

        # Удаление всех связанных блоков и связей
        Block.objects.filter(id__in=block_ids).delete()
        BlockLink.objects.filter(target__id__in=block_ids).delete()
        send_message_unsubscribe_user.delay(block_ids_str)

    return Response({'parent': parent_data}, status=status.HTTP_200_OK)


def _get_service_block():
    """Возвращает служебный блок, который используется как источник для ссылок."""

    service_block_id = getattr(settings, 'SERVICE_BLOCK_ID', None)
    if not service_block_id:
        raise ValueError('Service block id is not configured.')

    try:
        service_block_uuid = uuid.UUID(str(service_block_id))
    except (ValueError, TypeError) as exc:
        raise ValueError('Service block id has invalid format.') from exc

    try:
        return Block.objects.get(id=service_block_uuid)
    except Block.DoesNotExist as exc:
        raise ValueError('Service block not found.') from exc


def _get_deletable_block_ids(block, user_id):
    with connection.cursor() as cursor:
        cursor.execute(delete_tree_query, {'block_id': block.id, 'user_id': user_id})
        rows = cursor.fetchall()
    return [row[0] for row in rows]


def _reassign_links_to_service_block(block_ids, service_block):
    """Переназначает ссылки, указывающие на удаляемые блоки, на служебный блок."""

    block_ids_str = [str(block_id) for block_id in block_ids]
    links = BlockLink.objects.select_related('target').filter(source_id__in=block_ids_str)
    links = links.exclude(target_id__in=block_ids_str)

    updated_targets = set()

    with transaction.atomic():
        for link in links:
            target_block = link.target
            target_data = target_block.data or {}
            new_source_value = str(service_block.id)
            if target_data.get('source') != new_source_value:
                target_data['source'] = new_source_value
                target_block.data = target_data
                target_block.save(update_fields=['data'])
                updated_targets.add(target_block.id)

            if link.source_id != service_block.id:
                link.source = service_block
                link.save(update_fields=['source'])
                updated_targets.add(target_block.id)

    return [str(target_id) for target_id in updated_targets]