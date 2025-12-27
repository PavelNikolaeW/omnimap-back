"""Тесты для эндпойнта списка пользователей."""

import pytest
from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def regular_user(db):
    """Создаёт обычного пользователя."""
    return User.objects.create_user(
        username="regular_user",
        email="regular@test.com",
        password="testpass123"
    )


@pytest.fixture
def admin_user(db):
    """Создаёт пользователя-администратора."""
    return User.objects.create_user(
        username="admin_user",
        email="admin@test.com",
        password="testpass123",
        is_staff=True
    )


@pytest.fixture
def superuser(db):
    """Создаёт суперпользователя."""
    return User.objects.create_superuser(
        username="super_user",
        email="super@test.com",
        password="testpass123"
    )


@pytest.fixture
def auth_client(regular_user):
    """Аутентифицированный клиент (обычный пользователь)."""
    client = APIClient()
    client.force_authenticate(user=regular_user)
    return client


@pytest.fixture
def admin_client(admin_user):
    """Аутентифицированный клиент (администратор)."""
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def super_client(superuser):
    """Аутентифицированный клиент (суперпользователь)."""
    client = APIClient()
    client.force_authenticate(user=superuser)
    return client


@pytest.mark.django_db
class TestUserListView:
    """Тесты для GET /api/v1/users/."""

    def test_unauthenticated_returns_401(self, db):
        """Неаутентифицированный запрос возвращает 401."""
        client = APIClient()
        url = reverse("api:users-list")
        response = client.get(url)

        assert response.status_code == 401

    def test_regular_user_returns_403(self, auth_client):
        """Обычный пользователь получает 403."""
        url = reverse("api:users-list")
        response = auth_client.get(url)

        assert response.status_code == 403
        assert response.data["detail"] == "Admin access required"

    def test_admin_user_returns_users_list(self, admin_client, regular_user, admin_user):
        """Администратор получает список пользователей с пагинацией."""
        url = reverse("api:users-list")
        response = admin_client.get(url)

        assert response.status_code == 200
        # Проверяем структуру пагинированного ответа
        assert "count" in response.data
        assert "results" in response.data
        assert "next" in response.data
        assert "previous" in response.data

        assert response.data["count"] >= 2  # admin + regular

        usernames = [u["username"] for u in response.data["results"]]
        assert "admin_user" in usernames
        assert "regular_user" in usernames

    def test_superuser_returns_users_list(self, super_client, superuser):
        """Суперпользователь получает список пользователей."""
        url = reverse("api:users-list")
        response = super_client.get(url)

        assert response.status_code == 200
        assert "results" in response.data
        assert response.data["count"] >= 1

    def test_user_fields_in_response(self, admin_client, admin_user):
        """Ответ содержит нужные поля пользователя."""
        url = reverse("api:users-list")
        response = admin_client.get(url)

        assert response.status_code == 200
        assert len(response.data["results"]) > 0

        user_data = response.data["results"][0]
        assert "id" in user_data
        assert "username" in user_data
        assert "email" in user_data
        assert "is_active" in user_data
        assert "is_staff" in user_data
        assert "date_joined" in user_data

        # Пароль НЕ должен быть в ответе
        assert "password" not in user_data

    def test_returns_all_users_count(self, admin_client, admin_user):
        """Эндпойнт возвращает правильное количество пользователей."""
        # Создаём дополнительных пользователей
        for i in range(5):
            User.objects.create_user(
                username=f"user_{i}",
                email=f"user_{i}@test.com",
                password="testpass"
            )

        url = reverse("api:users-list")
        response = admin_client.get(url)

        assert response.status_code == 200
        # admin + 5 новых = минимум 6
        assert response.data["count"] >= 6

    def test_pagination_page_size(self, admin_client, admin_user):
        """Проверяем работу параметра page_size."""
        # Создаём 10 пользователей
        for i in range(10):
            User.objects.create_user(
                username=f"user_{i}",
                email=f"user_{i}@test.com",
                password="testpass"
            )

        url = reverse("api:users-list")
        response = admin_client.get(url, {"page_size": 5})

        assert response.status_code == 200
        assert len(response.data["results"]) == 5
        assert response.data["count"] >= 11  # admin + 10

    def test_pagination_page_navigation(self, admin_client, admin_user):
        """Проверяем навигацию по страницам."""
        # Создаём 10 пользователей
        for i in range(10):
            User.objects.create_user(
                username=f"user_{i}",
                email=f"user_{i}@test.com",
                password="testpass"
            )

        url = reverse("api:users-list")

        # Первая страница
        response = admin_client.get(url, {"page_size": 5, "page": 1})
        assert response.status_code == 200
        assert len(response.data["results"]) == 5
        assert response.data["next"] is not None
        assert response.data["previous"] is None

        # Вторая страница
        response = admin_client.get(url, {"page_size": 5, "page": 2})
        assert response.status_code == 200
        assert len(response.data["results"]) == 5
        assert response.data["previous"] is not None

    def test_users_ordered_by_date_joined_desc(self, admin_client, admin_user):
        """Пользователи отсортированы по дате регистрации (новые первые)."""
        # Создаём пользователей
        User.objects.create_user(username="first_user", password="test")
        User.objects.create_user(username="second_user", password="test")
        User.objects.create_user(username="third_user", password="test")

        url = reverse("api:users-list")
        response = admin_client.get(url)

        assert response.status_code == 200
        results = response.data["results"]

        # Новые пользователи должны быть первыми
        # third_user создан последним, значит должен быть первым в списке
        assert results[0]["username"] == "third_user"
