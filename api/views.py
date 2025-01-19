import json
import logging
import uuid
from collections import defaultdict
from pprint import pprint
from tkinter.tix import Tree

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
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
from .models import Block, BlockPermission, BlockLink, ALLOWED_SHOW_PERMISSIONS, PERMISSION_CHOICES, BlockUrlLinkModel
from .serializers import (RegisterSerializer,
                          CustomTokenObtainPairSerializer, BlockSerializer, get_object_for_block, get_forest_serializer,
                          load_empty_block_serializer, access_serializer, links_serializer, block_link_serializer)
from api.utils.query import get_all_trees_query, \
    load_empty_blocks_query, delete_tree_query, get_block_for_url
from .tasks import send_message_block_update, send_message_subscribe_user, set_block_permissions_task
from .utils.decorators import subscribe_to_blocks, determine_user_id, check_block_permissions
from celery.result import AsyncResult

User = get_user_model()

logger = logging.getLogger(__name__)


class TaskStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, task_id):
        """
        Получает статус задачи Celery по task_id.
        """
        task_result = AsyncResult(task_id)
        response_data = {
            'task_id': task_id,
            'status': task_result.status,
            'result': task_result.result if task_result.status == 'SUCCESS' else None,
        }
        return Response(response_data)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10  # Количество объектов на страницу
    page_size_query_param = 'page_size'
    max_page_size = 100


class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


class BlockSearchAPIView(generics.ListAPIView):
    serializer_class = BlockSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        query = self.request.GET.get('q', '').strip()
        include_public = self.request.GET.get('include_public', 'false').lower() == 'true'
        user = self.request.user

        filters = Q(creator=user)

        # Добавляем публичные блоки, если параметр include_public=True
        if include_public:
            filters |= Q(access_type='public')

        blocks = Block.objects.filter(filters)

        if query:
            blocks = blocks.filter(
                Q(title__icontains=query) |
                Q(data__text__icontains=query)
            )

        return blocks


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
    permission_classes = (IsAuthenticated,)

    def get(self, request, block_id):
        block_permissions = BlockPermission.objects.filter(block__id=block_id).select_related('user')
        if not block_permissions.exists():
            return Response({'error': 'Block does not exist'}, status=status.HTTP_404_NOT_FOUND)
        return Response(access_serializer(block_permissions))

    def post(self, request, block_id):
        initiator = request.user
        permission_type = request.data.get('permission_type')
        target_username = request.data.get('target_username')

        if not permission_type and not target_username:
            return Response(
                {"detail": "Please provide target_username and permission_type in request data."},
                status=status.HTTP_400_BAD_REQUEST
            )

        valid_permissions = [choice[0] for choice in PERMISSION_CHOICES]
        if permission_type not in valid_permissions:
            return Response(
                {"detail": f"Invalid permission '{permission_type}'. "
                           f"Must be one of: {valid_permissions}."},
                status=status.HTTP_400_BAD_REQUEST
            )

        block = get_object_or_404(Block, id=block_id)
        target_user = get_object_or_404(User, username=target_username)

        if not BlockPermission.objects.filter(block=block, user=initiator,
                                              permission__in=('edit_ac', 'delete')).first():
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        task = set_block_permissions_task.delay(
            initiator_id=initiator.id,
            target_user_id=target_user.id,
            block_id=str(block.id),
            new_permission=permission_type
        )

        return Response(
            {"task_id": task.id, "detail": "Permission update task has been started."},
            status=status.HTTP_202_ACCEPTED
        )


# todo сделать показ дефолтной страницы для анонимов для root_block_view, load_links, load_empty_blocks


