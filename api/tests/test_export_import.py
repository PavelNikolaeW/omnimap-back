"""Тесты для export → import roundtrip."""

import uuid
import pytest
from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth import get_user_model

from api.models import Block, BlockPermission
from api.services.import_blocks import import_blocks

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
def tree_structure(user):
    """
    Создаёт структуру дерева:
    root
    ├── child1
    │   ├── grandchild1
    │   └── grandchild2
    └── child2
        └── grandchild3
    """
    root = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Root Block",
        data={"key": "root_value"}
    )
    BlockPermission.objects.create(block=root, user=user, permission="delete")

    child1 = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Child 1",
        data={"key": "child1_value"},
        parent=root
    )
    BlockPermission.objects.create(block=child1, user=user, permission="edit")

    child2 = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Child 2",
        data={"key": "child2_value"},
        parent=root
    )
    BlockPermission.objects.create(block=child2, user=user, permission="edit")

    grandchild1 = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Grandchild 1",
        data={},
        parent=child1
    )
    BlockPermission.objects.create(block=grandchild1, user=user, permission="view")

    grandchild2 = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Grandchild 2",
        data={"nested": {"data": True}},
        parent=child1
    )
    BlockPermission.objects.create(block=grandchild2, user=user, permission="view")

    grandchild3 = Block.objects.create(
        id=uuid.uuid4(),
        creator=user,
        title="Grandchild 3",
        data={},
        parent=child2
    )
    BlockPermission.objects.create(block=grandchild3, user=user, permission="view")

    # Устанавливаем childOrder
    root.data["childOrder"] = [str(child1.id), str(child2.id)]
    root.save()

    child1.data["childOrder"] = [str(grandchild1.id), str(grandchild2.id)]
    child1.save()

    child2.data["childOrder"] = [str(grandchild3.id)]
    child2.save()

    return {
        "root": root,
        "child1": child1,
        "child2": child2,
        "grandchild1": grandchild1,
        "grandchild2": grandchild2,
        "grandchild3": grandchild3,
    }


# ==================== Export Tests ====================

@pytest.mark.django_db
class TestExportBlocks:
    """Тесты для endpoint export_blocks."""

    def test_export_single_block_without_children(self, auth_client, tree_structure):
        """Экспорт одного блока без дочерних."""
        root = tree_structure["root"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(root.id)],
            "include_children": False
        }, format="json")

        assert response.status_code == 200
        assert response.data["total"] == 1
        assert len(response.data["blocks"]) == 1
        assert response.data["blocks"][0]["id"] == str(root.id)
        assert response.data["blocks"][0]["title"] == "Root Block"

    def test_export_with_children(self, auth_client, tree_structure):
        """Экспорт блока с дочерними."""
        root = tree_structure["root"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(root.id)],
            "include_children": True
        }, format="json")

        assert response.status_code == 200
        # root + child1 + child2 + grandchild1 + grandchild2 + grandchild3 = 6
        assert response.data["total"] == 6

        exported_ids = {b["id"] for b in response.data["blocks"]}
        expected_ids = {str(b.id) for b in tree_structure.values()}
        assert exported_ids == expected_ids

    def test_export_with_permissions(self, auth_client, tree_structure):
        """Экспорт с включением прав доступа."""
        root = tree_structure["root"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(root.id)],
            "include_children": False,
            "include_permissions": True
        }, format="json")

        assert response.status_code == 200
        block = response.data["blocks"][0]
        assert "permissions" in block
        assert "users" in block["permissions"]
        assert len(block["permissions"]["users"]) > 0

    def test_export_preserves_data(self, auth_client, tree_structure):
        """Экспорт сохраняет данные блоков."""
        grandchild2 = tree_structure["grandchild2"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(grandchild2.id)],
            "include_children": False
        }, format="json")

        assert response.status_code == 200
        block = response.data["blocks"][0]
        assert block["data"] == {"nested": {"data": True}}

    def test_export_forbidden_block(self, auth_client, another_user):
        """Экспорт блока без прав возвращает 403."""
        forbidden_block = Block.objects.create(
            creator=another_user,
            title="Forbidden",
            data={}
        )

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(forbidden_block.id)]
        }, format="json")

        assert response.status_code == 403


