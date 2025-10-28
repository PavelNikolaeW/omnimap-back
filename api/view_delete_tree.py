import uuid
from django.conf import settings
from django.db import transaction, connection
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from api.models import Block, BlockLink, BlockPermission
from api.utils.decorators import check_block_permissions
from api.serializers import get_object_for_block
from api.utils.query import delete_tree_query
from api.tasks import send_message_block_update, send_message_unsubscribe_user


# ---------- force_delete_tree ----------
def get_all_descendant_ids(block_id):
    """
    Возвращает список всех дочерних id (включая всех потомков на любом уровне)
    для блока с указанным block_id.
    Использует рекурсивный CTE для максимальной производительности.
    """
    with connection.cursor() as cursor:
        cursor.execute("""
            WITH RECURSIVE descendants AS (
                SELECT id
                FROM api_block
                WHERE parent_id = %s
                UNION ALL
                SELECT b.id
                FROM api_block b
                INNER JOIN descendants d ON b.parent_id = d.id
            )
            SELECT id FROM descendants;
        """, [str(block_id)])
        rows = cursor.fetchall()
    rez = [block_id]
    rez.extend([row[0] for row in rows])
    return rez


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
@check_block_permissions({'tree_id': ['delete']})
def delete_tree(request, tree_id):
    """
    Удаляет блок/дерево
    """
    # 1) Берём корень с parent
    root_block = get_object_or_404(
        Block.objects.select_related("parent").only("id", "parent_id", "data"),
        id=tree_id,
    )

    # 2) Получаем ids поддерева
    ids_to_delete = get_all_descendant_ids(tree_id)
    if not ids_to_delete:
        return Response(status=status.HTTP_200_OK)

    # 3) Список target'ов, на которые указывают удаляемые source'ы
    target_ids_to_mark = list(
        BlockLink.objects
        .filter(source_id__in=ids_to_delete)
        .values_list("target_id", flat=True)
        .distinct()
    )

    # 4) Транзакция: bulk-update, правка родителя, удаление ссылок и блоков
    with transaction.atomic():
        # 4.1) Массовая пометка целевых блоков
        if target_ids_to_mark:
            Block.objects.filter(id__in=target_ids_to_mark).update(
                title='The resource of this link has been deleted.',
                data={"color": [0, 65, 47, 0]},
            )

        # 4.2) Подготовим данные для рассылки событий по обновлённым target'ам
        # (отдельным запросом заберём только нужные поля)
        updated_targets = []
        if target_ids_to_mark:
            updated_targets = list(
                Block.objects
                .only("id", "title", "data", "parent_id", "updated_at")
                .filter(id__in=target_ids_to_mark)
            )

        # 4.3) Если у корня есть родитель — удалим связь «родитель→ребёнок»
        parent = root_block.parent
        if parent:
            # remove_child, вероятно, правит parent.data.childOrder и сохраняет
            parent.remove_child(root_block)
            updated_targets.append(parent)

        # 4.4) Удаляем все ссылки
        # входящие в удаляемые target'ы + исходящие из удаляемых source'ов
        BlockLink.objects.filter(Q(target_id__in=ids_to_delete) | Q(source_id__in=ids_to_delete)).delete()

        # 4.5) Удаляем сами блоки поддерева
        Block.objects.filter(id__in=ids_to_delete).delete()

    # 5) Асинхронные события — после фиксации транзакции (но объекты у нас уже на руках)
    for block in updated_targets:
        send_message_block_update.delay(str(block.id), get_object_for_block(block))
    send_message_unsubscribe_user.delay([str(block.id) for block in updated_targets])
    return Response(
        {
            "parent": get_object_for_block(parent) if parent else {},
            "deleted": [str(x) for x in ids_to_delete],
        },
        status=status.HTTP_200_OK,
    )
