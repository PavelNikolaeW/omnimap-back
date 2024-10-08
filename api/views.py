import json
import logging
from pprint import pprint

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.pagination import PageNumberPagination

from .models import Block, ACCESS_TYPE_CHOICES
from .serializers import (RegisterSerializer,
                          CustomTokenObtainPairSerializer, BlockSerializer)
from .query import get_blocks_query

User = get_user_model()

logger = logging.getLogger(__name__)

INFORM_BLOCK_ID = '1ef849f0-b268-6f66-ae3f-256832c7dcca'
INFORM_BLOCK_USER_ID = 2


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
            data = get_flat_map_blocks(user.id, [block_id])
            data['root'] = data[block_id]
            return Response(data, status=status.HTTP_200_OK)
        data = get_flat_map_blocks(INFORM_BLOCK_USER_ID, [INFORM_BLOCK_ID])
        data['root'] = data[INFORM_BLOCK_ID]
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
                        'id': parent.id, 'children': [child.id for child in parent.children.all()], 'data': parent.data,
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
                        'title': new_parent.title
                    }, {
                        'id': old_parent.id, 'children': [child.id for child in old_parent.children.all()],
                        'data': old_parent.data,
                        'title': old_parent.title
                    }]
                return Response(res, status=status.HTTP_200_OK)

        return Response({}, status=status.HTTP_403_FORBIDDEN)


class LoadEmptyView(APIView):
    def post(self, request):
        user = request.user
        if user.is_authenticated:
            block_ids = request.data.get('block_ids', [])
            block_ids = [int(id) for id in block_ids]
            data = get_flat_map_blocks(user.id, block_ids)
            return Response(data, status=status.HTTP_200_OK)

        return Response({}, status=status.HTTP_403_FORBIDDEN)


class CreateLinkBlockView(APIView):
    @transaction.atomic
    def post(self, request):
        user = request.user
        print('CreateLinkBlockView', request.data)
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
                                   'children': [child.id for child in block.children.all()]})
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

                return Response(
                    [
                        {'id': new_block.id, 'title': new_block.title, 'data': new_block.data, 'children': []},
                        {'id': parent_block.id, 'title': parent_block.title, 'data': parent_block.data,
                         'children': [child.id for child in parent_block.children.all()]}
                    ],
                    status=status.HTTP_201_CREATED)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


