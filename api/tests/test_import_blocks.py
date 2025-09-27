import uuid

from django.test import TestCase
from django.contrib.auth import get_user_model

from api.models import Block, BlockPermission, Group, BlockLink
from api.services.import_blocks import import_blocks, DEFAULT_CREATOR_PERMISSION


User = get_user_model()


class ImportBlocksTests(TestCase):
    def setUp(self):
        # Базовые сущности
        self.owner = User.objects.create_user(username="owner", password="x")
        self.u1 = User.objects.create_user(username="u1")
        self.u2 = User.objects.create_user(username="u2")
        self.u3 = User.objects.create_user(username="u3")

        self.g1 = Group.objects.create(name="G1", owner=self.owner)
        self.g1.users.add(self.u1, self.u2)

        # Родители и ребёнок
        self.parentA = Block.objects.create(
            id=uuid.uuid4(),
            title="ParentA",
            data={"childOrder": []},
            creator=self.owner,
        )
        self.parentB = Block.objects.create(
            id=uuid.uuid4(),
            title="ParentB",
            data={"childOrder": []},
            creator=self.owner,
        )

        self.child = Block.objects.create(
            id=uuid.uuid4(),
            title="Child",
            data={},
            creator=self.owner,
            parent=self.parentA,
        )
        # Изначальный порядок детей у A
        self.parentA.data = {"childOrder": [str(self.child.id)]}
        self.parentA.save(update_fields=["data"])

    # --- Родитель/цикл ---

    def test_existing_block_with_external_parent_is_preserved(self):
        """Импортируем только CHILD, явно указывая parent_id внешнего родителя (не в payload). Родитель должен сохраниться."""
        payload = [{
            "id": str(self.child.id),
            "title": "Child updated",
            "data": {"text": "x"},
            "parent_id": str(self.parentA.id),   # внешний к партии, но есть в БД
            "links": [],
            "permissions": {},
            "updated_at": "2025-09-21T10:00:00Z",
        }]
        rep = import_blocks(payload, default_creator=self.owner)

        self.child.refresh_from_db()
        self.assertEqual(self.child.parent_id, self.parentA.id)
        self.assertEqual(self.child.title, "Child updated")
        self.assertFalse(rep.problem_blocks, f"unexpected problems: {rep.problem_blocks}")

    def test_parent_not_found_does_not_clear_existing_parent(self):
        """Если указали несуществующий parent_id, мы НЕ обнуляем parent и фиксируем problem_blocks."""
        unknown = uuid.uuid4()
        payload = [{
            "id": str(self.child.id),
            "title": "keep",
            "data": {},
            "parent_id": str(unknown),   # не существует
            "links": [],
            "permissions": {},
            "updated_at": "2025-09-21T10:00:00Z",
        }]
        rep = import_blocks(payload, default_creator=self.owner)

        self.child.refresh_from_db()
        self.assertEqual(self.child.parent_id, self.parentA.id, "parent must be preserved")
        codes = [p.code for p in (rep.problem_blocks or [])]
        self.assertIn("parent_not_found", codes)

    def test_cycle_detection_inside_payload(self):
        """Два блока в партии образуют цикл A<-B и A->B. Оба в problem_blocks с cycle_detected, parent не меняем."""
        a = Block.objects.create(id=uuid.uuid4(), title="A", data={}, creator=self.owner)
        b = Block.objects.create(id=uuid.uuid4(), title="B", data={}, creator=self.owner)

        payload = [
            {"id": str(a.id), "title": "A", "data": {}, "parent_id": str(b.id), "links": [], "permissions": {}},
            {"id": str(b.id), "title": "B", "data": {}, "parent_id": str(a.id), "links": [], "permissions": {}},
        ]
        rep = import_blocks(payload, default_creator=self.owner)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNone(a.parent_id)
        self.assertIsNone(b.parent_id)

        ids_in_pb = {p.block_id for p in rep.problem_blocks}
        codes = {p.code for p in rep.problem_blocks}
        self.assertIn(str(a.id), ids_in_pb)
        self.assertIn(str(b.id), ids_in_pb)
        self.assertIn("cycle_detected", codes)

    # --- Ссылки ---

    def test_links_to_external_block_created(self):
        """Ссылка на блок, которого нет в payload, но он есть в БД — должна создаться."""
        other = Block.objects.create(id=uuid.uuid4(), title="Other", data={}, creator=self.owner)
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(other.id)],    # внешний target
            "permissions": {},
        }]
        rep = import_blocks(payload, default_creator=self.owner)

        self.assertTrue(
            BlockLink.objects.filter(source_id=self.child.id, target_id=other.id).exists()
        )
        self.assertGreaterEqual(rep.links_upserted, 1)

        # Проверим что при модификации только ссылок блок не попадает в unchanged
        # (т.к. touched_ids учитывает изменения ссылок).
        self.assertEqual(rep.unchanged, 0)

    # --- Права ---

    def test_default_creator_permission_granted_for_new_block(self):
        """Новый блок должен получить дефолтное право для создателя, если creator_id явно не передали."""
        nb_id = uuid.uuid4()
        payload = [{
            "id": str(nb_id),
            "title": "NB",
            "data": {},
            "parent_id": None,
            "permissions": {},
        }]
        rep = import_blocks(payload, default_creator=self.owner)
        self.assertTrue(Block.objects.filter(id=nb_id).exists())
        self.assertTrue(
            BlockPermission.objects.filter(
                block_id=nb_id, user_id=self.owner.id, permission=DEFAULT_CREATOR_PERMISSION
            ).exists()
        )

    def test_group_permissions_expand_to_users(self):
        """Раздача прав группе должна разворачиваться в права пользователей группы."""
        nb_id = uuid.uuid4()
        payload = [{
            "id": str(nb_id),
            "title": "NB",
            "data": {},
            "parent_id": None,
            "permissions": {
                "groups": [{"group_id": self.g1.id, "permission": "view"}]
            },
        }]
        rep = import_blocks(payload, default_creator=self.owner)

        self.assertTrue(Block.objects.filter(id=nb_id).exists())
        self.assertTrue(
            BlockPermission.objects.filter(block_id=nb_id, user_id=self.u1.id, permission="view").exists()
        )
        self.assertTrue(
            BlockPermission.objects.filter(block_id=nb_id, user_id=self.u2.id, permission="view").exists()
        )

    # --- childOrder ---

    def test_childOrder_add_new_child(self):
        """Новый ребёнок, получивший parent_id, добавляется в конец parent.data.childOrder (без дублей)."""
        new_child_id = uuid.uuid4()
        payload = [{
            "id": str(new_child_id),
            "title": "Child2",
            "data": {},
            "parent_id": str(self.parentA.id),
            "permissions": {},
        }]
        import_blocks(payload, default_creator=self.owner)

        self.parentA.refresh_from_db()
        order = (self.parentA.data or {}).get("childOrder", [])
        self.assertEqual(order, [str(self.child.id), str(new_child_id)])

    def test_childOrder_move_between_parents(self):
        """Перенос ребёнка с A на B: удаляется из childOrder A, добавляется в конец childOrder B."""
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentB.id),  # перенос
            "permissions": {},
        }]
        import_blocks(payload, default_creator=self.owner)

        self.parentA.refresh_from_db()
        self.parentB.refresh_from_db()
        orderA = (self.parentA.data or {}).get("childOrder", [])
        orderB = (self.parentB.data or {}).get("childOrder", [])

        self.assertNotIn(str(self.child.id), orderA)
        self.assertEqual(orderB, [str(self.child.id)])

    def test_childOrder_remove_when_parent_null(self):
        """Если parent_id становится null — ребёнок удаляется из childOrder старого родителя."""
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": None,  # снимаем родителя
            "permissions": {},
        }]
        import_blocks(payload, default_creator=self.owner)

        self.child.refresh_from_db()
        self.parentA.refresh_from_db()
        self.assertIsNone(self.child.parent_id)

        orderA = (self.parentA.data or {}).get("childOrder", [])
        self.assertNotIn(str(self.child.id), orderA)
