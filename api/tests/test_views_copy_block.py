from pprint import pprint

from django.test import override_settings
import pytest
import uuid6
from rest_framework.test import APIClient
from django.urls import reverse
from api.models import Block
from django.contrib.auth import get_user_model

from api.views import get_flat_map

User = get_user_model()


@pytest.fixture
def user(db):
    """Фикстура для создания пользователя."""
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def auth_client(user):
    """Фикстура для аутентифицированного клиента."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def block_hierarchy(user):
    """Фикстура для создания иерархии блоков."""
    dest = Block.objects.create(
        creator=user, title="Parent Block", data={"example": "data"}
    )
    src = Block.objects.create(
        creator=user, title="Child Block", data={"childData": "value"}
    )
    grandchild1 = Block.objects.create(creator=user, title="grandchild 1")
    grandchild2 = Block.objects.create(creator=user, title="Grandchild 2",
                                       data={"view": "iframe",
                                             "attributes": [{"name": "sandbox",
                                                             "value": "allow-scripts allow-same-origin allow-forms"},
                                                            {"name": "src",
                                                             "value": "https://example.com"}],
                                             "childOrder": [],
                                             "titleIsVisible": False})

    child1 = Block.objects.create(creator=user, title="Child 1",
                                  data={
                                      "childOrder": [
                                          str(grandchild1.id),
                                          str(grandchild2.id)
                                      ],
                                      "customGrid": {
                                          "grid": [
                                              "grid-template-columns_1fr__1fr__1fr__1fr__1fr__",
                                              "grid-template-rows_auto__1fr__1fr__"
                                          ],
                                          "contentPosition": [
                                              "grid-column_1_sl_5"
                                          ],
                                          "childrenPositions": {
                                              str(grandchild1.id): [
                                                  "grid-column_1__3",
                                                  "grid-row_2__3"
                                              ],
                                              str(grandchild2.id): [
                                                  "grid-column_3__5",
                                                  "grid-row_2__4"
                                              ],
                                          }
                                      }
                                  })
    child2 = Block.objects.create(creator=user, title="Child 2")

    link_src = Block.objects.create(creator=user, title="link src",
                                    data={"example": "data"})
    link = Block.objects.create(creator=user,
                                data={"view": "link", "source": str(link_src.id), "childOrder": [],
                                      "titleIsVisible": False})
    grandChild3 = Block.objects.create(creator=user, title="grandchild3")
    child1.add_children([grandchild1, grandchild2, grandChild3])
    child2.add_child(link)
    src.add_children([child1, child2])
    return dest, src


@pytest.mark.django_db
def test_copy_block_hierarchy(auth_client, block_hierarchy, user):
    """Тест копирования блока с проверкой сохранения структуры и данных."""
    dest, src = block_hierarchy
    pprint(get_flat_map(user.id, [str(src.id)]))
    url = reverse("api:copy-block")

    response = auth_client.post(
        url, {"src": [src.id], "dest": dest.id}, format="json"
    )
    assert response.status_code == 200

    copied_hierarchy = response.data
    copied_block_id = copied_hierarchy[str(dest.id)]["children"][-1]

    assert str(src.id) != copied_block_id
    src_data = get_flat_map(user.id, [str(src.id)])
    copied_data = get_flat_map(user.id, [str(copied_block_id)])
    # todo сделать сравнение структуры словарей
    pprint(src_data)
    print('www')
    pprint(copied_data)


@pytest.fixture
def extended_block_hierarchy(user):
    """Фикстура для создания расширенной иерархии блоков.
    TODO запилить более сложную иерархию блоков"""
    dest = Block.objects.create(
        creator=user, title="Parent Block", data={"example": "data"}
    )
    src1 = Block.objects.create(
        creator=user, title="Source Block 1", data={"key": "value1"}
    )
    src2 = Block.objects.create(
        creator=user, title="Source Block 2", data={"key": "value2"}
    )
    child1 = Block.objects.create(creator=user, title="Child 1")
    child2 = Block.objects.create(creator=user, title="Child 2")

    src1.add_child(child1)
    src2.add_child(child2)

    return dest, [src1, src2]


@pytest.mark.django_db
def test_copy_multiple_ids(auth_client, extended_block_hierarchy, block_hierarchy, user):
    """Тест копирования нескольких блоков с проверкой структуры."""
    dest, sources = extended_block_hierarchy

    src_ids = [str(src.id) for src in sources]
    src_ids.append(str(dest.id))

    # Отправляем запрос на копирование
    url = reverse("api:copy-block")
    response = auth_client.post(
        url, {"src": src_ids, "dest": dest.id}, format="json"
    )

    # Проверяем, что запрос успешен
    assert response.status_code == 200

    copied_hierarchy = response.data

    assert src_ids != copied_hierarchy[str(dest.id)]['children']
    src_data = get_flat_map(user.id, src_ids)
    copied_data = get_flat_map(user.id, copied_hierarchy[str(dest.id)]['children'])
    # pprint(src_data)
    # print('WWWWWWWwwwWWWWWWWWWWWWW')
    # pprint(copied_data)


@pytest.mark.django_db
@override_settings(LIMIT_BLOCKS=2)
def test_copy_exceeds_limit(auth_client, block_hierarchy, user):
    """Тест превышения лимита на количество копируемых блоков."""
    dest, src = block_hierarchy
    url = reverse("api:copy-block")
    response = auth_client.post(url, {"src": [src.id], "dest": dest.id}, format="json")
    pprint(response.data)
    assert response.status_code == 400
    assert response.data['error'] == "Limit is exceeded"


@pytest.mark.django_db
def test_copy_with_empty_src(auth_client, block_hierarchy):
    """Тест с пустым списком источников."""
    dest, _ = block_hierarchy
    url = reverse("api:copy-block")
    response = auth_client.post(url, {"src": [], "dest": dest.id}, format="json")
    assert response.status_code == 400
    assert response.data['error'] == 'Src not found or forbidden'


@pytest.mark.django_db
def test_copy_with_invalid_src(auth_client, block_hierarchy):
    """Тест с несуществующим источником."""
    dest, _ = block_hierarchy
    invalid_id = "nonexistent-id"
    url = reverse("api:copy-block")
    response = auth_client.post(url, {"src": [invalid_id], "dest": dest.id}, format="json")
    assert response.status_code == 400
    assert response.data['error'] == 'Invalid UUIDs: nonexistent-id'


@pytest.mark.django_db
def test_copy_without_permission(block_hierarchy):
    """Тест копирования без прав доступа."""
    dest, src = block_hierarchy
    another_user = User.objects.create_user(username="another", password="testpass")
    client = APIClient()
    client.force_authenticate(user=another_user)
    url = reverse("api:copy-block")
    response = client.post(url, {"src": [src.id], "dest": dest.id}, format="json")
    assert response.status_code == 403


def deep_compare_without_uuid(obj1, obj2):
    if isinstance(obj1, dict) and isinstance(obj2, dict):
        keys1 = {key for key in obj1 if not is_uuid(key)}
        keys2 = {key for key in obj2 if not is_uuid(key)}
        if keys1 != keys2:
            return False

        for key in keys1:
            if not deep_compare_without_uuid(obj1[key], obj2[key]):
                return False

    elif isinstance(obj1, list) and isinstance(obj2, list):
        if len(obj1) != len(obj2):
            return False

        for item1, item2 in zip(obj1, obj2):
            if not deep_compare_without_uuid(item1, item2):
                return False

    elif isinstance(obj1, str) and isinstance(obj2, str):
        if is_uuid(obj1) or is_uuid(obj2):
            return True
        return obj1 == obj2

    else:
        return obj1 == obj2

    return True


def is_uuid(value):
    """
    Проверяет, является ли значение UUID.
    """
    if isinstance(value, str):
        try:
            uuid6.UUID(value)
            return True
        except ValueError:
            return False
    return False