class CopyBlockView(APIView):

    def flatten_structure(self, structure):
        result = []

        def flatten(node):
            if isinstance(node, list):
                for item in node:
                    flatten(item)
            elif isinstance(node, dict):
                # Извлекаем id дочерних элементов
                children_ids = [child['id'] for child in node['children'] if isinstance(child, dict)]
                # Создаем новый объект без вложенных children
                flat_node = {
                    'id': node['id'],
                    'title': node['title'],
                    'data': node['data'],
                    'children': children_ids
                }
                result.append(flat_node)

                # Рекурсивно обрабатываем всех детей
                for child in node['children']:
                    flatten(child)

        flatten(structure)
        return result

    def copy_block(self, block, creator, copied_blocks=None, deep=0):
        """
        Рекурсивно копирует блок и все его дочерние блоки.
        :param block: Исходный блок, который нужно скопировать.
        :param creator: Пользователь, который будет создателем нового блока.
        :param copied_blocks: Список для сохранения всех скопированных блоков в виде словарей.
        :param deep: Глубина рекурсии.
        :return: Новый скопированный блок и список всех скопированных блоков.
        """
        if copied_blocks is None:
            copied_blocks = []

        # Создаем новый блок с копированием всех полей
        new_block = Block.objects.create(
            creator=creator,
            access_type=block.access_type,
            data=block.data,
            title=block.title
        )

        # Копируем связи с видимостью и редактированием пользователей
        new_block.visible_to_users.set(block.visible_to_users.all())
        new_block.editable_by_users.set(block.editable_by_users.all())

        # Создаем словарь для хранения данных нового блока и списка его дочерних блоков
        new_block_dict = {
            'id': new_block.id,
            'title': new_block.title,
            'data': new_block.data,
            'children': []
        }

        # Добавляем словарь нового блока в список скопированных блоков
        copied_blocks.append(new_block_dict)

        # Рекурсивно копируем дочерние блоки
        for child in block.children.all():
            copied_child, copy = self.copy_block(child, creator, copied_blocks=None, deep=deep+1)
            copied_blocks.append(copy)
            new_block.children.add(copied_child)
            # Добавляем скопированный дочерний блок в список детей нового блока
            new_block_dict['children'].append({
                'id': copied_child.id,
                'title': copied_child.title,
                'data': copied_child.data,
                'children': []  # дочерние блоки будут добавлены при дальнейших вызовах
            })

        # Сохраняем изменения
        new_block.save()
        return new_block, copied_blocks

    @transaction.atomic
    def post(self, request):
        user = request.user
        print(request.data)
        if user.is_authenticated:
            block_id = request.data.get('dest')
            ids = request.data.get('src', [])
            block = get_object_or_404(Block, id=block_id)
            new_blocks = []
            if user == block.creator or block.editable_by_users.filter(id=user.id).exists():
                for id in ids:
                    block_to_copy = get_object_or_404(Block, id=id)
                    new_block, copies = self.copy_block(block_to_copy, user)
                    block.children.add(new_block)
                    new_blocks += self.flatten_structure(copies)
                new_blocks.append({
                    'id': block.id,
                    'title': block.title,
                    'data': block.data,
                    'children': [child.id for child in block.children.all()]
                })
                return Response(new_blocks,
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
                pprint(block.data)
                block.save()
                return Response({'id': block.id, 'title': block.title, 'data': block.data,
                                 'children': [child.id for child in block.children.all()]},
                                status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)

    @transaction.atomic
    def delete(self, request):
        user = request.user
        print(request.data)
        if user.is_authenticated:
            remove_id = request.data.get('removeId')
            parent_id = request.data.get('parentId')
            block = get_object_or_404(Block, id=parent_id.split('*')[-1])
            child = get_object_or_404(Block, id=remove_id)
            if user == block.creator or block.editable_by_users.filter(id=user.id).exists():
                block.children.remove(child)
                children = [child.id for child in block.children.all()]
                block_data = block.data
                custom_grid = block_data.get('customGrid', False)
                child_order = block_data.get('childOrder', [])
                pprint(block_data)
                if custom_grid:
                    new_child_positions = {}
                    for key, val in custom_grid['childrenPositions'].items():
                        if (key != 'col' and key != 'row') and int(key) in children:
                            new_child_positions[key] = val

                    custom_grid['childrenPositions'] = new_child_positions

                    block.save()
                pprint({'id': block.id, 'title': block.title, 'data': block.data, 'children': children})
                return Response(
                    {'id': block.id, 'title': block.title, 'data': block.data, 'children': children},
                    status=status.HTTP_200_OK)
        return Response({}, status=status.HTTP_403_FORBIDDEN)


def get_flat_map_blocks(user_id, block_ids):
    with connection.cursor() as cursor:
        cursor.execute(get_blocks_query, {'user_id': user_id, 'block_ids': block_ids})
        columns = [col[0] for col in cursor.description]
        json_fields = ['data']  # Поля, ожидаемые как JSON

        result = {}
        for row in cursor.fetchall():
            row_dict = dict(zip(columns, row))

            # Преобразование строк, содержащих JSON, в объекты Python
            for field in json_fields:
                try:
                    row_dict[field] = json.loads(row_dict[field])
                except json.JSONDecodeError:
                    print(f"Error decoding JSON for {field} in row {row[0]}")

            # Собираем результат, используя id блока в качестве ключа
            if row[0] in result:
                result[row[0]]['children'].extend(row_dict['children'])
                result[row[0]]['children'] = list(set(result[row[0]]['children']))  # Убираем дубликаты
            else:
                result[row[0]] = row_dict
        return result
