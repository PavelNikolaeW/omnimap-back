import json
from collections import defaultdict
from pprint import pprint

from rest_framework import serializers, status
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Block, Group,
)

FORBIDDEN_BLOCK = {'id': '',
                   'title': 'block 403 forbidden',
                   'children': [],
                   'updated_at': '2000-01-01T00:00:01.000001Z',
                   'data': {'color': [0, 100, 100, 0], 'childOrder': []}}


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Добавляем ID пользователя к данным токена
        token['user_id'] = user.id

        return token

    def validate(self, attrs):
        data = super().validate(attrs)

        # Добавляем ID пользователя в ответ
        data['user_id'] = self.user.id

        return data


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})

    class Meta:
        model = User
        fields = ('username', 'password', 'email')

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email'),
            password=validated_data['password']
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email')


class GroupSerializer(serializers.ModelSerializer):
    users = UserSerializer(many=True, read_only=True)
    owner = UserSerializer(read_only=True)

    class Meta:
        model = Group
        fields = ('id', 'name', 'owner', 'users')


class GroupCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ('name',)


class BlockSerializer(serializers.ModelSerializer):
    text = serializers.SerializerMethodField()

    class Meta:
        model = Block
        fields = [
            'id',
            'title',
            'text',
        ]

    def get_text(self, obj):
        return obj.data.get('text', '')


def get_object_for_block(block, children=None):
    return {
        'id': str(block.id),
        'title': block.title,
        'data': block.data,
        'updated_at': block.updated_at,
        'children': children if isinstance(children, (list, str)) else [str(child.id) for child in block.children.all()]
    }

def get_forest_serializer(rows):
    # blocks_by_root: для каждого root_id храним словарь блоков, которые явно загружены (есть строка с данными)
    blocks_by_root = defaultdict(dict)
    # children_mapping: для каждого root_id для каждого parent_id накапливаем список дочерних block_id
    children_mapping = defaultdict(lambda: defaultdict(list))
    # expected_children: для блоков, для которых задано поле total_children
    expected_children = {}
    # Кэш для ускорения разбора JSON
    json_cache = {}

    def parse_json(s):
        if s not in json_cache:
            json_cache[s] = json.loads(s or '{}')
        return json_cache[s]

    # Единый проход по строкам
    for root_id, block_id, parent_id, title, data, updated_at, total_children in rows:
        r = str(root_id)
        b = str(block_id)
        p = str(parent_id) if parent_id else None

        # Сохраняем данные блока только если есть явная строка (то есть, мы загружаем корректные поля)
        blocks_by_root[r][b] = {
            "id": b,
            "title": title,
            "data": parse_json(data),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "children": []  # список детей заполнится ниже
        }
        if total_children is not None:
            expected_children[b] = total_children

        # Если есть родитель, запоминаем связь
        if p:
            children_mapping[r][p].append(b)

    # Для каждого блока, если его данные явно загружены (есть row), добавляем список детей из children_mapping
    for r, parent_children in children_mapping.items():
        for parent_id, child_ids in parent_children.items():
            if parent_id in blocks_by_root[r]:
                blocks_by_root[r][parent_id]["children"] = child_ids

    # Фильтруем блоки: возвращаем блок только если число его дочерних блоков равно ожидаемому
    # Если для блока не задано expected_children, считаем, что он "полный"
    result = {}
    for r, blocks in blocks_by_root.items():
        filtered = {}
        for b, block in blocks.items():
            exp = expected_children.get(b, len(block["children"]))
            if len(block["children"]) < exp and b != r:
                continue  # пропускаем блок, если не все дочерние загружены
            filtered[b] = block
        if filtered:
            result[r] = filtered
    return result

def load_empty_block_serializer(rows, max_depth):
    blocks = {}
    children_map = defaultdict(list)  # parent_id_str -> list of child_id_str

    for (block_id, parent_id, title, data, updated_at, depth, permission) in rows:
        block_id_str = str(block_id)
        parent_id_str = str(parent_id) if parent_id else None

        if depth < max_depth:  # отрезаем последний ряд блоков, что бы в ответ попали блоки с полной информацией
            blocks[block_id_str] = {
                "id": block_id_str,
                "title": title,
                "data": json.loads(data or {}),
                "updated_at": updated_at.isoformat() if updated_at else None,
                "children": []
            } if permission != 'deny' else {**FORBIDDEN_BLOCK, 'id': block_id_str}

        if parent_id_str:
            children_map[parent_id_str].append(block_id_str)
    # Заполняем поле 'children' для каждого блока
    for parent_id, child_ids in children_map.items():
        if parent_id in blocks:
            blocks[parent_id]["children"].extend(child_ids)
    return blocks


def links_serializer(links):
    return [{'creator': link.creator.username,
             'source': link.source.id,
             'slug': link.slug,
             'id': str(link.id)
             } for link in links]


def access_serializer(block_permissions):
    return [{'user_id': perm.user.id,
             'username': perm.user.username,
             'email': perm.user.email,
             'permission': perm.permission
             } for perm in block_permissions]


def block_link_serializer(rows, max_depth):
    blocks_by_id = {}
    parent_map = {}

    for row in rows:
        block_id = str(row['id'])
        parent_id = str(row['parent_id']) if row['parent_id'] else None

        # Парсим данные и формируем блок
        block = {
            'id': row['id'],
            'title': row['title'],
            'data': json.loads(row['data']),
            'updated_at': row['updated_at'],
            'children': [],
            'depth': row['depth']
        }
        blocks_by_id[block_id] = block

        if parent_id:
            parent_map.setdefault(parent_id, []).append(block_id)

    # Связь блоков через parent_map
    for parent_id, children_ids in parent_map.items():
        if parent_id in blocks_by_id:
            blocks_by_id[parent_id]['children'] = children_ids

    # Фильтруем блоки по глубине
    filtered_blocks = {
        block_id: {
            'id': block['id'],
            'title': block['title'],
            'data': block['data'],
            'updated_at': block['updated_at'],
            'children': block['children']
        }
        for block_id, block in blocks_by_id.items()
        if block['depth'] <= max_depth - 1
    }

    return filtered_blocks
