from uuid import UUID

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db import IntegrityError, transaction, connection
from .models import Block, BlockPermission, BlockLink
from .serializers import get_object_for_block
from api.tasks import send_message_block_update
from .utils.query import restore_deleted_branch


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
    path('new-block/<uuid:parent_id>/', create_block, name='new-block'),
    path('new-tree/', create_new_tree, name='new-tree'),
    path('create-link-block/<uuid:parent_id>/<uuid:source_id>/', create_link_on_block, name='create-link-block'),
    path('move-block/<uuid:old_parent_id>/<uuid:new_parent_id>/<uuid:child_id>/', move_block, name='move-block'),
    path('copy-block/', CopyBlockView.as_view(), name='copy-block'),
    path('edit-block/<uuid:block_id>/', edit_block, name='edit-block'),
    """

    EDIT_PERMS = ['edit', 'edit_ac', 'delete']
    FORCE_PERMS = ['delete']

    def post(self, request, ):
        # print(request.data.get('operation'))
        operation = request.data.get('operation')
        force = request.data.get('force', False)
        user = request.user
        """{'url': 'new-tree/',
                'data': {'title': 'lll'},
                'isFail': False,
                'responseData': {'id': '0fc8a84b-1f57-4560-996a-bca9e6d13110', 'title': 'lll', 'data': {}, 'updated_at': '2025-03-22T14:41:32.209594Z', 'parent_id': 'None', 'children': []}}
                """

        if operation['url'].startswith('edit-block'):
            """
            {'url': 'edit-block/61b916d4-5471-4e69-b33c-66e579be6b67/', 
            'data': {'data': {'text': '<p>wwwww<br><br></p>\n'}}, 
            'isFail': False, 
            'responseData': {'id': '61b916d4-5471-4e69-b33c-66e579be6b67', 'title': 'kek', 'data': {'text': '<p>wwwww<br><br></p>\n', 'childOrder': []}, 'updated_at': '2025-03-22T13:16:24.324643Z', 'parent_id': '81ae4540-99a7-4cfb-b261-f0ea886d1610', 'children': []}}
            """
            # print(operation)
            return self._undo_edit_block(user, operation, force)
        elif operation['url'].startswith('new-tree'):
            return self._undo_new_tree(user, operation, force)
        elif operation['url'].startswith('new-block'):
            return self._undo_new_block(user, operation, force)
        elif operation['url'].startswith('create-link-block'):
            return self._undo_create_link_block(user, operation, force)
        elif operation['url'].startswith('copy-block'):
            return self._undo_copy_block(user, operation, force)
        elif operation['url'].startswith('move-block'):
            return self._undo_move_block(user, operation, force)
        elif operation['url'].startswith('delete-tree'):
            return self._undo_delete_block(user, operation, force)

        return Response([], status=status.HTTP_200_OK)

    def _undo_new_tree(self, user, operation, force):
        block_id = operation['responseData']['id']
        block = get_object_or_404(Block, id=block_id)

        if self._check_user_permissions(block, user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            block.history.all().delete()
            block.delete()
        return Response({'removed': [block_id]}, status=status.HTTP_200_OK)

    def _undo_edit_block(self, user, operation, force):
        block_id = UUID(operation['url'].split('/')[1])
        block = get_object_or_404(Block, id=block_id)

        if not self._check_user_permissions([block], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        # Берём исторические записи, отсортированные по времени (последняя сверху):
        history_entries = block.history.all().order_by('-history_date')

        # Убедимся, что записей минимум 2:
        if history_entries.count() < 2:
            raise ValueError("Нет предыдущих записей для отката.")

        # Получаем предпоследнюю запись (на которую нужно откатиться):
        previous_record = history_entries[1]

        # Вручную обновляем объект данными из предпоследней записи, минуя simple-history:
        with transaction.atomic():
            if history_entries[0].history_user_id != user.id or history_entries[1].history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user.",
                }, status=status.HTTP_409_CONFLICT)

            Block.objects.filter(id=block_id).update(
                title=previous_record.title,
                data=previous_record.data,
            )
            history_entries.first().delete()
        block = Block.objects.get(id=block_id)
        send_message_block_update.delay(block.id, get_object_for_block(block))
        return Response({'blocks': [get_object_for_block(block)]}, status=status.HTTP_200_OK)

    def _undo_new_block(self, user, operation, force):
        response_data = operation['responseData']
        new_block_id = response_data[0]['id']
        parent_id = response_data[1]['id']

        new_block = get_object_or_404(Block, pk=new_block_id)
        parent = get_object_or_404(Block, pk=parent_id)
        if not self._check_user_permissions([parent, new_block], user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            if parent.history.latest().history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user.",
                }, status=status.HTTP_409_CONFLICT)
            parent.remove_child(new_block)

            ids_to_delete = list(parent.history.order_by('-history_date').values_list('history_id', flat=True)[:2])
            parent.history.filter(history_id__in=ids_to_delete).delete()
            new_block.delete()
        send_message_block_update.delay(parent.id, get_object_for_block(parent))
        return Response({'blocks': [get_object_for_block(parent)], 'removed': [new_block_id]},
                        status=status.HTTP_200_OK)

    def _undo_create_link_block(self, user, operation, force):
        resp_data = operation['responseData']
        parent_id = resp_data[0]['id']
        source_id = resp_data[1]['id']
        link_id = resp_data[2]['id']
        blocks = {str(block.id): block for block in list(Block.objects.filter(id__in=[parent_id, source_id, link_id]))}

        if len(blocks) != 3:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        if not self._check_user_permissions(list(blocks.values()), user, force):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            link = blocks[link_id]
            parent = blocks[parent_id]
            source = blocks[source_id]
            history_entries = parent.history.all().order_by('-history_date')
            if history_entries[0].history_user_id != user.id:
                return Response({
                    "detail": "You are trying to revert changes made by another user.",
                }, status=status.HTTP_409_CONFLICT)

            BlockLink.objects.filter(target=parent, source=source).delete()
            parent.remove_child(link)
            link.delete()
            ids_to_delete = list(parent.history.order_by('-history_date').values_list('history_id', flat=True)[:2])
            parent.history.filter(history_id__in=ids_to_delete).delete()

        send_message_block_update.delay(parent.id, get_object_for_block(parent))
        return Response({'blocks': [get_object_for_block(parent)], 'removed': [link_id]}, status=status.HTTP_200_OK)

    def _undo_delete_block(self, user, operation, force):
        deleted_block_id = operation['url'].split('/')[1]
        parent_id = operation['responseData']['parent']['id']
        parent = get_object_or_404(Block, id=parent_id)

        if not self._check_user_permissions([parent], user, True):
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        with connection.cursor() as cursor:
            cursor.execute(restore_deleted_branch, {'block_id': deleted_block_id, 'parent_block_id': parent_id})
        block = Block.objects.get(id=deleted_block_id)
        parent.add_child(block)
        send_message_block_update.delay(parent_id, get_object_for_block(parent))
        return Response({'blocks': [get_object_for_block(parent)]}, status=status.HTTP_200_OK)

    def _undo_copy_block(self, user, operation, force):
        pass

    def _undo_move_block(self, user, operation, force):
        pass

    def _check_user_permissions(self, blocks, user, force=False):
        perms = self.EDIT_PERMS if not force else self.FORCE_PERMS

        # Получаем разрешения одним запросом
        perms = BlockPermission.objects.filter(
            block__in=blocks,
            user=user,
            permission__in=perms
        ).values_list('block_id', flat=True)
        print(perms)
        print(len(perms) == len(blocks))

        return len(perms) == len(blocks)
