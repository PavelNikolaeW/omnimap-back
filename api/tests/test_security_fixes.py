"""Тесты для security-исправлений в views."""

import pytest
from unittest.mock import patch, MagicMock
from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import Block, BlockPermission, BlockUrlLinkModel

User = get_user_model()


# ==================== Fixtures ====================

@pytest.fixture
def user(db):
    """Создаёт тестового пользователя."""
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def another_user(db):
    """Создаёт второго пользователя."""
    return User.objects.create_user(username="anotheruser", password="testpass")


@pytest.fixture
def auth_client(user):
    """Аутентифицированный клиент."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def another_auth_client(another_user):
    """Аутентифицированный клиент для второго пользователя."""
    client = APIClient()
    client.force_authenticate(user=another_user)
    return client


@pytest.fixture
def anon_client():
    """Анонимный клиент."""
    return APIClient()


@pytest.fixture
def block(user):
    """Создаёт блок с правами для пользователя."""
    block = Block.objects.create(creator=user, title="Test Block", data={})
    BlockPermission.objects.create(block=block, user=user, permission="delete")
    return block


@pytest.fixture
def block_with_slug(user, block):
    """Создаёт блок с публичным slug."""
    BlockUrlLinkModel.objects.create(block=block, slug="test-slug")
    return block


@pytest.fixture
def deep_tree(user):
    """Создаёт глубокое дерево для тестирования лимита глубины."""
    root = Block.objects.create(creator=user, title="Root", data={})
    BlockPermission.objects.create(block=root, user=user, permission="delete")

    current = root
    for i in range(60):  # Создаём дерево глубже MAX_SUBTREE_DEPTH (50)
        child = Block.objects.create(
            creator=user,
            title=f"Level {i+1}",
            data={},
            parent=current
        )
        BlockPermission.objects.create(block=child, user=user, permission="view")
        current.data = {"childOrder": [str(child.id)]}
        current.save()
        current = child

    return root


# ==================== TaskStatusView IDOR Tests ====================

@pytest.mark.django_db
class TestTaskStatusViewIDOR:
    """Тесты IDOR защиты для TaskStatusView."""

    @patch('api.views.AsyncResult')
    @patch('api.utils.task_utils.get_task_owner')
    def test_owner_can_view_task_status(self, mock_get_owner, mock_async, auth_client, user):
        """Владелец задачи может просмотреть её статус."""
        mock_get_owner.return_value = user.id
        mock_async.return_value.status = 'SUCCESS'
        mock_async.return_value.result = {'done': True}

        url = reverse("api:task_status", args=["test-task-id"])
        response = auth_client.get(url)

        assert response.status_code == 200
        assert response.data['task_id'] == "test-task-id"

    @patch('api.utils.task_utils.get_task_owner')
    def test_non_owner_cannot_view_task_status(self, mock_get_owner, another_auth_client, user):
        """Не-владелец не может просмотреть статус чужой задачи."""
        mock_get_owner.return_value = user.id  # Владелец - user, а запрос от another_user

        url = reverse("api:task_status", args=["test-task-id"])
        response = another_auth_client.get(url)

        assert response.status_code == 403
        assert "denied" in response.data.get("detail", "").lower() or response.status_code == 403

    @patch('api.utils.task_utils.get_task_owner')
    def test_unknown_task_allowed_for_owner_check_bypass(self, mock_get_owner, auth_client):
        """Задача без owner в Redis возвращает статус (legacy behavior)."""
        mock_get_owner.return_value = None

        url = reverse("api:task_status", args=["unknown-task-id"])
        response = auth_client.get(url)

        # Когда owner не найден в Redis, текущая реализация возвращает статус
        # (для обратной совместимости с задачами созданными до добавления IDOR защиты)
        assert response.status_code == 200

    def test_unauthenticated_user_cannot_access(self, anon_client):
        """Неаутентифицированный пользователь не может получить статус задачи."""
        url = reverse("api:task_status", args=["test-task-id"])
        response = anon_client.get(url)

        assert response.status_code == 401


# ==================== BlockSearchAPIView Depth Limit Tests ====================

@pytest.mark.django_db
class TestBlockSearchDepthLimit:
    """Тесты лимита глубины для BlockSearchAPIView."""

    def test_search_with_shallow_tree(self, auth_client, block):
        """Поиск по неглубокому дереву работает нормально."""
        url = reverse("api:search-block")
        response = auth_client.get(url, {"q": "Test", "root": str(block.id)})

        assert response.status_code == 200

    def test_search_with_deep_tree_respects_limit(self, auth_client, deep_tree):
        """Поиск по глубокому дереву ограничен MAX_SUBTREE_DEPTH."""
        url = reverse("api:search-block")
        response = auth_client.get(url, {"q": "Level", "root": str(deep_tree.id)})

        # Запрос должен успешно завершиться без таймаута
        assert response.status_code == 200


# ==================== Slug Validation Tests ====================

@pytest.mark.django_db
class TestSlugValidation:
    """Тесты валидации slug в create_url."""

    def test_valid_slug_accepted(self, auth_client, block):
        """Валидный slug принимается."""
        url = reverse("api:create-url", args=[str(block.id)])
        response = auth_client.post(url, {"slug": "valid-slug_123"}, format="json")

        assert response.status_code == 200

    def test_slug_with_special_chars_rejected(self, auth_client, block):
        """Slug со специальными символами отклоняется."""
        url = reverse("api:create-url", args=[str(block.id)])
        response = auth_client.post(url, {"slug": "invalid@slug!"}, format="json")

        assert response.status_code == 400
        assert "slug" in response.data.get("message", "").lower()

    def test_slug_too_long_rejected(self, auth_client, block):
        """Слишком длинный slug отклоняется."""
        url = reverse("api:create-url", args=[str(block.id)])
        long_slug = "a" * 101
        response = auth_client.post(url, {"slug": long_slug}, format="json")

        assert response.status_code == 400

    def test_empty_slug_rejected(self, auth_client, block):
        """Пустой slug отклоняется."""
        url = reverse("api:create-url", args=[str(block.id)])
        response = auth_client.post(url, {"slug": ""}, format="json")

        assert response.status_code == 400


# ==================== edit_block Data Validation Tests ====================

@pytest.mark.django_db
class TestEditBlockValidation:
    """Тесты валидации data в edit_block."""

    def test_valid_dict_data_accepted(self, auth_client, block):
        """Валидный dict для data принимается."""
        url = reverse("api:edit-block", args=[str(block.id)])
        response = auth_client.post(url, {"data": {"key": "value"}}, format="json")

        assert response.status_code == 200

    def test_list_data_rejected(self, auth_client, block):
        """Список для data отклоняется."""
        url = reverse("api:edit-block", args=[str(block.id)])
        response = auth_client.post(url, {"data": ["item1", "item2"]}, format="json")

        assert response.status_code == 400
        assert "data" in response.data.get("detail", "").lower()

    def test_string_data_rejected(self, auth_client, block):
        """Строка для data отклоняется."""
        url = reverse("api:edit-block", args=[str(block.id)])
        response = auth_client.post(url, {"data": "string data"}, format="json")

        assert response.status_code == 400

    def test_number_data_rejected(self, auth_client, block):
        """Число для data отклоняется."""
        url = reverse("api:edit-block", args=[str(block.id)])
        response = auth_client.post(url, {"data": 12345}, format="json")

        assert response.status_code == 400


# ==================== History Views Auth Tests ====================

@pytest.mark.django_db
class TestHistoryViewsAuth:
    """Тесты аутентификации для history views."""

    def test_history_list_requires_auth(self, anon_client, block):
        """BlockHistoryListView требует аутентификации."""
        url = reverse("api:block-history-list", args=[str(block.id)])
        response = anon_client.get(url)

        assert response.status_code == 401

    def test_history_list_requires_permission(self, another_auth_client, block):
        """BlockHistoryListView требует прав на блок."""
        url = reverse("api:block-history-list", args=[str(block.id)])
        response = another_auth_client.get(url)

        assert response.status_code == 403

    def test_history_list_allowed_with_permission(self, auth_client, block):
        """BlockHistoryListView доступен при наличии прав."""
        url = reverse("api:block-history-list", args=[str(block.id)])
        response = auth_client.get(url)

        assert response.status_code == 200

    def test_undo_requires_auth(self, anon_client):
        """BlockHistoryUndoView требует аутентификации."""
        url = reverse("api:block-history-undo")
        response = anon_client.post(url, {}, format="json")

        assert response.status_code == 401

    def test_undo_authenticated_user_can_access(self, auth_client):
        """Аутентифицированный пользователь может использовать undo."""
        url = reverse("api:block-history-undo")
        # Отправляем пустой operation - endpoint должен ответить ошибкой валидации, но не 401
        response = auth_client.post(url, {"operation": {}}, format="json")

        assert response.status_code != 401


# ==================== load_nodes Deny Permission Tests ====================

@pytest.mark.django_db
class TestLoadNodesDenyPermission:
    """Тесты исключения deny permission в load_nodes."""

    def test_block_with_deny_permission_returns_forbidden(self, user, another_user):
        """Блок с правом deny возвращает 403."""
        root = Block.objects.create(creator=user, title="Root", data={})

        # Даём deny для another_user на root
        BlockPermission.objects.create(block=root, user=another_user, permission="deny")

        client = APIClient()
        client.force_authenticate(user=another_user)

        url = reverse("api:load-nodes")
        response = client.post(url, {"tree": str(root.id)}, format="json")

        # Ответ должен быть 403 так как у пользователя deny
        assert response.status_code == 403

    def test_block_with_view_permission_allowed(self, user, another_user):
        """Блок с правом view доступен."""
        root = Block.objects.create(creator=user, title="Root", data={})

        # Даём view для another_user
        BlockPermission.objects.create(block=root, user=another_user, permission="view")

        client = APIClient()
        client.force_authenticate(user=another_user)

        url = reverse("api:load-nodes")
        response = client.post(url, {"tree": str(root.id)}, format="json")

        # Ответ должен быть успешным
        assert response.status_code == 200

    def test_load_nodes_requires_tree_param(self, auth_client):
        """load_nodes требует параметр tree."""
        url = reverse("api:load-nodes")
        response = auth_client.post(url, {}, format="json")

        assert response.status_code == 400
        assert "tree" in response.data.get("detail", "").lower()


# ==================== AccessBlockView GET Permission Tests ====================

@pytest.mark.django_db
class TestAccessBlockViewPermission:
    """Тесты проверки прав для AccessBlockView.get."""

    def test_access_view_requires_edit_ac_or_delete(self, another_auth_client, block):
        """GET /access/<block_id>/ требует права edit_ac или delete."""
        url = reverse("api:access-list", args=[str(block.id)])
        response = another_auth_client.get(url)

        assert response.status_code == 403

    def test_access_view_allowed_with_delete_permission(self, auth_client, block):
        """GET /access/<block_id>/ доступен с правом delete."""
        url = reverse("api:access-list", args=[str(block.id)])
        response = auth_client.get(url)

        assert response.status_code == 200
