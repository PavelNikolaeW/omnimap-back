import json
import logging

import uuid6
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

from django.conf import settings
from .models import Block, ACCESS_TYPE_CHOICES
from .serializers import (RegisterSerializer,
                          CustomTokenObtainPairSerializer, BlockSerializer)
from api.utils.query import get_blocks_query
from .tasks import send_message_block_update, send_message_subscribe_user

User = get_user_model()

logger = logging.getLogger(__name__)

FORBIDDEN_BLOCK = {'id': '',
                   'title': 'block 403 forbidden',
                   'children': [],
                   'updated_at': '2000-01-01T00:00:01.000001Z',
                   'can_be_edited_by_others': True,
                   'data': {'color': [0, 100, 100, 0], 'childOrder': []}}


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
                data={'color': 'default_color'}
            )
            new_block.save()
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

    def get(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            block = get_object_or_404(Block, id=block_id)
            access_type = block.access_type
            visible_to_users = [u.username for u in block.visible_to_users.all() if u.id != user.id]
            editable_by_users = [u.username for u in block.editable_by_users.all() if u.id != user.id]
            return Response({'access_type': access_type, 'visible_to_users': visible_to_users,
                             'editable_by_users': editable_by_users}, status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)

    def post(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            block = get_object_or_404(Block, id=block_id)
            if not user == block.creator:
                return Response({}, status=status.HTTP_403_FORBIDDEN)
            access_type = request.data.get('access_type')
            visible_for = request.data.get('visible_for')
            editable_for = request.data.get('editable_for')
            if access_type:
                if not any(access_type == t[0] for t in ACCESS_TYPE_CHOICES):
                    return Response({'message': 'Wrong access type'}, status=status.HTTP_400_BAD_REQUEST)
                block.access_type = access_type
                block.save()
            if visible_for:
                visible_for_user = get_object_or_404(User, username=visible_for)
                block.visible_to_users.add(visible_for_user)
            if editable_for:
                editable_for_user = get_object_or_404(User, username=editable_for)
                block.editable_by_users.add(editable_for_user)
            # todo отправлять сообщение об обнолвении
            return Response({
                'access_type': block.access_type,
                'visible_to_users': [u.username for u in block.visible_to_users.all() if u.id != user.id],
                'editable_by_users': [u.username for u in block.editable_by_users.all() if u.id != user.id]
            }, status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)

    def delete(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            block = get_object_or_404(Block, id=block_id)
            if not user == block.creator:
                return Response({}, status=status.HTTP_403_FORBIDDEN)
            remove_vis = request.data.get('remove_vision_for')
            remove_edit = request.data.get('remove_edit_for')

            if remove_vis:
                visible_for_user = get_object_or_404(User, username=remove_vis)
                block.visible_to_users.remove(visible_for_user)

            if remove_edit:
                editable_for_user = get_object_or_404(User, username=remove_edit)
                block.editable_by_users.remove(editable_for_user)

            return Response({
                'access_type': block.access_type,
                'visible_to_users': [u.username for u in block.visible_to_users.all() if u.id != user.id],
                'editable_by_users': [u.username for u in block.editable_by_users.all() if u.id != user.id]
            }, status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


class RootBlockView(APIView):
    def get(self, request):
        user = request.user
        if user.is_authenticated:
            block_id = user.blocks.first().id
            data, blocks_to_subscribe = get_flat_map_blocks(user.id, [block_id])
            data['root'] = data[block_id]
            send_message_subscribe_user.delay(blocks_to_subscribe, user.username)
            return Response(data, status=status.HTTP_200_OK)

        main_page_user = User.objects.get(username='main_page')
        main_page_block = main_page_user.blocks.first()
        data, _ = get_flat_map_blocks(main_page_user.id, [main_page_block.id])
        data['root'] = data[main_page_block.id]
        return Response(data, status=status.HTTP_203_NON_AUTHORITATIVE_INFORMATION)


class MoveBlockView(APIView):

    @transaction.atomic
    def post(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            child = get_object_or_404(Block, id=block_id)
            if user == child.creator or child.editable_by_users.filter(
                    id=user.id).exists() or child.access_type == 'public_ed':

                new_parent_id = request.data.get('new_parent_id')
                old_parent_id = request.data.get('old_parent_id')
                child_order = request.data.get('childOrder')

                print(f"{new_parent_id=} {old_parent_id=}")

                if new_parent_id == old_parent_id:
                    parent = get_object_or_404(Block, id=new_parent_id)
                    parent.set_child_order(child_order)
                    res = [{
                        'id': parent.id, 'updated_at': parent.updated_at,
                        'children': [child.id for child in parent.children.all()],
                        'data': parent.data,
                        'title': parent.title
                    }]
                else:
                    old_parent = get_object_or_404(Block, id=old_parent_id)
                    old_parent.children.remove(child)

                    new_parent = get_object_or_404(Block, id=new_parent_id)
                    new_parent.children.add(child)
                    new_parent.set_child_order(child_order)
                    res = [{
                        'id': new_parent.id, 'children': [child.id for child in new_parent.children.all()],
                        'data': new_parent.data,
                        'updated_at': new_parent.updated_at,
                        'title': new_parent.title
                    }, {
                        'id': old_parent.id, 'children': [child.id for child in old_parent.children.all()],
                        'data': old_parent.data,
                        'updated_at': old_parent.updated_at,
                        'title': old_parent.title
                    }]
                return Response(res, status=status.HTTP_200_OK)

        return Response({}, status=status.HTTP_403_FORBIDDEN)


class LoadEmptyView(APIView):
    def post(self, request):
        user = request.user
        block_ids = request.data.get('block_ids', [])
        if not block_ids:
            return Response({"error": "No block_ids provided"}, status=status.HTTP_400_BAD_REQUEST)

        accessible_blocks = []
        blocks_403 = {}
        for block_id in block_ids:
            block = get_object_or_404(Block, id=block_id)

            if block.access_type in ('public', 'public_ed') or block.visible_to_users.filter(id=user.id).exists():
                accessible_blocks.append(block_id)
            else:
                FORBIDDEN_BLOCK['id'] = block_id
                blocks_403[block_id] = FORBIDDEN_BLOCK.copy()

        data, blocks_to_subscribe = get_flat_map_blocks(user.id, accessible_blocks)
        data.update(blocks_403)
        send_message_subscribe_user.delay(blocks_to_subscribe, user.username)
        return Response(data, status=status.HTTP_200_OK)


class CreateLinkBlockView(APIView):
    @transaction.atomic
    def post(self, request):
        user = request.user

        if user.is_authenticated:
            block_id = request.data.get('dest')
            ids = request.data.get('src', [])
            block = get_object_or_404(Block, id=block_id)
            new_blocks = []
            if user == block.creator or block.editable_by_users.filter(id=user.id).exists():
                for id in ids:
                    new_link = Block.objects.create(creator=user, data={'view': 'link', 'source': id})
                    new_link.save()
                    new_blocks.append({
                        'id': new_link.id,
                        'data': new_link.data
                    })
                    block.children.add(new_link)

                new_blocks.append({'id': block.id,
                                   'data': block.data,
                                   'title': block.title,
                                   'updated_at': block.updated_at,
                                   'children': [child.id for child in block.children.all()]})
                # todo подписка на обновления
                return Response(new_blocks,
                                status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


class NewBlockView(APIView):
    @transaction.atomic
    def post(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            parent_block = get_object_or_404(Block, id=block_id)
            if parent_block.creator == user or parent_block.editable_by_users.filter(id=user.id).exists():
                new_block = Block.objects.create(
                    creator=user,
                    title=request.data.get('title', ''),
                    data=request.data.get('data', {})
                )
                new_block.save()
                parent_block.children.add(new_block)
                send_message_block_update.delay(parent_block.id,
                                                {'children': [child.id for child in parent_block.children.all()]})

                return Response(
                    [
                        {'id': new_block.id, 'title': new_block.title, 'data': new_block.data,
                         'updated_at': new_block.updated_at, 'children': []},
                        {'id': parent_block.id, 'title': parent_block.title, 'updated_at': parent_block.updated_at,
                         'data': parent_block.data,
                         'children': [child.id for child in parent_block.children.all()]}
                    ],
                    status=status.HTTP_201_CREATED)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


class CopyBlockView(APIView):

    def copy_hierarchy(self, user_id, src_ids):
        # Получаем все блоки для копирования
        src_map, _ = get_flat_map_blocks(user_id, src_ids)
        if len(src_map) >= settings.LIMIT_BLOCKS - 1:
            return {"error": "Limit is exceeded"}, {}

        # Если src_map пустой, значит нет доступа или блок не найден
        if not src_map:
            return {"error": "Src not found or forbidden"}, {}

        # Генерируем маппинг старый UUID -> новый UUID
        old_to_new = {old_id: str(uuid6.uuid6()) for old_id in src_map.keys()}

        # Функция рекурсивной замены UUID в data
        def replace_uuids_in_data(value, mapping):
            if isinstance(value, dict):
                return {mapping.get(k, k): replace_uuids_in_data(v, mapping) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_uuids_in_data(item, mapping) for item in value]
            elif isinstance(value, str):
                return mapping.get(value, value)
            else:
                return value

        # Подготавливаем данные для вставки
        blocks_to_insert = []
        children_relations = []
        copied_data = {}

        for old_id, block_data in src_map.items():
            new_id = old_to_new[old_id]

            # Заменяем UUID в data
            new_data = replace_uuids_in_data(block_data['data'], old_to_new)

            # Формируем запись для вставки нового блока
            # Все скопированные блоки:
            # - access_type = 'inherited'
            # - visible_to_users = [user_id]
            # - editable_by_users = [user_id]
            # - creator = user_id

            blocks_to_insert.append((
                new_id,
                user_id,
                'inherited',
                block_data['updated_at'],
                json.dumps(new_data),
                block_data['title']
            ))

            # Добавляем дочерние связи
            child_ids = []
            for child_id in block_data['children']:
                if child_id in old_to_new:
                    # Ссылка на новый дочерний id
                    new_child_id = old_to_new[child_id]
                    child_ids.append(new_child_id)
                    children_relations.append((new_id, new_child_id))

            copied_data[new_id] = {
                'id': new_id,
                "data": new_data,
                'updated_at': block_data['updated_at'],
                "title": block_data["title"],
                "children": child_ids
            }

        with transaction.atomic():
            with connection.cursor() as cursor:
                # Вставляем новые блоки
                insert_blocks_sql = '''
                    INSERT INTO api_block (id, creator_id, access_type, updated_at, data, title)
                    VALUES %s
                '''
                # blocks_to_insert – список кортежей (id, creator_id, access_type, data, title)
                execute_values(cursor, insert_blocks_sql, blocks_to_insert)

                # Вставляем связи parent->child (если они есть)
                if children_relations:
                    insert_children_sql = '''
                        INSERT INTO api_block_children (from_block_id, to_block_id)
                        VALUES %s
                    '''
                    # children_relations – список кортежей (from_block_id, to_block_id)
                    execute_values(cursor, insert_children_sql, children_relations)

                # Вставляем M2M связи для visible_to_users и editable_by_users
                new_ids = [old_to_new[i] for i in src_map.keys()]
                if new_ids:
                    # visible_to_users
                    insert_visible_sql = '''
                        INSERT INTO api_block_visible_to_users (block_id, user_id)
                        VALUES %s
                    '''
                    visible_rows = [(block_id, user_id) for block_id in new_ids]
                    execute_values(cursor, insert_visible_sql, visible_rows)

                    # editable_by_users
                    insert_editable_sql = '''
                        INSERT INTO api_block_editable_by_users (block_id, user_id)
                        VALUES %s
                    '''
                    editable_rows = [(block_id, user_id) for block_id in new_ids]
                    execute_values(cursor, insert_editable_sql, editable_rows)

        return copied_data, old_to_new

    def post(self, request):
        user = request.user
        if user.is_authenticated:
            block_dest_id = request.data.get('dest')
            ids = request.data.get('src', [])
            block_dest = get_object_or_404(Block, id=block_dest_id)
            if user == block_dest.creator or block_dest.editable_by_users.filter(id=user.id).exists():
                copies, mapped = self.copy_hierarchy(user.id, ids)

                if error_text := copies.get('error'):
                    return Response({'error': error_text}, status=status.HTTP_400_BAD_REQUEST)

                block_dest.children.add(*Block.objects.filter(id__in=[mapped[old_id] for old_id in ids]))
                copies[str(block_dest.id)] = {
                    'id': block_dest.id,
                    'title': block_dest.title,
                    'data': block_dest.data,
                    'updated_at': block_dest.updated_at,
                    'children': [str(child.id) for child in block_dest.children.all()]
                }
                #     todo отправлять сообщение об обновлвении
                return Response(copies,
                                status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


class EditBlockView(APIView):
    @transaction.atomic
    def post(self, request, block_id):
        user = request.user
        if user.is_authenticated:
            block = get_object_or_404(Block, id=block_id)
            if user == block.creator or block.editable_by_users.filter(
                    id=user.id).exists() or block.access_type == 'public_ed':
                block.title = request.data.get('title', block.title)
                data = request.data.get('data', block.data)
                if (block.access_type == 'public' or block.access_type == 'public_ed') and data.get('connections'):
                    data.pop('connections')
                block.data.update(data)
                if block.data.get('customGrid', {}).get('reset'):
                    block.data.pop('customGrid')
                block.save()
                return Response(
                    {
                        'id': block.id,
                        'title': block.title,
                        'data': block.data,
                        'updated_at': block.updated_at,
                        'children': [child.id for child in block.children.all()]
                    },
                    status=status.HTTP_200_OK)
        #     todo отправлять сообщение об обновлвении
        return Response({}, status=status.HTTP_403_FORBIDDEN)

    @transaction.atomic
    def delete(self, request):
        user = request.user
        if user.is_authenticated:
            remove_id = request.data.get('removeId')
            parent_id = request.data.get('parentId')
            block = get_object_or_404(Block, id=parent_id.split('*')[-1])
            child = get_object_or_404(Block, id=remove_id)
            if user == block.creator or block.editable_by_users.filter(id=user.id).exists():
                block.children.remove(child)
                return Response(
                    {
                        'id': block.id,
                        'title': block.title,
                        'data': block.data,
                        'updated_at': block.updated_at,
                        'children': [child.id for child in block.children.all()]
                    },
                    status=status.HTTP_200_OK)
            #     todo отправлять сообщение об обновлвении
        return Response({}, status=status.HTTP_403_FORBIDDEN)


def get_flat_map_blocks(user_id, block_ids):
    with connection.cursor() as cursor:
        cursor.execute(get_blocks_query, {'user_id': user_id, 'block_ids': block_ids})
        columns = [col[0] for col in cursor.description]
        json_fields = ['data']  # Поля, ожидаемые как JSON

        result = {}
        block_to_subscribe = []
        for row in cursor.fetchall():
            row_dict = dict(zip(columns, row))

            # Преобразование строк, содержащих JSON, в объекты Python
            for field in json_fields:
                try:
                    row_dict[field] = json.loads(row_dict[field])
                except json.JSONDecodeError:
                    print(f"Error decoding JSON for {field} in row {row[0]}")
            if row_dict['can_be_edited_by_others']:
                block_to_subscribe.append(row_dict)
            # Собираем результат, используя id блока в качестве ключа
            if row[0] in result:
                result[row[0]]['children'].extend(row_dict['children'])
                result[row[0]]['children'] = list(set(result[row[0]]['children']))  # Убираем дубликаты
            else:
                result[row[0]] = row_dict
        return result, block_to_subscribe
