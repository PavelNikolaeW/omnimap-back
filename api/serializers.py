import json
from collections import defaultdict

from rest_framework import serializers, status
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    Block, Group, BlockReminder, BlockChangeSubscription,
    UserNotificationSettings, REPEAT_CHOICES, EMAIL_MODE_CHOICES,
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
        token['is_staff'] = user.is_staff
        return token

    def validate(self, attrs):
        data = super().validate(attrs)

        # Добавляем ID пользователя в ответ
        data['user_id'] = self.user.id
        data['is_staff'] = self.user.is_staff
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
        'parent_id': str(block.parent_id),
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
            "parent_id": p,
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
                "parent_id": parent_id_str,
                "data": json.loads(data or {}),
                "updated_at": updated_at.isoformat() if updated_at else None,
                "children": []
            } if permission != 'deny' else {**FORBIDDEN_BLOCK, 'id': block_id_str, 'parent_id': parent_id_str}

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
            'parent_id': parent_id,
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
            'parent_id': block['parent_id'],
            'title': block['title'],
            'data': block['data'],
            'updated_at': block['updated_at'],
            'children': block['children']
        }
        for block_id, block in blocks_by_id.items()
        if block['depth'] <= max_depth - 1
    }

    return filtered_blocks


class PermissionUserItemSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    permission = serializers.ChoiceField(choices=["view", "edit", "deny"])


class PermissionGroupItemSerializer(serializers.Serializer):
    group_id = serializers.IntegerField()
    permission = serializers.ChoiceField(choices=["view", "edit", "deny"])


class PermissionsSerializer(serializers.Serializer):
    users = PermissionUserItemSerializer(many=True, required=False, default=list)
    groups = PermissionGroupItemSerializer(many=True, required=False, default=list)


class ImportBlockItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()  # PK твоего Block
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    data = serializers.JSONField(required=False)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    creator_id = serializers.IntegerField(required=False, allow_null=True)  # если не передашь — возьмём request.user
    links = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    permissions = PermissionsSerializer(required=False, default=dict)
    updated_at = serializers.DateTimeField(required=False, allow_null=True)


class ImportBlocksSerializer(serializers.Serializer):
    blocks = ImportBlockItemSerializer(many=True)


# ============================================================================
# Сериализаторы для напоминаний и уведомлений
# ============================================================================

class BlockReminderSerializer(serializers.ModelSerializer):
    block_id = serializers.UUIDField(write_only=True)
    block_text = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BlockReminder
        fields = [
            'id', 'block_id', 'block', 'remind_at', 'timezone', 'message',
            'repeat', 'is_sent', 'sent_at', 'snoozed_until',
            'created_at', 'updated_at', 'block_text'
        ]
        read_only_fields = [
            'id', 'block', 'is_sent', 'sent_at', 'snoozed_until',
            'created_at', 'updated_at'
        ]

    def get_block_text(self, obj):
        return obj.block.data.get('text', obj.block.title or '')[:200]

    def validate_block_id(self, value):
        try:
            block = Block.objects.get(id=value)
        except Block.DoesNotExist:
            raise serializers.ValidationError("Block not found")

        # Проверяем, нет ли уже напоминания для этого блока
        if BlockReminder.objects.filter(block_id=value).exists():
            raise serializers.ValidationError("Reminder already exists for this block")

        return value

    def validate(self, attrs):
        user = self.context['request'].user
        from django.conf import settings

        # Проверяем лимит напоминаний
        current_count = BlockReminder.objects.filter(user=user).count()
        if current_count >= settings.MAX_REMINDERS_PER_USER:
            raise serializers.ValidationError(
                f"Maximum reminders limit reached ({settings.MAX_REMINDERS_PER_USER})"
            )

        return attrs

    def create(self, validated_data):
        block_id = validated_data.pop('block_id')
        block = Block.objects.get(id=block_id)
        validated_data['block'] = block
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class BlockReminderUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockReminder
        fields = ['remind_at', 'timezone', 'message', 'repeat']


class ReminderSnoozeSerializer(serializers.Serializer):
    minutes = serializers.IntegerField(min_value=1, max_value=1440)


class BlockChangeSubscriptionSerializer(serializers.ModelSerializer):
    block_id = serializers.UUIDField(write_only=True)
    block_text = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = BlockChangeSubscription
        fields = [
            'id', 'block_id', 'block', 'depth',
            'on_text_change', 'on_data_change', 'on_move',
            'on_child_add', 'on_child_delete',
            'created_at', 'last_notification_at', 'block_text'
        ]
        read_only_fields = ['id', 'block', 'created_at', 'last_notification_at']

    def get_block_text(self, obj):
        return obj.block.data.get('text', obj.block.title or '')[:200]

    def validate_block_id(self, value):
        try:
            Block.objects.get(id=value)
        except Block.DoesNotExist:
            raise serializers.ValidationError("Block not found")
        return value

    def validate(self, attrs):
        user = self.context['request'].user
        block_id = attrs.get('block_id')
        from django.conf import settings

        # Проверяем, что подписки ещё нет
        if BlockChangeSubscription.objects.filter(block_id=block_id, user=user).exists():
            raise serializers.ValidationError("Subscription already exists for this block")

        # Проверяем лимит подписок
        current_count = BlockChangeSubscription.objects.filter(user=user).count()
        if current_count >= settings.MAX_SUBSCRIPTIONS_PER_USER:
            raise serializers.ValidationError(
                f"Maximum subscriptions limit reached ({settings.MAX_SUBSCRIPTIONS_PER_USER})"
            )

        return attrs

    def create(self, validated_data):
        block_id = validated_data.pop('block_id')
        block = Block.objects.get(id=block_id)
        validated_data['block'] = block
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class BlockChangeSubscriptionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockChangeSubscription
        fields = [
            'depth', 'on_text_change', 'on_data_change',
            'on_move', 'on_child_add', 'on_child_delete'
        ]


class UserNotificationSettingsSerializer(serializers.ModelSerializer):
    telegram_linked = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = UserNotificationSettings
        fields = [
            'telegram_chat_id', 'telegram_username', 'telegram_enabled',
            'telegram_linked_at', 'telegram_linked',
            'email_enabled', 'email_mode',
            'push_enabled', 'push_subscription',
            'quiet_hours_enabled', 'quiet_hours_start', 'quiet_hours_end',
            'timezone', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'telegram_chat_id', 'telegram_username', 'telegram_linked_at',
            'created_at', 'updated_at'
        ]

    def get_telegram_linked(self, obj):
        return bool(obj.telegram_chat_id)


class TelegramLinkResponseSerializer(serializers.Serializer):
    link = serializers.URLField()
    token = serializers.CharField()
    expires_at = serializers.DateTimeField()


class TelegramStatusSerializer(serializers.Serializer):
    linked = serializers.BooleanField()
    username = serializers.CharField(allow_null=True, allow_blank=True)
    linked_at = serializers.DateTimeField(allow_null=True)


class PushSubscriptionSerializer(serializers.Serializer):
    endpoint = serializers.URLField()
    keys = serializers.DictField(child=serializers.CharField())