# ==================== Export → Import Roundtrip Tests ====================

@pytest.mark.django_db
class TestExportImportRoundtrip:
    """Тесты для проверки совместимости export и import."""

    def test_export_format_matches_import_requirements(self, auth_client, tree_structure, user):
        """Формат экспорта соответствует требованиям импорта."""
        root = tree_structure["root"]

        # Экспортируем
        export_url = reverse("api:export-blocks")
        export_response = auth_client.post(export_url, {
            "block_ids": [str(root.id)],
            "include_children": True
        }, format="json")

        assert export_response.status_code == 200
        exported_blocks = export_response.data["blocks"]

        # Проверяем формат каждого блока
        for block in exported_blocks:
            assert "id" in block
            assert "title" in block
            assert "data" in block
            assert "parent_id" in block
            # id должен быть строкой UUID
            assert isinstance(block["id"], str)
            # data должен быть словарём
            assert isinstance(block["data"], dict)

    def test_export_import_creates_new_blocks(self, auth_client, tree_structure, user):
        """Экспорт → изменение id → импорт создаёт новые блоки."""
        child1 = tree_structure["child1"]

        # Экспортируем child1 с детьми
        export_url = reverse("api:export-blocks")
        export_response = auth_client.post(export_url, {
            "block_ids": [str(child1.id)],
            "include_children": True
        }, format="json")

        assert export_response.status_code == 200
        exported_blocks = export_response.data["blocks"]

        # child1 + grandchild1 + grandchild2 = 3
        assert len(exported_blocks) == 3

        # Сначала генерируем все новые ID
        id_mapping = {}
        for block in exported_blocks:
            old_id = block["id"]
            new_id = str(uuid.uuid4())
            id_mapping[old_id] = new_id

        # Затем обновляем все ссылки
        for block in exported_blocks:
            old_id = block["id"]
            block["id"] = id_mapping[old_id]

            # Обновляем parent_id
            if block["parent_id"] and block["parent_id"] in id_mapping:
                block["parent_id"] = id_mapping[block["parent_id"]]
            elif block["parent_id"] == str(child1.parent_id):
                # Корневой блок экспорта — привязываем к root
                block["parent_id"] = str(tree_structure["root"].id)

            # Обновляем childOrder в data
            if "childOrder" in block["data"]:
                block["data"]["childOrder"] = [
                    id_mapping.get(cid, cid)
                    for cid in block["data"]["childOrder"]
                ]

        # Импортируем напрямую через сервис (синхронно)
        rep = import_blocks(exported_blocks, user)

        # Проверяем результат
        assert len(rep.created) == 3
        assert len(rep.problem_blocks) == 0

        # Проверяем что блоки созданы
        for new_id in id_mapping.values():
            assert Block.objects.filter(id=new_id).exists()

    def test_export_import_preserves_hierarchy(self, auth_client, tree_structure, user):
        """Экспорт → импорт сохраняет иерархию."""
        root = tree_structure["root"]

        # Экспортируем всё дерево
        export_url = reverse("api:export-blocks")
        export_response = auth_client.post(export_url, {
            "block_ids": [str(root.id)],
            "include_children": True
        }, format="json")

        exported_blocks = export_response.data["blocks"]

        # Создаём mapping старых id → новых
        id_mapping = {b["id"]: str(uuid.uuid4()) for b in exported_blocks}

        # Обновляем все id и ссылки
        for block in exported_blocks:
            old_id = block["id"]
            block["id"] = id_mapping[old_id]

            if block["parent_id"] and block["parent_id"] in id_mapping:
                block["parent_id"] = id_mapping[block["parent_id"]]
            elif block["parent_id"] is None:
                # Новый корень — делаем его корневым
                pass

            if "childOrder" in block["data"]:
                block["data"]["childOrder"] = [
                    id_mapping.get(cid, cid)
                    for cid in block["data"]["childOrder"]
                ]

        # Импортируем
        rep = import_blocks(exported_blocks, user)

        assert len(rep.created) == 6
        assert len(rep.problem_blocks) == 0

        # Проверяем иерархию
        new_root_id = id_mapping[str(root.id)]
        new_root = Block.objects.get(id=new_root_id)

        # У нового root должно быть 2 ребёнка
        children = Block.objects.filter(parent_id=new_root_id)
        assert children.count() == 2

    def test_export_import_preserves_data_fields(self, auth_client, tree_structure, user):
        """Экспорт → импорт сохраняет все поля data."""
        grandchild2 = tree_structure["grandchild2"]

        # Экспортируем один блок
        export_url = reverse("api:export-blocks")
        export_response = auth_client.post(export_url, {
            "block_ids": [str(grandchild2.id)],
            "include_children": False
        }, format="json")

        exported_blocks = export_response.data["blocks"]
        assert len(exported_blocks) == 1

        # Меняем id
        new_id = str(uuid.uuid4())
        exported_blocks[0]["id"] = new_id
        exported_blocks[0]["parent_id"] = None  # Делаем корневым

        # Импортируем
        rep = import_blocks(exported_blocks, user)

        assert len(rep.created) == 1

        # Проверяем данные
        new_block = Block.objects.get(id=new_id)
        assert new_block.data == {"nested": {"data": True}}
        assert new_block.title == "Grandchild 2"

    def test_export_import_with_permissions(self, auth_client, tree_structure, user):
        """Экспорт с permissions → импорт создаёт права."""
        # Используем листовой блок без детей, чтобы избежать проблем с childOrder
        grandchild1 = tree_structure["grandchild1"]

        # Экспортируем с правами
        export_url = reverse("api:export-blocks")
        export_response = auth_client.post(export_url, {
            "block_ids": [str(grandchild1.id)],
            "include_children": False,
            "include_permissions": True
        }, format="json")

        exported_blocks = export_response.data["blocks"]

        # Меняем id
        new_id = str(uuid.uuid4())
        exported_blocks[0]["id"] = new_id
        exported_blocks[0]["parent_id"] = None

        # Преобразуем формат permissions для import_blocks
        # Export возвращает {"users": [...]}, а import ожидает [...]
        if "permissions" in exported_blocks[0] and "users" in exported_blocks[0]["permissions"]:
            exported_blocks[0]["permissions"] = exported_blocks[0]["permissions"]["users"]

        # Импортируем
        rep = import_blocks(exported_blocks, user)

        # Проверяем что нет проблем
        assert len(rep.problem_blocks) == 0, f"Problems: {rep.problem_blocks}"
        assert len(rep.created) == 1

        # Проверяем права
        new_block = Block.objects.get(id=new_id)
        perms = BlockPermission.objects.filter(block=new_block)
        assert perms.exists()


