"""Набор REST-эндпоинтов для работы с блоками, деревьями и правами доступа."""

import datetime
import json
import logging
import uuid
from collections import defaultdict, namedtuple
from itertools import chain
from pprint import pprint

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.db.models.expressions import RawSQL
from django.shortcuts import get_object_or_404
from psycopg2._json import Json
from psycopg2.extras import execute_values
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.pagination import PageNumberPagination
from rest_framework.decorators import api_view, permission_classes
from django.utils.timezone import now
from django.conf import settings
from .models import Block, BlockPermission, BlockLink, ALLOWED_SHOW_PERMISSIONS, PERMISSION_CHOICES, \
    Group
from .serializers import (RegisterSerializer,
                          CustomTokenObtainPairSerializer, BlockSerializer, get_object_for_block, get_forest_serializer,
                          load_empty_block_serializer, access_serializer)
from api.utils.query import get_all_trees_query, \
    load_empty_blocks_query
from .tasks import send_message_block_update, send_message_subscribe_user, \
    set_block_group_permissions_task, set_block_permissions_task, import_blocks_task, \
    notify_block_change
from .utils.decorators import subscribe_to_blocks, determine_user_id, check_block_permissions
from celery.result import AsyncResult

PermissionData = namedtuple('PermissionData', ['user', 'permission'])

User = get_user_model()

logger = logging.getLogger(__name__)


class TaskStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        """
        Получает статус задачи Celery по task_id.
        Проверяет, что задача принадлежит текущему пользователю через Redis.

        Возвращает:
        - task_id: ID задачи
        - status: PENDING | PROGRESS | SUCCESS | FAILURE | RETRY
        - progress: информация о прогрессе (для PROGRESS)
        - result: результат выполнения (для SUCCESS и FAILURE)
        """
        from api.utils.task_utils import get_task_owner

        # Проверяем владельца задачи
        task_owner_id = get_task_owner(task_id)

        if task_owner_id is not None and task_owner_id != request.user.id:
            return Response(
                {'detail': 'You do not have permission to view this task'},
                status=status.HTTP_403_FORBIDDEN
            )

        task_result = AsyncResult(task_id)
        task_status = task_result.status

        response_data = {
            'task_id': task_id,
            'status': task_status,
        }

        # Обрабатываем разные статусы
        if task_status == 'PROGRESS':
            # Задача выполняется — возвращаем информацию о прогрессе
            meta = task_result.info or {}
            response_data['progress'] = {
                'stage': meta.get('stage', 'unknown'),
                'percent': meta.get('progress', 0),
                'total_blocks': meta.get('total_blocks'),
            }
            response_data['result'] = None

        elif task_status == 'SUCCESS':
            # Задача завершена успешно — возвращаем результат
            response_data['result'] = task_result.result

        elif task_status == 'FAILURE':
            # Задача завершилась с ошибкой
            error_info = task_result.info
            if isinstance(error_info, Exception):
                response_data['result'] = {
                    'success': False,
                    'error': str(error_info),
                    'error_type': type(error_info).__name__
                }
            else:
                response_data['result'] = error_info

        elif task_status == 'RETRY':
            # Задача будет повторена
            response_data['result'] = None
            response_data['message'] = 'Task is being retried'

        elif task_status == 'PENDING':
            # Задача в очереди или не существует
            response_data['result'] = None

        else:
            # Неизвестный статус (например, custom states)
            response_data['result'] = task_result.info

        return Response(response_data)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 200  # Количество объектов на страницу
    page_size_query_param = 'page_size'
    max_page_size = 200


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class BlockSearchAPIView(generics.ListAPIView):
    serializer_class = BlockSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    ordering = ['-updated_at', 'id']
    ordering_fields = ['updated_at']

    # Максимальная глубина рекурсии для поиска в поддереве
    MAX_SUBTREE_DEPTH = 50

    def _get_subtree_ids(self, root_id):
        """
        Возвращает список UUID всех блоков в поддереве с корнем root_id (включая сам root_id),
        учитывая обычные связи и линковые блоки без использования EXISTS.
        Ограничено MAX_SUBTREE_DEPTH уровнями для защиты от DoS.
        """
        with connection.cursor() as cursor:
            cursor.execute("""
                WITH RECURSIVE subtree AS (
                    -- базовый случай: берем id, data и глубину
                    SELECT id, data, 1 AS depth
                      FROM api_block
                     WHERE id = %s

                    UNION ALL

                    -- рекурсивный случай: берем id, data и увеличиваем глубину
                    SELECT b.id, b.data, s.depth + 1
                      FROM api_block b
                      JOIN subtree s
                        ON b.parent_id = s.id
                        OR (
                            s.data->>'view' = 'link'
                            AND (s.data->>'source')::uuid = b.id
                        )
                     WHERE s.depth < %s
                )
                SELECT DISTINCT id FROM subtree;
            """, [str(root_id), self.MAX_SUBTREE_DEPTH])
            return [row[0] for row in cursor.fetchall()]

    def get_queryset(self):
        user = self.request.user
        query = self.request.GET.get('q', '').strip()
        root = self.request.GET.get('root')
        everywhere = self.request.GET.get('everywhere', 'false').lower() == 'true'

        # 1) Блоки, к которым у пользователя есть нужные права
        perm_filter = Q(permissions__user=user,
                        permissions__permission__in=ALLOWED_SHOW_PERMISSIONS)
        qs = Block.objects.filter(perm_filter).distinct()

        # 2) Ограничение поддеревом, если нужно
        if not everywhere and root:
            # проверим, что root существует — чтобы сразу 404, а не пустой список
            get_object_or_404(Block, pk=root)
            subtree_ids = self._get_subtree_ids(root)
            qs = qs.filter(id__in=subtree_ids)

        # 3) Накладываем поиск по тексту и заголовку
        if query:
            qs = qs.filter(
                Q(title__icontains=query) |
                Q(data__text__icontains=query)
            )

        return qs