@api_view(["GET"])
@determine_user_id
@subscribe_to_blocks(send_message_subscribe_user)
def load_tress(request, user_id):
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
    user = request.user
    parent_block = get_object_or_404(Block, id=parent_id)
    data = request.data.get('data', {})
    if 'childOrder' not in data.keys():
        data['childOrder'] = []
    new_block = Block.objects.create(
        creator_id=user.id,
        title=request.data.get('title', ""),
        data=data)
    parent_permissions = BlockPermission.objects.filter(block=parent_block)
    with transaction.atomic():
        new_permissions = [
            BlockPermission(
                block=new_block,
                user=perm.user,
                permission=perm.permission
            )
            for perm in parent_permissions
        ]
        BlockPermission.objects.bulk_create(new_permissions)  # Массовое создание разрешений
    parent_block.add_child(new_block)
    _ = [send_message_subscribe_user.delay([str(new_block.id)], perm.user.id) for perm in parent_permissions]
    send_message_block_update.delay(str(parent_block.id), get_object_for_block(parent_block))
    return Response([get_object_for_block(new_block), get_object_for_block(parent_block)],
                    status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@check_block_permissions({
    'parent_id': ['edit_ac', 'edit', 'delete'], 'child_id': ['delete', ]})
def delete_child_block(request, parent_id, child_id):
    parent_block = get_object_or_404(Block, id=parent_id)
    child_block = get_object_or_404(Block, id=child_id)

    parent_block.remove_child(child_block)
    BlockLink.objects.filter(target__id=child_id).delete()  # если дочерни блок это ссылка то удаляем запись о ссылке
    if not BlockLink.objects.filter(source__id=child_id).exists():
        # если нет ссылок на этот блок то удаляем
        # todo unsubscribe child
        child_block.delete()
    send_message_block_update.delay(parent_block.id, get_object_for_block(parent_block))
    return Response(get_object_for_block(parent_block), status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'tree_id': ['delete'], })
def delete_tree(request, tree_id):
    block = get_object_or_404(Block, id=tree_id)
    user_id = request.user.id
    with connection.cursor() as cursor:
        cursor.execute(delete_tree_query, {'block_id': tree_id, 'user_id': user_id})
        rows = cursor.fetchall()
    parent_block = block.parent
    parent_data = {}
    if parent_block:
        parent_block.remove_child(block)
        parent_data = get_object_for_block(parent_block)
        send_message_block_update.delay(str(parent_block.id), get_object_for_block(parent_block))

    Block.objects.filter(id__in=[row[0] for row in rows]).delete()
    return Response({'parent': parent_data}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'block_id': ['delete'], })
def create_url(request, block_id):
    block = get_object_or_404(Block, id=block_id)
    slug = request.data.get('slug')
    link = BlockUrlLinkModel.objects.create(source=block, slug=slug, creator=request.user)
    text = block.data.setdefault('text', '') + "<br>" + f"{settings.FRONTEND_HOST}?path/{link.slug}"
    block.data['text'] = text
    block.save(update_fields=['data'])
    send_message_block_update.delay(str(block.id), get_object_for_block(block))
    return Response(get_object_for_block(block), status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated, ])
@check_block_permissions({'block_id': ALLOWED_SHOW_PERMISSIONS, })
def get_urls(request, block_id):
    links = BlockUrlLinkModel.objects.filter(source_id=block_id)
    return Response(links_serializer(links), status=status.HTTP_200_OK)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, ])
def delete_url(request, link_id):
    link = get_object_or_404(id=link_id, creator=request.user)
    link.delete()
    return Response({'detail': 'Deleted successfully'}, status=status.HTTP_200_OK)