# ==================== Edge Cases ====================

@pytest.mark.django_db
class TestExportImportEdgeCases:
    """Тесты граничных случаев."""

    def test_export_empty_block_ids(self, auth_client):
        """Экспорт с пустым списком block_ids возвращает 400."""
        url = reverse("api:export-blocks")
        response = auth_client.post(url, {"block_ids": []}, format="json")

        assert response.status_code == 400

    def test_export_nonexistent_block(self, auth_client):
        """Экспорт несуществующего блока возвращает 403 (нет прав)."""
        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(uuid.uuid4())]
        }, format="json")

        assert response.status_code == 403

    def test_export_max_depth_limit(self, auth_client, tree_structure):
        """Экспорт с max_depth=1 ограничивает глубину."""
        root = tree_structure["root"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(root.id)],
            "include_children": True,
            "max_depth": 1
        }, format="json")

        assert response.status_code == 200
        # С depth=1 получим только root
        assert response.data["total"] == 1

    def test_export_deduplicates_blocks(self, auth_client, tree_structure):
        """Экспорт нескольких блоков с общими детьми дедуплицирует результат."""
        root = tree_structure["root"]
        child1 = tree_structure["child1"]

        url = reverse("api:export-blocks")
        response = auth_client.post(url, {
            "block_ids": [str(root.id), str(child1.id)],
            "include_children": True
        }, format="json")

        assert response.status_code == 200
        # Должно быть 6 уникальных блоков, не больше
        assert response.data["total"] == 6

        # Проверяем уникальность
        ids = [b["id"] for b in response.data["blocks"]]
        assert len(ids) == len(set(ids))