class RegisterView(APIView):
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            new_block = Block.objects.create(
                creator=user,
                title=user.username,
                data={"color": "default_color"}
            )
            block_p = BlockPermission.objects.create(user=user, block=new_block, permission='delete')
            new_block.save()
            block_p.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'message': 'User created successfully with tokens',
                'user_id': user.id,
                'block_id': new_block.id
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AccessBlockView(APIView):
    """Управляет правами доступа к блоку для отдельных пользователей и групп."""

    permission_classes = (IsAuthenticated,)

    def get(self, request, block_id):
        """Возвращает все права доступа, выданные для указанного блока."""

        block = get_object_or_404(Block, pk=block_id)

        # Проверяем, что пользователь имеет право на управление доступом
        if not BlockPermission.objects.filter(
            block=block,
            user=request.user,
            permission__in=('edit_ac', 'delete')
        ).exists():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        block_permissions = BlockPermission.objects.filter(block__id=block_id).select_related('user')
        if not block_permissions.exists():
            return Response({'error': 'Block does not exist'}, status=status.HTTP_404_NOT_FOUND)
        return Response(access_serializer(block_permissions))

    def post(self, request, block_id):
        """Запускает Celery-задачу по изменению прав доступа пользователя или группы."""

        initiator = request.user
        permission_type = request.data.get('permission_type')
        target_username = request.data.get('target_username')
        group_name = request.data.get('group_name')

        if not permission_type or (not target_username and not group_name):
            return Response(
                {"detail": "Please provide permission_type and either target_username or group_name in request data."},
                status=status.HTTP_400_BAD_REQUEST
            )

        valid_permissions = [choice[0] for choice in PERMISSION_CHOICES]
        if permission_type not in valid_permissions:
            return Response(
                {"detail": f"Invalid permission '{permission_type}'. Must be one of: {valid_permissions}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        block = get_object_or_404(Block, id=block_id)

        # Проверка, что инициатор имеет право на изменение стартового блока
        if not BlockPermission.objects.filter(block=block, user=initiator,
                                              permission__in=('edit_ac', 'delete')).exists():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        from api.utils.task_utils import save_task_owner

        if target_username:
            target_user = get_object_or_404(User, username=target_username)
            task = set_block_permissions_task.delay(
                initiator_id=initiator.id,
                target_user_id=target_user.id,
                block_id=block.id,
                new_permission=permission_type,
            )
            detail_msg = "Permission update task has been started for user."
        else:
            group = get_object_or_404(Group, name=group_name, owner=initiator)
            task = set_block_group_permissions_task.delay(
                initiator_id=initiator.id,
                group_id=group.id,
                block_id=block.id,
                new_permission=permission_type,
            )
            detail_msg = "Permission update task has been started for group."

        # Сохраняем владельца задачи для проверки прав доступа
        save_task_owner(task.id, initiator.id)

        return Response(
            {"task_id": task.id, "detail": detail_msg},
            status=status.HTTP_202_ACCEPTED
        )


@api_view(["GET"])
@determine_user_id
@subscribe_to_blocks(send_message_subscribe_user)
def load_trees(request, user_id):
    """
    Возвращает все корневые блоки (parent IS NULL) для текущего пользователя
    и все их потомки, но суммарно ограничивает кол-во строк LIMIT.
    Формат ответа:
    {
      "<root_uuid>": {
        "<block_uuid>": {
          "id": "<block_uuid>",
          "title": "...",
          "data": {...},
          "updated_at": "2024-12-28T17:00:00.123456Z",
          "children": ["<child_block_uuid1>", "<child_block_uuid2>", ...]
        },
        ...
      },
      "<root_uuid2>": { ... },
      ...
    }
    """
    with connection.cursor() as cursor:
        cursor.execute(get_all_trees_query, {"user_id": user_id, 'creator_id': user_id})
        rows = cursor.fetchall()

    if not rows:
        return Response({"detail": "No blocks found for this user."}, status=404)
    return Response(get_forest_serializer(rows))


@api_view(['POST'])
@determine_user_id
@subscribe_to_blocks(send_message_subscribe_user)
def load_empty_blocks(request, user_id):
    """
    Эндпоинт для получения доступных блоков.
    Тело запроса должно содержать JSON с ключом 'block_ids', например:
    {
        "block_ids": ["uuid1", "uuid2", ...]
    }
    Возвращает JSON с плоским словарём блоков, на которые у пользователя есть право (view/edit).
    """

    # Получаем список block_ids из тела запроса
    block_ids = request.data.get('block_ids', [])
    if not block_ids:
        return Response({"detail": "No block_ids specified"}, status=400)

    # Проверяем и преобразуем block_ids в UUID
    try:
        block_ids = [uuid.UUID(bid) for bid in block_ids]
    except (ValueError, TypeError):
        return Response({"detail": "Invalid block_id format"}, status=400)
    with connection.cursor() as cursor:
        cursor.execute(load_empty_blocks_query, {
            'user_id': user_id,
            'block_ids': block_ids,
            'ALLOWED_PERMISSIONS': ALLOWED_SHOW_PERMISSIONS,
            'max_depth': settings.MAX_DEPTH_LOAD,
        })
        rows = cursor.fetchall()
    if rows:
        return Response(load_empty_block_serializer(rows, settings.MAX_DEPTH_LOAD))
    return Response({"detail": "No blocks found for this user."}, status=404)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'parent_id': ['edit_ac', 'edit', 'delete']})