@api_view(['GET'])
def block_url(request, slug):
    link = get_object_or_404(BlockUrlLinkModel, slug=slug)
    source = link.source

    with connection.cursor() as cursor:
        cursor.execute(get_block_for_url, {'block_id': str(source.id), 'max_depth': settings.LINK_LOAD_DEPTH_LIMIT})
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    data = block_link_serializer(rows, settings.LINK_LOAD_DEPTH_LIMIT)
    send_message_subscribe_user(list(data.keys()), -1)
    return Response(data, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({
    'parent_id': ['edit_ac', 'edit', 'delete'],
    'source_id': ['view', 'edit_ac', 'edit', 'delete']})
def create_link_on_block(request, parent_id, source_id):
    user = request.user
    parent_block = get_object_or_404(Block, id=parent_id)
    source_block = get_object_or_404(Block, id=source_id)
    link = Block.objects.create(
        creator=user,
        data={'view': 'link', 'source': str(source_id)})
    BlockPermission.objects.create(user=user, block=link, permission='delete').save()
    parent_block.add_child(link)
    BlockLink.objects.create(target=link, source=source_block).save()
    send_message_subscribe_user.delay([str(source_block.id)], user.id)
    send_message_block_update.delay(parent_block.id, get_object_for_block(parent_block))
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
        new_parent.add_child(child)
        res = [get_object_for_block(new_parent), get_object_for_block(old_parent)]
        send_message_block_update.delay(old_parent.id, get_object_for_block(old_parent))
        send_message_block_update.delay(new_parent.id, get_object_for_block(new_parent))
    return Response(res, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@check_block_permissions({'block_id': ['edit_ac', 'edit', 'delete'], })
def edit_block(request, block_id):
    block = get_object_or_404(Block, id=block_id)
    block.title = request.data.get('title', block.title)
    data = request.data.get('data', {})
    data.pop('source', None)
    block.data.update(data)
    if block.data.get('customGrid', {}).get('reset'):
        block.data.pop('customGrid')
    block.save()
    send_message_block_update.delay(block.id, get_object_for_block(block))
    return Response(get_object_for_block(block), status=status.HTTP_200_OK)


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
    validate_res = False

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
                    'max_depth': settings.MAX_DEPTH_LOAD,
                    'max_blocks': settings.LIMIT_BLOCKS
                }
            )
            rows = cursor.fetchall()

        if not rows:
            return {}, {}, {"detail": "Src not found or forbidden"}
        if len(rows) >= settings.LIMIT_BLOCKS:
            return {}, {}, {"detail": "Limit is exceeded"}

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
                "updated_at": block['updated_at'].isoformat(),
                "title": block['title'],
                "children": []
            }

        # 5) Массовая вставка блоков
        insert_sql = """
            INSERT INTO api_block (id, parent_id, creator_id, title, data, updated_at)
            VALUES %s
        """
        insert_permission_sql = """
            INSERT INTO api_blockpermission (block_id, user_id, permission)
            VALUES %s
        """
        with transaction.atomic(), connection.cursor() as cursor:
            execute_values(cursor, insert_sql, blocks_to_insert)
            execute_values(cursor, insert_permission_sql, access_to_insert)

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

    def validation(self, request, block_dest_id, src_ids):
        if not block_dest_id or not src_ids:
            self.validate_res = {"detail": "dest and src_ids is required"}, status.HTTP_400_BAD_REQUEST

        if not BlockPermission.objects.filter(
                block__id=block_dest_id,
                user=request.user,
                permission__in=['edit', 'edit_ac', 'delete']
        ).exists():
            self.validate_res = {'detail': f'Forbidden {block_dest_id}'}, status.HTTP_403_FORBIDDEN
        if not all(BlockPermission.objects.filter(block__id=src_id,
                                                  user=request.user,
                                                  permission__in=['view', 'edit', 'edit_ac', 'delete']).exists() for
                   src_id in
                   src_ids):
            self.validate_res = {'detail': 'Forbidden'}, status.HTTP_403_FORBIDDEN
        if self.validate_res:
            return True
        return False

    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response({"detail": "Authentication required"}, status=status.HTTP_403_FORBIDDEN)

        block_dest_id = request.data.get('dest')  # строка
        src_ids = request.data.get('src', [])  # список строк

        block_dest = get_object_or_404(Block, id=block_dest_id)
        if self.validation(request, block_dest_id, src_ids):
            return Response(self.validate_res[0], self.validate_res[1])

        validated_src_ids, error = self.validate_uuid_list(src_ids)
        if error:
            self.validate_res = error, status.HTTP_400_BAD_REQUEST

        copies, mapped, err = self.copy_hierarchy(user.id, validated_src_ids)
        if err:
            return Response(err, status=status.HTTP_400_BAD_REQUEST)

        # Подвешиваем корневые скопированные блоки к block_dest
        new_root_ids = [mapped[old_id] for old_id in src_ids if old_id in mapped]

        if new_root_ids:
            block_dest.add_children(Block.objects.filter(id__in=new_root_ids))

        # Обновляем children информации для block_dest
        existing_children_ids = list(
            Block.objects.filter(parent=block_dest).values_list('id', flat=True)
        )

        # Обновляем copied_data с информацией о block_dest
        copies[str(block_dest.id)] = {
            "id": str(block_dest.id),
            "data": block_dest.data,
            "updated_at": block_dest.updated_at.isoformat(),
            "title": block_dest.title,
            "children": [str(child_id) for child_id in existing_children_ids],
        }
        send_message_block_update.delay(block_dest.id, get_object_for_block(block_dest))
        send_message_subscribe_user.delay(list(copies.keys()), user.id)
        return Response(copies, status=status.HTTP_200_OK)


def get_flat_map(user_id, block_ids):
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
