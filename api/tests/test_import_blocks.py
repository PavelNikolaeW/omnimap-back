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

        link_block = Block.objects.filter(parent_id=self.child.id, data__view='link').first()
        self.assertIsNotNone(link_block)
        self.assertEqual(link_block.data.get('target'), str(other.id))
        self.assertEqual(link_block.data.get('source'), str(other.id))

        self.child.refresh_from_db()
        self.assertIn(str(link_block.id), self.child.data.get('childOrder', []))

        self.assertTrue(
            BlockLink.objects.filter(source_id=other.id, target_id=link_block.id).exists()
        )
        self.assertGreaterEqual(rep.links_upserted, 1)

        # Проверим что при модификации только ссылок блок не попадает в unchanged
        # (т.к. touched_ids учитывает изменения ссылок).
        self.assertEqual(rep.unchanged, 0)

    def test_links_skip_missing_target(self):
        """Ссылка на несуществующий блок игнорируется и не создаётся."""
        missing_id = uuid.uuid4()
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(missing_id)],
            "permissions": {},
        }]

        rep = import_blocks(payload, default_creator=self.owner)

        self.assertFalse(
            Block.objects.filter(parent_id=self.child.id, data__view='link').exists(),
            "link block must not be created for missing target",
        )
        self.assertEqual(rep.links_upserted, 0)
        self.assertFalse(rep.problem_blocks, f"unexpected problems: {rep.problem_blocks}")

    def test_links_to_block_from_payload_created(self):
        """Если target отсутствует в БД, но присутствует в payload, то ссылка создаётся."""
        target_id = uuid.uuid4()
        payload = [
            {
                "id": str(self.child.id),
                "title": "Child",
                "data": {},
                "parent_id": str(self.parentA.id),
                "links": [str(target_id)],
                "permissions": {},
            },
            {
                "id": str(target_id),
                "title": "New target",
                "data": {},
                "parent_id": None,
                "links": [],
                "permissions": {},
            },
        ]

        rep = import_blocks(payload, default_creator=self.owner)

        self.assertTrue(Block.objects.filter(id=target_id).exists())
        link_block = Block.objects.filter(parent_id=self.child.id, data__view='link').first()
        self.assertIsNotNone(link_block)
        self.assertEqual(link_block.data.get('target'), str(target_id))
        self.assertEqual(link_block.data.get('source'), str(target_id))
        self.assertTrue(
            BlockLink.objects.filter(source_id=target_id, target_id=link_block.id).exists(),
            "link to target created in the same payload must be created",
        )
        self.assertGreaterEqual(rep.links_upserted, 1)

    def test_links_skip_duplicate_for_same_target(self):
        """Если ссылка на target уже существует у родителя, новую не создаём."""
        other = Block.objects.create(id=uuid.uuid4(), title="Other", data={}, creator=self.owner)
        existing_link = Block.objects.create(
            creator=self.owner,
            parent=self.child,
            data={"view": "link", "target": str(other.id)},
        )
        BlockLink.objects.create(source=other, target=existing_link)

        data = self.child.data or {}
        data["childOrder"] = [str(existing_link.id)]
        self.child.data = data
        self.child.save(update_fields=["data"])

        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(other.id)],
            "permissions": {},
        }]

        rep = import_blocks(payload, default_creator=self.owner)

        self.assertEqual(Block.objects.filter(parent_id=self.child.id, data__view='link').count(), 1)
        self.assertEqual(rep.links_upserted, 0)

    def test_link_block_inherits_permissions_from_parent(self):
        """Созданный линк-блок должен получить права родителя."""
        other = Block.objects.create(id=uuid.uuid4(), title="Other", data={}, creator=self.owner)
        BlockPermission.objects.create(block=self.child, user=self.u1, permission="view")
        BlockPermission.objects.create(block=self.child, user=self.u2, permission="edit")

        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(other.id)],
            "permissions": {},
        }]

        import_blocks(payload, default_creator=self.owner)

        link_block = Block.objects.filter(parent_id=self.child.id, data__view="link").first()
        self.assertIsNotNone(link_block)

        self.assertTrue(
            BlockPermission.objects.filter(block=link_block, user=self.u1, permission="view").exists()
        )
        self.assertTrue(
            BlockPermission.objects.filter(block=link_block, user=self.u2, permission="edit").exists()
        )

    def test_links_ignore_self_target(self):
        """Ссылки на самого себя игнорируются."""
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(self.child.id)],
            "permissions": {},
        }]

        rep = import_blocks(payload, default_creator=self.owner)

        self.assertEqual(Block.objects.filter(parent=self.child, data__view="link").count(), 0)
        self.assertEqual(rep.links_upserted, 0)

    def test_links_collapse_duplicates_from_payload(self):
        """Повторяющиеся target в списке links создают только одну ссылку."""
        other = Block.objects.create(id=uuid.uuid4(), title="Other", data={}, creator=self.owner)
        payload = [{
            "id": str(self.child.id),
            "title": "Child",
            "data": {},
            "parent_id": str(self.parentA.id),
            "links": [str(other.id), str(other.id)],
            "permissions": {},
        }]

        rep = import_blocks(payload, default_creator=self.owner)

        link_blocks = Block.objects.filter(parent=self.child, data__view="link")
        self.assertEqual(link_blocks.count(), 1)
        link_block = link_blocks.first()

        self.child.refresh_from_db()
        child_order = (self.child.data or {}).get("childOrder", [])
        self.assertEqual(child_order.count(str(link_block.id)), 1)

        self.assertEqual(
            BlockLink.objects.filter(source_id=other.id, target_id=link_block.id).count(),
            1,
        )
        self.assertEqual(rep.links_upserted, 1)

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

    def test_childOrder_remove_unused_child_deletes_block(self):
        """Удалённый из childOrder блок удаляется, если больше нигде не используется."""
        payload = [{
            "id": str(self.parentA.id),
            "title": self.parentA.title,
            "data": {"childOrder": []},
            "permissions": {},
        }]

        import_blocks(payload, default_creator=self.owner)

        self.assertFalse(Block.objects.filter(id=self.child.id).exists())
        self.parentA.refresh_from_db()
        orderA = (self.parentA.data or {}).get("childOrder", [])
        self.assertEqual(orderA, [])
