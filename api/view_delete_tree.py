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


def _delete_tree(block, user_id):
    """Удаление дерева блоков через SQL-запрос"""

    with connection.cursor() as cursor:
        cursor.execute(delete_tree_query, {'block_id': block.id, 'user_id': user_id})
        rows = cursor.fetchall()

    block_ids = [str(row[0]) for row in rows]

    if not block_ids:
        return Response({'detail': 'Forbidden. Some sub-branches are not available for deletion.'},
                        status=status.HTTP_403_FORBIDDEN)

    # Проверка наличия ссылок на удаляемые блоки
    if BlockLink.objects.filter(source__id__in=block_ids).exists():
        return Response({'detail': "You can't delete a tree. It has blocks - which are referenced."},
                        status=status.HTTP_400_BAD_REQUEST)

    parent_block = block.parent
    parent_data = {}

    with transaction.atomic():
        if parent_block:
            parent_block.remove_child(block)
            parent_data = get_object_for_block(parent_block)
            send_message_block_update.delay(str(parent_block.id), parent_data)

        # Удаление всех связанных блоков и связей
        send_message_unsubscribe_user.delay(block_ids)
        Block.objects.filter(id__in=block_ids).delete()
        BlockLink.objects.filter(target__id__in=block_ids).delete()

    return Response({'parent': parent_data}, status=status.HTTP_200_OK)