def create_block(request, parent_id):
    """Создаёт дочерний блок и наследует права доступа родителя."""

    user = request.user
    parent_block = get_object_or_404(Block, id=parent_id)
    data = request.data.get('data', {})
    if 'childOrder' not in data.keys():
        data['childOrder'] = []
    new_block = Block.objects.create(
        creator=user,
        title=request.data.get('title', ""),
        data=data
    )
    parent_permissions = chain(
        BlockPermission.objects.filter(block=parent_block).exclude(user=user),
        [PermissionData(user=user, permission='delete')]
    )
    with transaction.atomic():
        new_permissions = [
            BlockPermission(
                block=new_block,
                user=perm.user,
                permission=perm.permission) for perm in parent_permissions
        ]
        BlockPermission.objects.bulk_create(new_permissions)  # Массовое создание разрешений
    parent_block.add_child(new_block)
    new_block.parent_id = parent_block.id

    send_message_subscribe_user.delay([str(new_block.id)], [perm.user.id for perm in new_permissions])
    send_message_block_update.delay(str(parent_block.id), get_object_for_block(parent_block))
    send_message_block_update.delay(str(new_block.id), get_object_for_block(new_block))

    # Уведомление о добавлении дочернего блока
    notify_block_change.delay(str(parent_block.id), 'child_add', user.id)

    return Response([get_object_for_block(new_block), get_object_for_block(parent_block)],
                    status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_new_tree(request):
    """Создаёт корневой блок (новое дерево) для текущего пользователя."""

    title = request.data.get('title', "")
    user = request.user
    block = Block.objects.create(creator=user, title=title, data={})
    BlockPermission(
        block=block,
        user=user,
        permission='delete'
    ).save()
    send_message_subscribe_user.delay([str(block.id)], [user.id])
    send_message_block_update.delay(str(block.id), get_object_for_block(block))
    return Response(get_object_for_block(block), status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({
    'parent_id': ['edit_ac', 'edit', 'delete'],
    'source_id': ['view', 'edit_ac', 'edit', 'delete']})
def create_link_on_block(request, parent_id, source_id):
    """Создаёт блок-ссылку на существующий блок и наследует права доступа."""

    user = request.user

    blocks = {b.id: b for b in Block.objects.filter(id__in=[parent_id, source_id])}
    parent_block = blocks.get(parent_id)
    source_block = blocks.get(source_id)

    if not parent_block or not source_block:
        return Response({"error": "Block not found"}, status=404)

    with transaction.atomic():
        # Создаём новый блок-ссылку
        link = Block.objects.create(creator=user, data={'view': 'link', 'source': str(source_id)})

        # Создаём связь
        BlockLink.objects.create(target=link, source=source_block)

        # Загружаем разрешения одним запросом
        parent_rem = list(BlockPermission.objects.filter(block=parent_block))

        BlockPermission.objects.bulk_create([
            BlockPermission(user=perm.user, block=link, permission=perm.permission)
            for perm in parent_rem
        ], ignore_conflicts=True)

        # Отправка сообщений о подписке одним батчем
        user_ids = {perm.user.id for perm in parent_rem}

        parent_block.add_child(link)

        _ = [set_block_permissions_task.delay(
            initiator_id=user.id,
            target_user_id=perm.user.id,
            block_id=source_block.id,
            new_permission=perm.permission,
        ) for perm in parent_rem]

        send_message_subscribe_user.delay([str(link.id), str(source_block.id)], list(user_ids))
        send_message_block_update.delay(parent_block.id, get_object_for_block(parent_block))
        send_message_block_update.delay(link.id, get_object_for_block(link))

    return Response([
        get_object_for_block(parent_block),
        get_object_for_block(source_block),
        get_object_for_block(link)
    ], status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({
    'old_parent_id': ['edit_ac', 'edit', 'delete'],
    'new_parent_id': ['edit_ac', 'edit', 'delete'],
    'child_id': ['view', 'edit_ac', 'edit', 'delete']})
def move_block(request, old_parent_id, new_parent_id, child_id):
    """Перемещает блок между родителями и обновляет порядок детей."""

    child = get_object_or_404(Block, id=child_id)

    if 'childOrder' not in request.data:
        return Response({"detail": "childOrder fields are required"}, status=status.HTTP_400_BAD_REQUEST)
    child_order = request.data.get('childOrder')
    if new_parent_id == old_parent_id:
        parent = get_object_or_404(Block, id=new_parent_id)
        parent.set_child_order(child_order)
        res = [get_object_for_block(parent)]
        send_message_block_update.delay(parent.id, get_object_for_block(parent))
    else:
        old_parent = get_object_or_404(Block, id=old_parent_id)
        new_parent = get_object_or_404(Block, id=new_parent_id)
        old_parent.remove_child(child)
        new_parent.add_child_and_set_order(child, child_order)
        existing_source_perms = list(BlockPermission.objects.filter(block_id=new_parent)
                                     .values_list('user_id', 'permission'))
        _ = [set_block_permissions_task.delay(
            initiator_id=request.user.id,
            target_user_id=user_id,
            block_id=child.id,
            new_permission=permission,
        ) for user_id, permission in existing_source_perms]

        res = [get_object_for_block(new_parent), get_object_for_block(old_parent), get_object_for_block(child)]
        send_message_block_update.delay(old_parent.id, get_object_for_block(old_parent))
        send_message_block_update.delay(new_parent.id, get_object_for_block(new_parent))

        # Уведомления о перемещении
        notify_block_change.delay(str(child.id), 'move', request.user.id)
        notify_block_change.delay(str(old_parent.id), 'child_delete', request.user.id)
        notify_block_change.delay(str(new_parent.id), 'child_add', request.user.id)

    return Response(res, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'block_id': ['edit_ac', 'edit', 'delete'], })
def edit_block(request, block_id):
    """Обновляет заголовок и данные блока, исключая системные поля."""

    block = get_object_or_404(Block, id=block_id)
    old_text = block.data.get('text', '')
    old_title = block.title

    block.title = request.data.get('title', block.title)
    data = request.data.get('data', {})

    # Валидация: data должен быть словарём
    if not isinstance(data, dict):
        return Response(
            {"detail": "data must be a JSON object"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Удаляем системные поля, которые нельзя редактировать напрямую
    data.pop('source', None)

    block.data.update(data)
    if block.data.get('customGrid', {}).get('reset'):
        block.data.pop('customGrid')
    block.save()
    send_message_block_update.delay(block.id, get_object_for_block(block))

    # Определяем тип изменения и отправляем уведомление
    new_text = block.data.get('text', '')
    if new_text != old_text or block.title != old_title:
        notify_block_change.delay(str(block.id), 'text_change', request.user.id)
    elif data:
        notify_block_change.delay(str(block.id), 'data_change', request.user.id)

    return Response(get_object_for_block(block), status=status.HTTP_200_OK)


def build_values(rows):
    """Формирует SQL-плейсхолдеры и параметры для массовой вставки блоков."""

    placeholders = []
    params = []
    for row in rows:
        placeholders.append("(%s,%s,%s,%s,%s,%s)")
        params.extend([
            row['id'], row['title'], Json(row['data']),
            row['parent_id'], row['creator_id'], row['updated_at']
        ])
    return ','.join(placeholders), params


class CopyBlockView(APIView):
    """
    Копируем поддеревья блоков и возвращаем результат в формате:
    {
      "<new_block_id>": {
        "id": <new_block_id>,
        "data": {...},
        "updated_at": "...",
        "title": "...",
        "children": [<child_id>, ...]
      },
      ...
      "<dest_id>": {
        "id": "<dest_id>",
        "data": {...},
        "updated_at": "...",
        "title": "...",
        "children": [...]
      }
    }
    """
    permission_classes = [IsAuthenticated]
    MAX_DEPTH_COPY = 50

    def copy_hierarchy(self, user_id, src_ids):
        """
        src_ids — список строк (UUID в виде строк).
        Возвращаем:
          - copied_data (dict): {<new_uuid_str>: {"id": ..., "title": ..., "children": ...}, ...}
          - old_to_new (dict): {<old_uuid_str>: <new_uuid_str>, ...}
        """

        # 0) Функция для замены старых uuid на новые в данных блока
        def replace_uuids_in_data(data, mapping):
            if isinstance(data, dict):
                return {mapping.get(k, k): replace_uuids_in_data(v, mapping) for k, v in data.items()}
            elif isinstance(data, list):
                return [replace_uuids_in_data(item, mapping) for item in data]
            elif isinstance(data, str):
                return mapping.get(data, data)
            return data

        # 1) Загрузка нужных блоков
        with connection.cursor() as cursor:
            cursor.execute(
                load_empty_blocks_query,
                {
                    'user_id': user_id,
                    'block_ids': src_ids,
                    'max_depth': self.MAX_DEPTH_COPY,
                    'max_blocks': settings.LIMIT_BLOCKS
                }
            )
            rows = cursor.fetchall()

        if not rows:
            return {}, {}, {"detail": "Src not found or forbidden"}
        if len(rows) >= settings.LIMIT_BLOCKS:
            return {}, {}, {"detail": "Limit is exceeded"}
        if rows[-1][-2] >= self.MAX_DEPTH_COPY:
            return {}, {}, {"detail": "Max depth is exceeded"}

        # 2) Формирование src_map и parent_to_children
        src_map = {
            str(row[0]): {
                'id': str(row[0]),
                'parent_id': str(row[1]) if row[1] else None,
                'creator_id': user_id,
                'title': row[2],
                'data': json.loads(row[3]),
                'updated_at': row[4],
            }
            for row in rows
        }

        parent_to_children = defaultdict(list)
        for block in src_map.values():
            parent_id = block['parent_id']
            if parent_id in src_map:
                parent_to_children[parent_id].append(block['id'])

        # 3) Генерация маппинга old_to_new UUIDs
        old_to_new = {str(old_id): str(uuid.uuid4()) for old_id in src_map.keys()}

        # 4) Подготовка данных для вставки и результирующего словаря
        blocks_to_insert = []
        access_to_insert = []
        copied_data = {}
        for old_id, block in src_map.items():
            new_id = old_to_new[old_id]
            new_parent = old_to_new.get(block['parent_id'])

            # Заменяем UUIDs в data
            new_data = replace_uuids_in_data(block['data'], old_to_new)

            blocks_to_insert.append((
                new_id,
                new_parent,
                user_id,
                block['title'],
                json.dumps(new_data, ensure_ascii=False),
                now()
            ))
            access_to_insert.append((new_id, user_id, 'delete'))

            copied_data[new_id] = {
                "id": new_id,
                "data": new_data,
                "parent_id": new_parent,
                "updated_at": block['updated_at'].isoformat(),
                "title": block['title'],
                "children": []
            }

        # 5) Массовая вставка блоков
        insert_sql = """
            INSERT INTO api_block (id, parent_id, creator_id, title, data, updated_at)
            VALUES %s
        """
        with transaction.atomic(), connection.cursor() as cursor:
            execute_values(cursor, insert_sql, blocks_to_insert)

        # 6) Заполнение "children" для каждого родителя
        for old_parent, children in parent_to_children.items():
            new_parent = old_to_new.get(old_parent)
            if new_parent:
                copied_data[new_parent]["children"] = [old_to_new[child] for child in children]

        return copied_data, old_to_new, {}

    def validate_uuid_list(self, uuid_list):
        """
        Проверяет, что все элементы в списке являются корректными UUID.
        Возвращает список UUID объектов или ошибку.
        """
        validated_uuids = []
        invalid_uuids = []

        for uid in uuid_list:
            try:
                validated_uuids.append(uuid.UUID(uid))
            except (ValueError, TypeError):
                invalid_uuids.append(uid)

        if invalid_uuids:
            return None, {
                "error": f"Invalid UUIDs: {', '.join(invalid_uuids)}"
            }

        return validated_uuids, None

    def validate_permissions(self, request, block_dest_id, src_ids):
        """
        Проверяет права доступа. Возвращает (True, None) если всё ок,
        или (False, (error_dict, status_code)) если есть ошибка.
        """
        if not block_dest_id or not src_ids:
            return False, ({"detail": "dest and src_ids is required"}, status.HTTP_400_BAD_REQUEST)

        if not BlockPermission.objects.filter(
                block__id=block_dest_id,
                user=request.user,
                permission__in=['edit', 'edit_ac', 'delete']
        ).exists():
            return False, ({'detail': f'Forbidden {block_dest_id}'}, status.HTTP_403_FORBIDDEN)

        if not all(BlockPermission.objects.filter(block__id=src_id,
                                                  user=request.user,
                                                  permission__in=['view', 'edit', 'edit_ac', 'delete']).exists() for
                   src_id in src_ids):
            return False, ({'detail': 'Forbidden'}, status.HTTP_403_FORBIDDEN)

        return True, None

    def post(self, request):
        user = request.user

        block_dest_id = request.data.get('dest')  # строка
        src_ids = request.data.get('src', [])  # список строк

        # Проверяем наличие src_ids перед другими операциями
        if not src_ids:
            return Response({"detail": "src_ids is required"}, status=status.HTTP_400_BAD_REQUEST)

        block_dest = get_object_or_404(Block, id=block_dest_id)

        is_valid, error = self.validate_permissions(request, block_dest_id, src_ids)
        if not is_valid:
            return Response(error[0], error[1])

        validated_src_ids, uuid_error = self.validate_uuid_list(src_ids)
        if uuid_error:
            return Response(uuid_error, status=status.HTTP_400_BAD_REQUEST)

        copies, mapped, err = self.copy_hierarchy(user.id, validated_src_ids)
        if err:
            return Response(err, status=status.HTTP_400_BAD_REQUEST)

        # Оборачиваем операции записи в транзакцию
        with transaction.atomic():
            # Получаем права для block_dest и устанавливаем их для новых блоков
            existing_source_perms = list(
                BlockPermission.objects.filter(block_id=block_dest_id)
                .values_list('user_id', 'permission')
            )
            new_permissions = [
                BlockPermission(block_id=new_block_id, user_id=user_id, permission=permission)
                for new_block_id in mapped.values()
                for user_id, permission in existing_source_perms
            ]
            BlockPermission.objects.bulk_create(new_permissions)

            # Связываем скопированные корневые блоки с block_dest
            new_root_ids = [mapped[old_id] for old_id in src_ids if old_id in mapped]
            if new_root_ids:
                block_dest.add_children(Block.objects.filter(id__in=new_root_ids))

            # Обновляем список дочерних блоков block_dest
            existing_children_ids = list(
                Block.objects.filter(parent=block_dest).values_list('id', flat=True)
            )

            # Обновляем данные копий с информацией о block_dest
            copies[str(block_dest.id)] = {
                "id": str(block_dest.id),
                "data": block_dest.data,
                "parent_id": str(block_dest.parent.id) if block_dest.parent else None,
                "updated_at": block_dest.updated_at.isoformat(),
                "title": block_dest.title,
                "children": [str(child_id) for child_id in existing_children_ids],
            }
            [copies[block_id].update({'parent_id': str(block_dest.id)}) for block_id in new_root_ids]

        # Асинхронная отправка сообщений об обновлении блоков
        send_message_block_update.delay(block_dest.id, get_object_for_block(block_dest))
        send_message_subscribe_user.delay(list(copies.keys()), [user.id])

        # Безопасное получение ID первого скопированного блока
        first_copy_id = mapped.get(src_ids[0], '') if src_ids and mapped else ''
        return Response(copies, status=status.HTTP_200_OK, headers={'x-copy-block-id': first_copy_id})


def get_flat_map(user_id, block_ids):
    """Возвращает плоское представление блоков, доступных пользователю."""

    block_ids = [uuid.UUID(bid) for bid in block_ids]

    with connection.cursor() as cursor:
        cursor.execute(load_empty_blocks_query, {
            'user_id': user_id,
            'block_ids': block_ids,
            'max_blocks': settings.LIMIT_BLOCKS
        })
        rows = cursor.fetchall()
    if rows:
        return load_empty_block_serializer(rows)


class ImportBlocksView(APIView):
    """
    POST /api/blocks/import
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from api.utils.task_utils import save_task_owner

        data = request.data
        payload = data.get('payload', [])
        if not payload and isinstance(data, dict):
            payload = [item for item in data.values()]
        user = request.user

        # Безопасные default_perms — игнорируем пользовательский ввод
        default_perms = [{'user_id': user.id, 'permission': 'delete'}]

        task = import_blocks_task.delay(payload=payload, user_id=user.id, default_perms=default_perms)

        # Сохраняем владельца задачи для проверки прав доступа
        save_task_owner(task.id, user.id)

        return Response(data={"task_id": task.id},
                        status=status.HTTP_202_ACCEPTED)


class UserListView(generics.ListAPIView):
    """
    GET /api/v1/users/
    Возвращает список всех пользователей с пагинацией. Доступен только администраторам.

    Параметры:
    - page: номер страницы (по умолчанию 1)
    - page_size: количество на странице (по умолчанию 200, макс. 200)
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return User.objects.all().order_by('-date_joined')

    def list(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return Response(
                {'detail': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )

        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)

        if page is not None:
            users_data = [
                {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'is_active': user.is_active,
                    'is_staff': user.is_staff,
                    'date_joined': user.date_joined,
                }
                for user in page
            ]
            return self.get_paginated_response(users_data)

        users_data = [
            {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'is_staff': user.is_staff,
                'date_joined': user.date_joined,
            }
            for user in queryset
        ]
        return Response(users_data, status=status.HTTP_200_OK)

