from uuid import UUID

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import IntegrityError, transaction, connection
from .models import Block, BlockPermission, BlockLink
from .serializers import get_object_for_block
from api.tasks import send_message_block_update
from .utils.query import restore_deleted_branch, delete_tree_query
from .view_delete_tree import _delete_tree


class BlockHistoryListView(APIView):
    """
    Предоставляет список всех исторических записей для конкретного блока.
    GET /api/blocks/<uuid>/history/
    """

    def get(self, request, block_id):
        block = get_object_or_404(Block, id=block_id)
        history = block.history.all().order_by('-history_date')
        history_list = []
        for record in history:
            history_list.append({
                "history_id": record.history_id,
                "history_date": record.history_date,
                "changed_by": str(record.history_user) if record.history_user else None,
                "history_type": record.get_history_type_display(),
                "title": record.title,
                "data": record.data,
            })
        return Response(history_list, status=status.HTTP_200_OK)


class BlockHistoryUndoView(APIView):
    """
    View для отката (undo) операций с блоками:
        - new-tree
        - edit-block
        - new-block
        - create-link-block
        - copy-block
        - move-block
        - delete-tree
    """

    EDIT_PERMS = ['edit', 'edit_ac', 'delete']
    FORCE_PERMS = ['delete']

    def post(self, request, ):
        operation = request.data.get('operation')
        force = request.data.get('force', False)
        user = request.user

        if not operation or not isinstance(operation, dict):
            return Response({'detail': 'Invalid operation data'}, status=status.HTTP_400_BAD_REQUEST)

        url_path = operation.get('url', '')
        if url_path.startswith('edit-block'):
            return self._undo_edit_block(user, operation, force)
        elif url_path.startswith('new-tree'):
            return self._undo_new_tree(user, operation, force)
        elif url_path.startswith('new-block'):
            return self._undo_new_block(user, operation, force)
        elif url_path.startswith('create-link-block'):
            return self._undo_create_link_block(user, operation, force)
        elif url_path.startswith('copy-block'):
            return self._undo_copy_block(user, operation, force)
        elif url_path.startswith('move-block'):
            return self._undo_move_block(user, operation, force)
        elif url_path.startswith('delete-tree'):
            return self._undo_delete_block(user, operation, force)

        # Если операция нам не знакома:
        return Response([], status=status.HTTP_200_OK)

    def _undo_new_tree(self, user, operation, force):
        """
        Отмена создания нового дерева (удаляем созданный root-блок вместе с историей).
        """
        block_id = operation['responseData']['id']
        block = get_object_or_404(Block, id=block_id)

        # Проверяем права (если нет — запрещаем доступ).
        if not self._has_user_permissions_for_blocks([block], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            block.history.all().delete()
            block.delete()

        return Response({'removed': [block_id]}, status=status.HTTP_200_OK)

    def _undo_edit_block(self, user, operation, force):
        """
        Отмена редактирования блока (откатываемся на предыдущую запись в истории).
        """
        # URL вида: edit-block/<uuid:block_id>/
        try:
            block_id = UUID(operation['url'].split('/')[1])
        except (IndexError, ValueError):
            return Response({'detail': 'Invalid block ID'}, status=status.HTTP_400_BAD_REQUEST)

        block = get_object_or_404(Block, id=block_id)

        if not self._has_user_permissions_for_blocks([block], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # Берём исторические записи (последняя сверху):
        history_entries = block.history.all().order_by('-history_date')
        if history_entries.count() < 2:
            return Response({'detail': 'No previous history entry found.'},
                            status=status.HTTP_409_CONFLICT)

        # Предпоследняя запись, на которую откатываемся:
        previous_record = history_entries[1]

        with transaction.atomic():
            # Проверяем, что верхние две записи истории принадлежат тому же пользователю:
            if history_entries[0].history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user."
                }, status=status.HTTP_409_CONFLICT)

            # Откат данных:
            Block.objects.filter(id=block_id).update(
                title=previous_record.title,
                data=previous_record.data,
            )
            # Удаляем последнюю (актуальную) запись, чтобы зафиксировать откат
            history_entries.first().delete()

        # Отправляем асинхронное сообщение об изменении (зависит от логики проекта).
        updated_block = Block.objects.get(id=block_id)
        send_message_block_update.delay(updated_block.id, get_object_for_block(updated_block))

        return Response({'blocks': [get_object_for_block(updated_block)]},
                        status=status.HTTP_200_OK)

    def _undo_new_block(self, user, operation, force):
        """
        Отмена создания нового блока (удаляем созданный блок + вычищаем последние истории у родителя).
        """
        response_data = operation['responseData']
        try:
            new_block_id = response_data[0]['id']
            parent_id = response_data[1]['id']
        except (IndexError, KeyError):
            return Response({'detail': 'Invalid responseData structure'},
                            status=status.HTTP_400_BAD_REQUEST)

        new_block = get_object_or_404(Block, pk=new_block_id)
        parent = get_object_or_404(Block, pk=parent_id)

        if not self._has_user_permissions_for_blocks([parent, new_block], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            # Проверяем, что последняя запись истории у родителя — от того же пользователя
            if parent.history.latest().history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user."
                }, status=status.HTTP_409_CONFLICT)

            parent.remove_child(new_block)

            # Удаляем две последние записи истории у родителя:
            ids_to_delete = list(parent.history.order_by('-history_date')
                                 .values_list('history_id', flat=True)[:2])
            parent.history.filter(history_id__in=ids_to_delete).delete()

            new_block.delete()

        send_message_block_update.delay(parent.id, get_object_for_block(parent))
        return Response({
            'blocks': [get_object_for_block(parent)],
            'removed': [new_block_id]
        }, status=status.HTTP_200_OK)

    def _undo_create_link_block(self, user, operation, force):
        """
        Отмена создания "ссылочного" блока (link-block).
        Удаляем BlockLink, удаляем сам "link" и чистим историю у родителя.
        """
        resp_data = operation.get('responseData', [])
        if len(resp_data) < 3:
            return Response({'detail': 'Invalid responseData structure'},
                            status=status.HTTP_400_BAD_REQUEST)

        parent_id = resp_data[0].get('id')
        source_id = resp_data[1].get('id')
        link_id = resp_data[2].get('id')
        if not (parent_id and source_id and link_id):
            return Response({'detail': 'Missing IDs for parent/source/link'},
                            status=status.HTTP_400_BAD_REQUEST)

        blocks = Block.objects.filter(id__in=[parent_id, source_id, link_id])
        blocks_map = {str(b.id): b for b in blocks}

        if len(blocks_map) != 3:
            return Response({'detail': 'Not all blocks were found'},
                            status=status.HTTP_404_NOT_FOUND)

        if not self._has_user_permissions_for_blocks(list(blocks_map.values()), user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            link = blocks_map[link_id]
            parent = blocks_map[parent_id]
            source = blocks_map[source_id]

            if parent.history.latest().history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user."
                }, status=status.HTTP_409_CONFLICT)

            # Удаляем связь target=parent, source=source (BlockLink)
            BlockLink.objects.filter(target=parent, source=source).delete()

            # Удаляем дочерний "link" из parent
            parent.remove_child(link)
            link.delete()

            # Чистим историю у родителя
            ids_to_delete = list(parent.history.order_by('-history_date')
                                 .values_list('history_id', flat=True)[:2])
            parent.history.filter(history_id__in=ids_to_delete).delete()

        send_message_block_update.delay(parent.id, get_object_for_block(parent))
        return Response({
            'blocks': [get_object_for_block(parent)],
            'removed': [link_id]
        }, status=status.HTTP_200_OK)

    def _undo_delete_block(self, user, operation, force):
        """
        Отмена удаления ветки. Восстанавливает дерево с помощью SQL-запроса,
        затем возвращает его в родителя.
        """
        try:
            deleted_block_id = operation['url'].split('/')[1]  # delete-tree/<uuid:tree_id>/
        except IndexError:
            return Response({'detail': 'Invalid operation URL'}, status=status.HTTP_400_BAD_REQUEST)

        parent_data = operation.get('responseData', {}).get('parent', {})
        parent_id = parent_data.get('id')
        if not parent_id:
            return Response({'detail': 'Invalid parent data'}, status=status.HTTP_400_BAD_REQUEST)

        parent = get_object_or_404(Block, id=parent_id)

        # Для восстановления удалённого блока нужна "delete"-разрешение (force=True).
        if not self._has_user_permissions_for_blocks([parent], user, force=True):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # Проверка пользователя в истории родителя
        if parent.history.latest().history_user_id != user.id:
            return Response({
                "detail": "You are trying to revert changes made by another user."
            }, status=status.HTTP_409_CONFLICT)

        # Выполняем SQL для восстановления
        with connection.cursor() as cursor:
            cursor.execute(restore_deleted_branch, {
                'block_id': deleted_block_id,
                'parent_block_id': parent_id
            })

        # Добавляем восстановленный блок обратно в родителя
        block = Block.objects.get(id=deleted_block_id)
        parent.add_child(block)

        # Отправляем сообщение о том, что родитель изменился
        send_message_block_update.delay(parent_id, get_object_for_block(parent))

        # Чистим историю
        ids_to_delete = list(parent.history.order_by('-history_date')
                             .values_list('history_id', flat=True)[:2])
        parent.history.filter(history_id__in=ids_to_delete).delete()

        return Response({'blocks': [get_object_for_block(parent)]}, status=status.HTTP_200_OK)

    def _undo_copy_block(self, user, operation, force):
        """
        Отмена копирования блока: удаляем скопированный блок + его дочерние элементы.
        """
        parent_id = operation['data'].get('dest')
        copy_id = operation.get('copyId')
        if not parent_id or not copy_id:
            return Response({'detail': 'Invalid data for copy-block undo'},
                            status=status.HTTP_400_BAD_REQUEST)

        parent = get_object_or_404(Block, id=parent_id)
        copy = get_object_or_404(Block, id=copy_id)

        if not self._has_user_permissions_for_blocks([parent, copy], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # Проверяем, что последняя запись истории у родителя — от того же пользователя
        if parent.history.latest().history_user_id != user.id:
            return Response({
                "detail": "You are trying to revert changes made by another user."
            }, status=status.HTTP_409_CONFLICT)

        # SQL, который удаляет ветку (copy) целиком
        with connection.cursor() as cursor:
            cursor.execute(delete_tree_query, {
                'block_id': copy.id,
                'user_id': user.id
            })
            rows = cursor.fetchall()
        block_ids = [row[0] for row in rows]

        with transaction.atomic():
            parent.remove_child(copy)
            Block.objects.filter(id__in=block_ids).delete()
            BlockLink.objects.filter(target__id__in=block_ids).delete()

        send_message_block_update.delay(parent_id, get_object_for_block(parent))

        # Чистим историю родителя
        ids_to_delete = list(parent.history.order_by('-history_date')
                             .values_list('history_id', flat=True)[:2])
        parent.history.filter(history_id__in=ids_to_delete).delete()

        return Response({
            'blocks': [get_object_for_block(parent)],
            'removed': [copy_id]
        }, status=status.HTTP_200_OK)

    def _undo_move_block(self, user, operation, force):
        """
        Отмена перемещения блока. URL: move-block/<uuid:old_parent_id>/<uuid:new_parent_id>/<uuid:child_id>/

        Если old_parent == new_parent, достаточно просто откатить историю одного родителя.
        Иначе возвращаем ребёнка в старого родителя и восстанавливаем данные родителя и нового родителя
        из их предыдущих исторических записей. После чего удаляем "лишние" записи (последние две).
        """
        try:
            # Пример URL: move-block/old_parent_id/new_parent_id/child_id/
            _, old_parent_id, new_parent_id, child_id, _ = operation['url'].split('/')
        except ValueError:
            return Response({'detail': 'Invalid move-block URL'}, status=status.HTTP_400_BAD_REQUEST)

        old_parent = get_object_or_404(Block, id=old_parent_id)
        new_parent = get_object_or_404(Block, id=new_parent_id)
        child = get_object_or_404(Block, id=child_id)

        # Проверка прав доступа
        if not self._has_user_permissions_for_blocks([old_parent, new_parent, child], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # ------------------------------------
        # Вспомогательная функция, которая:
        # 1. Проверяет, что последняя запись истории - от нужного пользователя
        # 2. Смотрит, что записей >= 2
        # 3. Откатывает объект (текущий) к предпоследней записи
        # 4. Удаляет последнюю запись
        # ------------------------------------
        def revert_block_to_previous(block):
            history_qs = block.history.order_by('-history_date')
            if history_qs.count() < 2 or history_qs[0].history_user_id != user.id:
                return False  # Сигнализируем, что откат невозможен

            # Берём предпоследнюю запись
            previous_record = history_qs[1]

            # Применяем её поля к модели
            block.title = previous_record.title
            block.data = previous_record.data
            block.save()

            # Удаляем последнюю запись (актуальную)
            history_qs.first().delete()

            # Убирвем запись об отмене
            block.history.latest().delete()

            return True

        # ------------------------------------
        # Если old_parent_id == new_parent_id, просто откатим историю одного родителя
        # ------------------------------------
        if new_parent_id == old_parent_id:
            with transaction.atomic():
                success = revert_block_to_previous(old_parent)
                if not success:
                    return Response({
                        "detail": "No previous history for old_parent to revert or user mismatch."
                    }, status=status.HTTP_409_CONFLICT)

            send_message_block_update.delay(old_parent.id, get_object_for_block(old_parent))
            return Response({'blocks': [get_object_for_block(old_parent)], 'removed': []},
                            status=status.HTTP_200_OK)

        # ------------------------------------
        # Иначе: полноценное откатывание перемещения
        # ------------------------------------
        with transaction.atomic():
            # Откатываем состояние new_parent
            success = revert_block_to_previous(new_parent)
            # Удаляем ребёнка из new_parent
            new_parent.children.remove(child)
            if not success:
                return Response({
                    "detail": "No previous history for new_parent to revert or user mismatch."
                }, status=status.HTTP_409_CONFLICT)

            # Откат old_parent
            success = revert_block_to_previous(old_parent)
            if not success:
                return Response({
                    "detail": "No previous history for old_parent to revert or user mismatch."
                }, status=status.HTTP_409_CONFLICT)

            # Возвращаем ребёнка в old_parent
            old_parent.children.add(child)

        # Рассылаем обновлённые данные
        send_message_block_update.delay(old_parent.id, get_object_for_block(old_parent))
        send_message_block_update.delay(new_parent.id, get_object_for_block(new_parent))

        return Response({
            'blocks': [
                get_object_for_block(old_parent),
                get_object_for_block(new_parent),
            ],
            'removed': []
        }, status=status.HTTP_200_OK)

    def _has_user_permissions_for_blocks(self, blocks, user, force=False):
        """
        Проверяет, что у пользователя есть нужные права (EDIT_PERMS или FORCE_PERMS) на ВСЕ блоки в списке blocks.
        Возвращает True, если у пользователя действительно есть права, иначе False.
        """
        if not blocks:
            return False

        # Если передали 1 блок в виде объекта, превращаем в список
        if isinstance(blocks, Block):
            blocks = [blocks]

        required_perms = self.FORCE_PERMS if force else self.EDIT_PERMS
        required_block_ids = {b.id for b in blocks}

        # Все block_id, для которых у пользователя есть хотя бы один из нужных perms
        block_ids_with_perms = set(
            BlockPermission.objects.filter(
                block__in=blocks,
                user=user,
                permission__in=required_perms
            )
            .values_list('block_id', flat=True)
            .distinct()
        )

        # Нужно, чтобы пользователь имел права на каждый блок из списка
        return required_block_ids.issubset(block_ids_with_perms)
