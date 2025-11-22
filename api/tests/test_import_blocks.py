import time
import uuid
from pprint import pprint
from unittest.mock import patch

from celery import signals
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import Block, BlockPermission, Group, BlockLink
from api.services.import_blocks import import_blocks, DEFAULT_CREATOR_PERMISSION, DummyTask

User = get_user_model()


def create_block(creator, title='', data={}, access='delete', parent_id=None, children=None):
    block = Block.objects.create(
        id=uuid.uuid4(),
        title=title,
        data=data,
        creator=creator,
        parent_id=parent_id
    )
    if children:
        block.add_children(children)
    if access:
        BlockPermission.objects.create(user=creator, permission=access, block=block)
    return block


class ImportBlocksTests(TestCase):

    def setUp(self):
        # Базовые сущности
        self.owner = User.objects.create_user(username="owner", password="x")
        self.u1 = User.objects.create_user(username="u1")
        self.u2 = User.objects.create_user(username="u2")
        self.u3 = User.objects.create_user(username="u3")

        self.g1 = Group.objects.create(name="G1", owner=self.owner)
        self.g1.users.add(self.u1, self.u2)

        self.childAA = create_block(self.owner, 'childAA', data={}, access='delete')
        self.childBB = create_block(self.owner, 'childBB', data={}, access='delete')

        self.childA = create_block(self.owner, 'childA', data={}, access='delete', children=[self.childAA])
        self.childB = create_block(self.owner, 'childB', data={}, access='delete', children=[self.childBB])

        self.parentA = create_block(self.owner, 'parentA', data={}, access='delete', children=[self.childA])
        self.parentB = create_block(self.owner, 'parentB', data={}, access='delete', children=[self.childB])

        self.block_link = create_block(self.owner, data={'view': 'link', 'source': str(self.childB.id)},
                                       access='delete')
        self.childAA.add_child(self.block_link)
        self.new_block = create_block(self.owner, 'test new_block', data={}, access='delete')
        self.childBB.add_child(self.new_block)
        self.block_non_access = create_block(self.owner, 'None access', access='')

    # --- Базовая валидация id / доступа ---

    def test_wrong_uuid(self):
        """Передали не валидный id, вернется одна ошибка not_valid_uuid"""
        payload = [{
            "id": 'kek',
        }]

        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 1)
        self.assertIn('not_valid_uuid', rep.errors)

    def test_not_access_block(self):
        """Передали блок, на который нет прав, вернется ошибка forbidden"""
        payload = [{
            'id': self.block_non_access.id
        }]

        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 1)
        self.assertIn('forbidden', rep.errors)

    # --- Циклы (существующие и новые блоки) ---

    def test_cycle_0(self):
        """Пробуем зациклить существующие блоки, самый короткий цикл, вернется ошибка cycle_detected"""
        payload = [{
            'id': self.parentA.id,
            'parent_id': self.parentA.id
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 1)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_1(self):
        """Пробуем зациклить существующие блоки, простой цикл, вернется ошибка cycle_detected"""
        payload = [{
            'id': self.parentA.id,
            'parent_id': self.childA.id
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 2)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_2(self):
        """Пробуем зациклить существующие блоки, цикл, вернется ошибка cycle_detected"""
        payload = [{
            'id': self.parentA.id,
            'parent_id': self.childAA.id
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 3)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_3(self):
        """Пробуем зациклить существующие блоки, длинный цикл, вернется ошибка cycle_detected"""
        payload = [{
            'id': self.parentB.id,
            'parent_id': self.childAA.id
        }, {
            'id': self.parentA.id,
            'parent_id': self.childBB.id
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 6)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_4(self):
        """Ищем цикл в пришедших данных (самозацикливание нового блока)"""
        new_block = uuid.uuid4()
        payload = [{
            'id': new_block,
            'title': 'new_block',
            'parent_id': new_block
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 1)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_5(self):
        """Ищем цикл в пришедших данных (2 новых блока с перекрестной ссылкой)"""
        new_block = uuid.uuid4()
        new_block1 = uuid.uuid4()
        payload = [{
            'id': new_block,
            'title': 'new_block',
            'parent_id': new_block1
        }, {
            'id': new_block1,
            'parent_id': new_block
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 2)
        self.assertIn('cycle_detected', rep.errors)

    def test_cycle_6(self):
        """Ищем цикл в пришедших данных + существующие блоки"""
        new_block1 = uuid.uuid4()
        new_block2 = uuid.uuid4()
        payload = [{
            'id': new_block1,
            'title': 'new_block',
            'parent_id': new_block2
        }, {
            'id': self.parentA.id,
            'parent_id': new_block1
        }, {
            'id': new_block2,
            'parent_id': self.childAA.id
        }]
        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.problem_blocks), 5)
        self.assertIn('cycle_detected', rep.errors)

    # --- Изменение блоков / childOrder ---

    def test_change_block_0(self):
        """Изменяем блок (title + permissions)"""
        new_title = 'new title'
        payload = [{
            "id": self.parentA.id,
            "title": new_title,
            "permissions": [
                {'user_id': self.u1.id, 'permission': 'delete'}
            ]
        }]
        old_data = self.parentA.data
        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()

        self.assertIn(self.parentA.id, rep.updated)
        self.assertEqual(new_title, self.parentA.title)
        self.assertEqual(old_data, self.parentA.data)

        perm = BlockPermission.objects.filter(block=self.parentA, user=self.u1.id)
        self.assertEqual(len(perm), 1)

    def test_change_block_1(self):
        """Изменяем блок в payload, другой блок не меняем -> попадает в unchanged"""
        new_title = 'new title'
        payload = [{
            'id': self.parentA.id,
            'parent_id': None
        }, {
            'id': self.childA.id,
            'parent_id': self.parentA.id,
            'title': new_title
        }]
        rep = import_blocks(payload, self.owner)
        self.childA.refresh_from_db()

        self.assertEqual(len(rep.unchanged), 1)
        self.assertEqual(len(rep.updated), 1)
        self.assertEqual(len(rep.created), 0)
        self.assertEqual(self.childA.title, new_title)

    def test_change_block_2(self):
        """childOrder содержит мусорные id -> ошибки not_found_child/not_valid_childOrder"""
        payload = [{
            'id': self.parentA.id,
            'data': {'childOrder': [str(self.childA.id), 'kek', str(uuid.uuid4())]},
            'parent_id': None
        }]

        rep = import_blocks(payload, self.owner)
        self.assertEqual(len(rep.errors), 2)
        for er in rep.errors:
            self.assertIn(er, ['not_found_child', 'not_valid_childOrder'])

    def test_change_block_3(self):
        """Переносим существующий блок в childOrder другого родителя"""
        new_title = 'new title'
        payload = [{
            'id': self.parentA.id,
            'title': new_title,
            'data': {'childOrder': [str(self.childA.id), str(self.childB.id)]},
            'parent_id': None
        }]

        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()
        self.parentB.refresh_from_db()
        self.childA.refresh_from_db()
        self.childB.refresh_from_db()

        self.assertEqual(self.parentA.id, self.childA.parent_id)
        self.assertEqual(self.parentA.id, self.childB.parent_id)
        self.assertEqual(self.parentA.data['childOrder'], [str(self.childA.id), str(self.childB.id)])
        self.assertEqual(self.parentB.data['childOrder'], [])
        self.assertEqual(len(rep.updated), 3)

    def test_change_block_4(self):
        """Удаляем ребенка из childOrder -> он помечен как deleted в репорте"""
        new_title = 'new title'
        payload = [{
            'id': self.parentA.id,
            'title': new_title,
            'data': {'childOrder': []},
            'parent_id': None
        }]
        rep = import_blocks(payload, self.owner)
        print(rep)
        self.parentA.refresh_from_db()

        self.assertIsNotNone(self.parentA.children)
        self.assertFalse(Block.objects.filter(id=self.childA.id).exists())
        self.assertIn(self.childA.id, rep.deleted)

    def test_change_block_5(self):
        """Не изменяем блок — должен попасть в unchanged"""
        payload = [{
            'id': self.parentA.id,
            'title': self.parentA.title,
            'data': self.parentA.data,
            'parent_id': None
        }]
        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()
        self.assertEqual(len(rep.unchanged), 1)

    def test_change_block_6(self):
        """Не изменяем блок — должен попасть в unchanged"""
        payload = [{
            'id': self.parentA.id,
            'parent_id': None
        }]
        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()
        self.assertEqual(len(rep.unchanged), 1)

    # --- Создание блоков ---

    def test_create_block_0(self):
        """Создать блок"""
        bid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': bid,
            'title': title,
        }]

        rep = import_blocks(payload, self.owner)
        block = Block.objects.get(id=bid)
        block_perm = BlockPermission.objects.filter(block=block)

        self.assertTrue(block_perm.exists())
        self.assertEqual(block.title, title)
        self.assertEqual(len(rep.unchanged), 0)
        self.assertEqual(len(rep.updated), 0)
        self.assertEqual(len(rep.created), 1)

    def test_create_block_1(self):
        """Создать блок c дочерним блоком"""
        pid = uuid.uuid4()
        chid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': pid,
            'title': title,
            'data': {'childOrder': [str(chid)]}
        }, {
            'id': chid,
            'title': 'child',
            'parent_id': pid,
            'permissions': [
                {'user_id': self.owner.id, 'permission': 'delete'},
                {'user_id': self.u1.id, 'permission': 'delete'}
            ]
        }]

        rep = import_blocks(payload, self.owner)
        parent = Block.objects.get(id=pid)
        child = Block.objects.get(id=chid)

        self.assertEqual(parent.id, child.parent_id)
        self.assertEqual(parent.title, title)
        self.assertEqual(len(rep.unchanged), 0)
        self.assertEqual(len(rep.updated), 0)
        self.assertEqual(len(rep.created), 2)

        only_owner_perm = BlockPermission.objects.filter(block=parent)
        self.assertTrue(only_owner_perm.exists())
        self.assertEqual(only_owner_perm[0].user.id, self.owner.id)

        owner_and_u1 = BlockPermission.objects.filter(block=child)
        self.assertEqual(len(owner_and_u1), 2)

    def test_create_block_2(self):
        """Создать блок c существующим дочерним блоком: обновится parent у ребенка и старый родитель"""
        pid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': pid,
            'title': title,
            'data': {'childOrder': [str(self.childA.id)]}
        }]

        rep = import_blocks(payload, self.owner)
        parent = Block.objects.get(id=pid)
        self.childA.refresh_from_db()
        self.parentA.refresh_from_db()

        self.assertEqual(parent.title, title)
        self.assertEqual(len(rep.unchanged), 0)
        self.assertEqual(len(rep.updated), 2)
        self.assertEqual(len(rep.created), 1)
        self.assertEqual(parent.id, self.childA.parent_id)
        self.assertIsNotNone(self.parentA.children)

    def test_create_block_3(self):
        """Создать блок c существующим и новым дочерними блоками"""
        pid = uuid.uuid4()
        chid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': pid,
            'title': title,
            'data': {'childOrder': [str(self.childA.id), str(chid)]}
        }, {
            'id': chid,
            'title': 'child',
            'parent_id': pid
        }]

        rep = import_blocks(payload, self.owner)
        parent = Block.objects.get(id=pid)
        child = Block.objects.get(id=chid)
        self.childA.refresh_from_db()

        self.assertEqual(parent.title, title)
        self.assertEqual(len(rep.unchanged), 0)
        self.assertEqual(len(rep.updated), 2)
        self.assertEqual(len(rep.created), 2)
        self.assertEqual(parent.id, self.childA.parent_id)
        self.assertEqual(parent.id, child.parent_id)
        self.assertEqual(parent.data['childOrder'], [str(self.childA.id), str(chid)])

    def test_create_block_4(self):
        """Создать блок c существующим и новым дочерними блоками, но без parent_id у нового -> ошибка"""
        pid = uuid.uuid4()
        chid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': pid,
            'title': title,
            'data': {'childOrder': [str(self.childA.id), str(chid)]}
        }, {
            'id': chid,
            'title': 'child',
        }]

        rep = import_blocks(payload, self.owner)
        self.assertEqual(rep.problem_blocks[0].code, 'not_valid_childOrder')

    def test_create_block_5(self):
        """Создать блок c несуществующим ребенком -> not_found_child"""
        pid = uuid.uuid4()
        chid = uuid.uuid4()
        title = 'new_block'
        payload = [{
            'id': pid,
            'title': title,
            'data': {'childOrder': [str(chid)]},
        }]

        rep = import_blocks(payload, self.owner)
        self.assertEqual(rep.problem_blocks[0].code, 'not_found_child')

    # --- Родитель / сохранение родителя / ошибки родителя ---

    def test_existing_block_with_external_parent_is_preserved(self):
        """
        Импортируем только CHILD, явно указывая parent_id существующего родителя.
        Родитель должен сохраниться, данные обновятся.
        """
        payload = [{
            "id": self.childA.id,
            "title": "Child updated",
            "data": {"text": "x"},
            "parent_id": self.parentA.id,
            "permissions": {},
        }]
        rep = import_blocks(payload, self.owner)

        self.childA.refresh_from_db()
        self.assertEqual(self.childA.parent_id, self.parentA.id)
        self.assertEqual(self.childA.title, "Child updated")
        self.assertEqual(self.childA.data, {"text": "x"})
        self.assertIn(self.childA.id, rep.updated)
        self.assertFalse(rep.problem_blocks, f"unexpected problems: {rep.problem_blocks}")

    def test_existing_block_without_external_parent_is_preserved(self):
        """
        Импортируем блок без указания parent_id — существующий parent_id не должен сбрасываться.
        """
        old_parent_id = self.childA.parent_id
        payload = [{
            "id": str(self.childA.id),
            "title": "Child updated 2",
            "data": {"text": "y"},
        }]

        rep = import_blocks(payload, self.owner)

        self.childA.refresh_from_db()
        self.assertEqual(self.childA.parent_id, old_parent_id)
        self.assertEqual(self.childA.title, "Child updated 2")
        self.assertEqual(self.childA.data, {"text": "y"})
        self.assertIn(self.childA.id, rep.updated)
        self.assertFalse(rep.problem_blocks)

    def test_parent_not_found_does_not_clear_existing_parent(self):
        """Обновляем блок с несуществующим parent_id, должна вернуться ошибка и старый parent сохранится"""
        unknown = uuid.uuid4()
        old_parent_id = self.childA.parent_id
        payload = [{
            "id": str(self.childA.id),
            "title": "keep",
            "data": {},
            "parent_id": str(unknown),
            "permissions": {},
        }]
        rep = import_blocks(payload, self.owner)
        pprint(rep)
        self.childA.refresh_from_db()

        self.assertEqual(self.childA.parent_id, old_parent_id)
        self.assertIn('not_found_parent', rep.errors)

    # --- Дополнительные edge-case тесты ---

    def test_not_valid_field_keeps_block_unchanged(self):
        """Невалидное поле в payload -> not_valid_field, блок не меняется."""
        old = Block.objects.get(id=self.parentA.id)
        payload = [{
            "id": self.parentA.id,
            "kek": "lol",
        }]
        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()

        self.assertEqual(self.parentA.title, old.title)
        self.assertEqual(self.parentA.data, old.data)
        self.assertIn("not_valid_field", rep.errors)
        self.assertTrue(
            any(pb.code == "not_valid_field" and pb.block_id == str(self.parentA.id)
                for pb in rep.problem_blocks)
        )

    def test_create_block_default_creator_permission(self):
        """Создание блока без permissions -> ставится DEFAULT_CREATOR_PERMISSION для создателя."""
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "with default perm",
        }]
        rep = import_blocks(payload, self.owner)
        block = Block.objects.get(id=bid)

        perms = BlockPermission.objects.filter(block=block)
        self.assertEqual(perms.count(), 1)
        self.assertEqual(perms[0].user, self.owner)
        self.assertEqual(perms[0].permission, DEFAULT_CREATOR_PERMISSION)
        self.assertIn(bid, rep.created)
        self.assertFalse(rep.problem_blocks)

    def test_create_block_with_explicit_permissions_overrides_default(self):
        """
        Явно указанные permissions должны быть применены как есть (без лишнего дефолта).
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "perms",
            "permissions": [
                {"user_id": self.owner.id, "permission": "delete"},
                {"user_id": self.u1.id, "permission": "view"},
            ],
        }]

        rep = import_blocks(payload, self.owner)
        block = Block.objects.get(id=bid)
        perms = BlockPermission.objects.filter(block=block).order_by("user_id")

        self.assertEqual(perms.count(), 2)
        self.assertEqual({p.user_id for p in perms}, {self.owner.id, self.u1.id})
        self.assertIn(bid, rep.created)
        self.assertFalse(rep.problem_blocks)

    def test_create_block_with_parent_id_only(self):
        """Создать новый блок с parent_id существующего блока без childOrder."""
        pid = self.parentA.id
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "child via parent_id",
            "parent_id": pid,
        }]
        rep = import_blocks(payload, self.owner)
        child = Block.objects.get(id=bid)

        self.assertEqual(child.parent_id, pid)
        self.assertIn(bid, rep.created)
        self.assertFalse(rep.problem_blocks)

    def test_mixed_created_updated_unchanged(self):
        """
        Смешанный сценарий:
        - parentA без изменений -> unchanged
        - childA с новым title -> updated
        - новый блок -> created
        """
        new_id = uuid.uuid4()
        payload = [
            {"id": self.parentA.id, "title": self.parentA.title},
            {"id": self.childA.id, "title": "upd-title"},
            {"id": new_id, "title": "created"},
        ]

        rep = import_blocks(payload, self.owner)
        self.childA.refresh_from_db()
        created_block = Block.objects.get(id=new_id)

        self.assertIn(self.parentA.id, rep.unchanged)
        self.assertIn(self.childA.id, rep.updated)
        self.assertIn(new_id, rep.created)
        self.assertEqual(self.childA.title, "upd-title")
        self.assertEqual(created_block.title, "created")

    def test_duplicate_id_aborts_import(self):
        """Дублирующийся id в payload -> duplicate_id, импорт не применяется."""
        bid = uuid.uuid4()
        payload = [
            {"id": bid, "title": "first"},
            {"id": bid, "title": "second"},
        ]

        rep = import_blocks(payload, self.owner)

        self.assertIn("duplicate_id", rep.errors)
        # Никаких блоков с этим id в БД нет
        self.assertFalse(Block.objects.filter(id=bid).exists())
        # rep.created здесь содержит bid (особенность реализации),
        # но факт — в БД изменений нет.

    def test_invalid_parent_id_in_payload_aborts_import(self):
        """
        Невалидный parent_id попадает в not_valid_uuid ещё на этапе сбора payload.
        Импорт целиком откатывается.
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "x",
            "parent_id": "kek",  # не UUID
        }]

        rep = import_blocks(payload, self.owner)

        self.assertIn("not_valid_uuid", rep.errors)
        self.assertFalse(Block.objects.filter(id=bid).exists())

    def test_invalid_permission_object_aborts_import(self):
        """
        permissions не-словарь -> not_valid_permission, импорт не применяется.
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "x",
            "permissions": ["wrong"],  # некорректный формат
        }]

        rep = import_blocks(payload, self.owner)

        self.assertIn("not_valid_permission", rep.errors)
        self.assertFalse(Block.objects.filter(id=bid).exists())

    def test_invalid_permission_value_aborts_import(self):
        """
        Некорректный permission -> not_valid_permission_field, импорт не применяется.
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "x",
            "permissions": [
                {"user_id": self.owner.id, "permission": "kek"},
            ],
        }]

        rep = import_blocks(payload, self.owner)

        self.assertIn("not_valid_permission_field", rep.errors)
        self.assertFalse(Block.objects.filter(id=bid).exists())

    def test_permission_dedup_and_upsert_on_existing(self):
        """
        Дедуп по (block_id, user_id) и on_conflict UPDATE:
        - у u1 уже есть perm=view
        - в payload два perm (view, delete) для u1
        - в итоге остаётся delete.
        """
        # у parentA уже есть права у owner; добавим u1:view
        BlockPermission.objects.create(
            block=self.parentA,
            user=self.u1,
            permission="view",
        )

        payload = [{
            "id": self.parentA.id,
            "permissions": [
                {"user_id": self.u1.id, "permission": "view"},
                {"user_id": self.u1.id, "permission": "delete"},  # должен победить
            ],
        }]

        rep = import_blocks(payload, self.owner)

        self.parentA.refresh_from_db()
        perm_qs = BlockPermission.objects.filter(block=self.parentA, user=self.u1)
        self.assertEqual(perm_qs.count(), 1)
        self.assertEqual(perm_qs.first().permission, "delete")
        self.assertGreaterEqual(len(rep.permissions_upserted), 1)
        # Блок по данным (title/data/parent) остался unchanged
        self.assertIn(self.parentA.id, rep.unchanged)

    def test_forbidden_existing_block_cannot_be_updated(self):
        """
        Если у пользователя только view-права на блок, он не попадает в allowed_ids,
        и попытка апдейта даёт forbidden и не меняет блок.
        """
        blocked = create_block(self.owner, 'no_rights', access='view')
        payload = [{
            "id": blocked.id,
            "title": "new",
        }]

        rep = import_blocks(payload, self.owner)

        blocked.refresh_from_db()
        self.assertEqual(blocked.title, 'no_rights')
        self.assertIn('forbidden', rep.errors)

    def test_external_child_moved_by_childorder_updates_parents_and_child(self):
        """
        childOrder нового родителя указывает на существующего ребёнка:
        - ребёнок переезжает к новому родителю
        - из childOrder старого родителя он убирается
        - rep.updated содержит и старого родителя, и ребёнка.
        """
        pid = uuid.uuid4()
        payload = [{
            "id": pid,
            "title": "new_parent",
            "data": {"childOrder": [str(self.childA.id)]},
        }]

        rep = import_blocks(payload, self.owner)

        new_parent = Block.objects.get(id=pid)
        self.childA.refresh_from_db()
        self.parentA.refresh_from_db()

        self.assertEqual(self.childA.parent_id, new_parent.id)
        old_co = (self.parentA.data or {}).get("childOrder", [])
        self.assertNotIn(str(self.childA.id), old_co)
        self.assertIn(pid, rep.created)
        self.assertIn(self.childA.id, rep.updated)
        self.assertIn(self.parentA.id, rep.updated)

    def test_default_creator_permission_on_create_if_no_permissions_specified(self):
        """
        При создании блока без permissions должен проставиться DEFAULT_CREATOR_PERMISSION для создателя.
        (Проверка интеграции add_perms + bulk upsert).
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "with_default_perm",
        }]

        rep = import_blocks(payload, self.owner)

        block = Block.objects.get(id=bid)
        perms = BlockPermission.objects.filter(block=block)
        self.assertEqual(perms.count(), 1)
        self.assertEqual(perms.first().user, self.owner)
        self.assertEqual(perms.first().permission, DEFAULT_CREATOR_PERMISSION)
        self.assertIn(bid, rep.created)
        self.assertFalse(rep.problem_blocks)

    def test_permissions_payload_do_not_add_extra_default_for_creator(self):
        """
        Если в permissions явно указан создатель, не должно появляться лишних дублей:
        остаётся ровно то, что передано (upsert по (block_id, user_id)).
        """
        bid = uuid.uuid4()
        payload = [{
            "id": bid,
            "title": "explicit_perms",
            "permissions": [
                {"user_id": self.owner.id, "permission": "delete"},
                {"user_id": self.u1.id, "permission": "view"},
            ],
        }]

        rep = import_blocks(payload, self.owner)

        block = Block.objects.get(id=bid)
        perms = BlockPermission.objects.filter(block=block).order_by("user_id")

        self.assertEqual(perms.count(), 2)
        self.assertEqual({p.user_id for p in perms}, {self.owner.id, self.u1.id})
        self.assertIn(bid, rep.created)
        self.assertFalse(rep.problem_blocks)

    def test_cycle_through_forbidden_block_is_ignored(self):
        """
        Если на блок нет прав, он помечается forbidden ещё до проверки циклов,
        и cycle_detected не выставляется.
        """
        mid = create_block(self.owner, 'mid', access='')  # нет прав -> не в allowed_ids
        a = create_block(self.owner, 'a', access='delete', parent_id=mid.id)

        payload = [{
            "id": mid.id,
            "parent_id": a.id,  # формально цикл mid <-> a, но mid forbidden
        }]

        rep = import_blocks(payload, self.owner)

        # Сработал forbidden
        self.assertIn("forbidden", rep.errors)
        # Циклы не проверяются, пока есть другие проблемы
        self.assertNotIn("cycle_detected", rep.errors)

        mid.refresh_from_db()
        # Родитель не должен поменяться
        self.assertIsNone(mid.parent_id)

    def test_update_block_with_invalid_parent_id_string(self):
        """
        Невалидный parent_id для существующего блока даёт not_valid_uuid ещё на этапе
        сбора payload и не меняет блок.
        """
        old_parent_id = self.childA.parent_id

        payload = [{
            "id": self.childA.id,
            "parent_id": "not-a-uuid",
        }]

        rep = import_blocks(payload, self.owner)

        self.childA.refresh_from_db()
        self.assertIn("not_valid_uuid", rep.errors)
        self.assertEqual(self.childA.parent_id, old_parent_id)
        self.assertFalse(rep.created)
        self.assertFalse(rep.updated)

    def test_change_block_childorder_not_list(self):
        """
        Если childOrder не список, получаем not_valid_childOrder и импорт не применяется.
        """
        old_data = self.parentA.data
        payload = [{
            "id": self.parentA.id,
            "data": {"childOrder": "not-a-list"},
        }]

        rep = import_blocks(payload, self.owner)

        self.parentA.refresh_from_db()
        self.assertIn("not_valid_childOrder", rep.errors)
        # Так как были ошибки до _apply, данные блока не должны измениться
        self.assertEqual(self.parentA.data, old_data)

    def test_malformed_childorder_on_existing_parent_ignored_when_moving_child(self):
        """
        У старого родителя childOrder = 'oops' (не список).
        При переносе ребёнка к новому parent через childOrder:
        - ребёнок переезжает;
        - старый parent остаётся с битым childOrder (но без ошибки).
        """
        parent_broken = create_block(
            self.owner,
            'broken_parent',
            data={'childOrder': 'oops'},
            access='delete',
        )
        child = create_block(
            self.owner,
            'broken_child',
            data={},
            access='delete',
            parent_id=parent_broken.id,
        )

        new_parent_id = uuid.uuid4()
        payload = [{
            "id": new_parent_id,
            "title": "new_parent",
            "data": {"childOrder": [str(child.id)]},
        }]

        rep = import_blocks(payload, self.owner)

        new_parent = Block.objects.get(id=new_parent_id)
        child.refresh_from_db()
        parent_broken.refresh_from_db()

        self.assertEqual(child.parent_id, new_parent.id)
        # битый childOrder у старого родителя не тронут и не ломает импорт
        self.assertEqual(parent_broken.data.get("childOrder"), "oops")
        self.assertIn(new_parent_id, rep.created)
        self.assertIn(child.id, rep.updated)

    def test_payload_missing_in_create_blocks_is_safe_defensive(self):
        """
        Defensive-кейс для _set_create_blocks: если в create_ids попал id без payload,
        функция не падает и помечает payload_missing.
        """
        from api.services import import_blocks as mod

        rep = mod.ImportReport()
        fake_id = uuid.uuid4()
        ctx = mod.ImportContext(
            user=self.owner,
            payload_by_id={},  # нет fake_id
            existing_by_id={},
            allowed_ids=set(),
            rep=rep,
            deleted_ids=set(),
            child_parent={},
            perms={},
            links_create=[],
            links_update=[]
        )

        new_blocks = mod._set_create_blocks({fake_id}, ctx)

        self.assertEqual(new_blocks, [])
        self.assertTrue(
            any(p.block_id == str(fake_id) and p.code == "payload_missing"
                for p in rep.problem_blocks)
        )

    def test_link_created(self):
        '''Создание ссылки на блок'''
        link_id = uuid.uuid4()
        new_child_order = self.parentA.data['childOrder']
        new_child_order.append(str(link_id))
        payload = [{
            'id': self.parentA.id,
            'data': {
                'childOrder': new_child_order
            },
            'title': self.parentA.title,
            'parent_id': self.parentA.parent_id
        }, {
            'id': link_id,
            'data': {'view': 'link', 'source': str(self.parentB.id)},
            'parent_id': self.parentA.id
        }, {
            'id': self.parentB.id,
            'data': self.parentB.data,
            'title': self.parentB.title,
            'parent_id': self.parentB.parent_id
        }]

        rep = import_blocks(payload, self.owner)
        self.assertTrue(BlockLink.objects.filter(source=self.parentB, target=self.parentA).exists())
        self.assertEqual(rep.links_upserted, 1)
        self.assertEqual(rep.created.pop(), link_id)
        self.assertEqual(rep.updated.pop(), self.parentA.id)
        self.assertEqual(rep.unchanged.pop(), self.parentB.id)
        self.assertEqual(rep.links_upserted, 1)
        self.assertEqual(rep.permissions_upserted, 1)

    def test_update_link_0(self):
        '''Меняем существующию ссылку'''
        payload = [{
            'id': self.block_link.id,
            'data': {'view': 'link', 'source': str(self.childBB.id)},
            'parent_id': self.block_link.parent_id
        }]
        rep = import_blocks(payload, self.owner)
        self.block_link.refresh_from_db()
        self.assertIn(self.block_link.id, rep.updated)
        self.assertEqual(rep.links_upserted, 1)
        self.assertEqual(self.block_link.data['source'], str(self.childBB.id))

    def test_update_link_1(self):
        '''Меняем существующию ссылку и ее родителя'''
        payload = [{
            'id': self.block_link.id,
            'data': {'view': 'link', 'source': str(self.childAA.id)},
            'parent_id': self.childB.id
        }]
        rep = import_blocks(payload, self.owner)
        self.block_link.refresh_from_db()
        self.childB.refresh_from_db()
        self.childAA.refresh_from_db()
        self.assertEqual(self.block_link.data['source'], str(self.childAA.id))
        self.assertEqual(self.block_link.parent_id, self.childB.id)
        self.assertEqual(rep.links_upserted, 1)
        self.assertIn(str(self.block_link.id), self.childB.data['childOrder'])
        self.assertNotIn(str(self.block_link.id), self.childAA.data['childOrder'])

    def test_update_link_2(self):
        '''Ссылка ссылается на своего родителя, вернется ошибка'''
        payload = [{
            'id': self.block_link.id,
            'data': {'view': 'link', 'source': str(self.childB.id)},
            'parent_id': self.childB.id
        }]
        rep = import_blocks(payload, self.owner)
        self.block_link.refresh_from_db()
        self.childAA.refresh_from_db()
        self.assertEqual(self.block_link.parent_id, self.childAA.id)
        self.assertNotEqual(self.block_link.parent_id, self.childB.id)
        self.assertIn('wrong_parent_link', rep.errors)

    def test_update_link_3(self):
        '''Меняем существующию ссылку на блок без прав'''
        payload = [{
            'id': self.block_link.id,
            'data': {'view': 'link', 'source': str(self.block_non_access.id)},
            'parent_id': self.block_link.parent_id
        }]
        rep = import_blocks(payload, self.owner)
        print(rep)
        self.block_link.refresh_from_db()
        self.assertEqual(self.block_link.data['source'], str(self.childB.id))
        self.assertIn('not_allowed_link', rep.errors)

    def test_update_link_4(self):
        '''Меняем существующию ссылку на блок без прав'''
        payload = [{
            'id': self.block_link.id,
            'data': {'view': 'link', 'source': 'kek'},
            'parent_id': self.block_link.parent_id
        }]
        rep = import_blocks(payload, self.owner)
        self.block_link.refresh_from_db()
        self.assertEqual(self.block_link.data['source'], str(self.childB.id))
        self.assertIn('not_valid_source_uuid', rep.errors)

    def test_link_in_payload(self):
        '''Сылка есть в payload'''
        link_id = uuid.uuid4()
        new_block_id = uuid.uuid4()
        new_data_bb = {'childOrder': [str(link_id)]}
        new_data_aa = self.childAA.data
        new_data_aa['childOrder'].append(str(new_block_id))
        payload = [{
            'id': link_id,
            'data': {'view': 'link', 'source': str(new_block_id)},
            'parent_id': self.childBB.id
        }, {
            'id': new_block_id,
            'title': 'new_block',
            'parent_id': self.childAA.id,
            'data': {'text': 'new block'}
        }, {
            'id': self.childBB.id,
            'data': new_data_bb,
            'title': self.childBB.title,
            'parent_id': self.childBB.parent_id
        }, {
            'id': self.childAA.id,
            'data': new_data_aa,
            'title': self.childAA.title,
            'parent_id': self.childAA.parent_id
        }]

        rep = import_blocks(payload, self.owner)
        pprint(rep)
        self.childAA.refresh_from_db()
        self.childBB.refresh_from_db()
        link_filter = Block.objects.filter(id=link_id)
        self.assertTrue(link_filter.exists())
        link = link_filter[0]
        new_block = Block.objects.get(id=new_block_id)
        self.assertEqual(link.parent_id, self.childBB.id)
        self.assertEqual(Block.objects.get(id=new_block_id).parent_id, self.childAA.id)
        self.assertEqual(new_block.data['text'], 'new block')

    def test_parent_0(self):
        '''Меняем родителя у блока childOrder должен изменится у нового и старого родителя'''
        new_parent_a_data = self.parentA.data
        new_parent_a_data['childOrder'] = [child for child in new_parent_a_data['childOrder'] if
                                           str(self.childA.id) != child]
        new_parent_a_data['text'] = 'text'
        new_parent_b_data = self.parentB.data
        new_parent_b_data['childOrder'].append(str(self.childA.id))
        new_parent_b_data['text'] = 'textb'
        payload = [{
            'id': self.childA.id,
            'title': self.childA.title,
            'data': self.childA.data,
            'parent_id': self.parentB.id
        }, {
            'id': self.parentA.id,
            'title': self.parentA.title,
            'data': new_parent_a_data,
            'parent_id': self.parentA.parent_id
        }, {
            'id': self.parentB.id,
            'title': 'PARENT_B',
            'data': new_parent_b_data,
            'parent_id': self.parentB.parent_id
        }]
        pprint(payload)
        rep = import_blocks(payload, self.owner)
        self.childA.refresh_from_db()
        self.parentA.refresh_from_db()
        self.assertNotIn(str(self.childA.id), self.parentA.data['childOrder'])
        self.assertEqual(self.parentA.data['text'], 'text')
        self.parentB.refresh_from_db()
        self.assertEqual(self.childA.parent_id, self.parentB.id)
        self.assertIn(str(self.childA.id), self.parentB.data['childOrder'])

    def test_parent_1(self):
        '''Меняем родителя у блока, без родителей в payload childOrder должен изменится у нового и старого родителя'''
        payload = [{
            'id': self.childA.id,
            'data': self.childA.data,
            'parent_id': self.parentB.id
        }]

        rep = import_blocks(payload, self.owner)
        self.childA.refresh_from_db()
        self.parentB.refresh_from_db()
        self.parentA.refresh_from_db()
        self.assertEqual(self.childA.parent_id, self.parentB.id)
        self.assertIn(str(self.childA.id), self.parentB.data['childOrder'])
        self.assertNotIn(str(self.childA.id), self.parentA.data['childOrder'])

    def test_parent_2(self):
        '''Меняем childOrder'''
        parent_a_data = self.parentA.data
        parent_a_data['childOrder'] = [str(self.childA.id), str(self.childB.id), str(self.childBB.id)]
        payload = [{
            'id': self.parentA.id,
            'data': parent_a_data,
            'parent_id': self.parentA.parent_id
        }]

        rep = import_blocks(payload, self.owner)
        self.parentA.refresh_from_db()
        self.childB.refresh_from_db()
        self.childBB.refresh_from_db()
        self.assertEqual(self.childB.parent_id, self.parentA.id)
        self.assertEqual(self.childBB.parent_id, self.parentA.id)
        self.assertNotIn(str(self.childBB.id), self.childB.data['childOrder'])

    def test_task_statuses(self):
        task = DummyTask()
        parent_a_data = self.parentA.data
        parent_a_data['childOrder'] = [str(self.childA.id), str(self.childB.id), str(self.childBB.id)]
        payload = [{
            'id': self.parentA.id,
            'data': parent_a_data,
            'parent_id': self.parentA.parent_id
        }]
        _ = import_blocks(payload, self.owner, task=task)
        for status, meta in task.states:
            if status == 'START':
                continue
            elif status == 'DATA_PREPARED':
                continue
            elif status == 'SUCCESS':
                if isinstance(meta, dict):
                    continue
            else:
                print(status, meta)
                self.assertTrue(False)

    @override_settings(
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_EAGER_PROPAGATES=True,
        task_always_eager=True,
        task_eager_propagates=True,
    )
    def test_task_delay(self):
        """Проверим какие еще таски вызываеются"""
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

        called_tasks = []

        # handler будет вызываться перед запуском каждой Celery-таски
        def on_task_prerun(sender=None, task_id=None, task=None, *args, **kwargs):
            # sender — экземпляр Task, у него есть .name
            name = getattr(sender, "name", None)
            if name:
                called_tasks.append(name)

        # подписываемся на сигнал
        signals.task_prerun.connect(on_task_prerun)
        parent_a_data = self.parentA.data
        parent_a_data['childOrder'] = [str(self.childA.id), str(self.childB.id), str(self.childBB.id)]
        try:
            resp = self.client.post(
                '/api/v1/import/',
                data={'payload': [{
                    'id': self.parentA.id,
                    'data': parent_a_data,
                    'parent_id': self.parentA.parent_id,
                    'permissions': [{'user_id': self.u1.id, 'permission': 'delete'},
                                    {'user_id': self.u2.id, 'permission': 'view'}, ]
                }, {
                    'id': self.parentB.id,
                    'data': {'text': 'self.parentB.data', 'childOrder': []},
                    'permissions': [{'user_id': self.u1.id, 'permission': 'delete'},
                                    {'user_id': self.u2.id, 'permission': 'view'}, ]
                }]},
                format='json',
            )
        finally:
            # ОБЯЗАТЕЛЬНО отписываемся, чтобы не влиять на другие тесты
            signals.task_prerun.disconnect(on_task_prerun)

        task_id = resp.data['task_id']
        url_task = reverse('api:task_status', kwargs={'task_id': task_id})
        res = self.client.get(url_task)
        for task in [
            'api.tasks.import_blocks_task',
            'api.tasks.send_message_unsubscribe_user',
            'api.tasks.send_message_access_update',
            'api.tasks.send_message_access_update',
            'api.tasks.send_message_blocks_update'
        ]:
            self.assertIn(task, called_tasks)

    def test_all(self):
        '''
        1 создаем новые блоки в разных местах, везде ддолжны применится childOrder
        2 назначаем права
        3 переназначаем дочерние блоки
        4 переназначаем родителей
        5 создаем ссылки
        '''
        block_id_0 = uuid.uuid4()
        block_id_1 = uuid.uuid4()
        block_id_2 = uuid.uuid4()
        block_id_3 = uuid.uuid4()
        link_0_id = uuid.uuid4()
        link_1_id = uuid.uuid4()
        permissions = [
            {'user_id': self.owner.id, 'permission': 'delete'},
            {'user_id': self.u1.id, 'permission': 'delete'},
            {'user_id': self.u2.id, 'permission': 'view'},
        ]
        payload = [{
            'id': block_id_0,
            'parent_id': self.parentA.id,
            'title': 'new child for parentA and new parent for childA',
            'data': {'childOrder': [str(self.childA.id), str(link_0_id), str(block_id_1)], 'text': 'test'},
            'permissions': permissions.copy(),
        }, {
            'id': link_0_id,
            'data': {'view': 'link', 'source': str(self.childB.id)},
            'parent_id': block_id_0,
            'permissions': permissions.copy(),
        }, {
            'id': block_id_1,
            'parent_id': block_id_0,
            'title': 'new child block_id_0',
            'data': {
                'text': 'text',
                'childOrder': [str(link_1_id), str(self.parentB.id)]
            },
            'permissions': permissions.copy(),
        }, {
            'id': link_1_id,
            'data': {'view': 'link', 'source': str(self.childBB.id)},
            'parent_id': block_id_1,
            'permissions': permissions.copy(),
        }, {
            'id': self.parentB.id,
            'title': 'kek',
            'parent_id': block_id_1,
            'data': {
                'text': 'text',
                'childOrder': [str(block_id_2), str(block_id_3), str(self.childB.id)]
            },
            'permissions': permissions.copy(),
        }, {
            'id': block_id_2,
            'parent_id': self.parentB.id,
            'title': 'block 2',
            'data': {'childOrder': []},
            'permissions': permissions.copy(),
        }, {
            'id': block_id_3,
            'parent_id': self.parentB.id,
            'title': 'block 3',
            'data': {'childOrder': []},
            'permissions': permissions.copy(),
        }, {
            'id': self.childAA.id,  # блок будет в неизмененных дочерний блок не удалится потому что не задано data
            'parent_id': self.childA.id,
            'permissions': permissions.copy(),
        }, {
            'id': self.childBB.id,
            'data': {'childOrder': []},  # удалится дочерний блок self.new_block
            'parent_id': self.childB.id,
            'permissions': permissions.copy(),
        }]
        # pprint(payload)
        rep = import_blocks(payload, self.owner)
        pprint(rep)
        self.assertIn(self.parentA.id, rep.updated)
        self.assertIn(self.childA.id, rep.updated)
        self.assertIn(self.parentB.id, rep.updated)
        self.assertIn(self.childBB.id, rep.updated)

        self.assertIn(block_id_0, rep.created)
        self.assertIn(block_id_1, rep.created)
        self.assertIn(block_id_2, rep.created)
        self.assertIn(block_id_3, rep.created)
        self.assertIn(link_0_id, rep.created)
        self.assertIn(link_1_id, rep.created)

        self.assertIn(self.childAA.id, rep.unchanged)
        self.assertIn(self.new_block.id, rep.deleted)
        self.assertNotIn(self.block_link.id, rep.deleted)
        self.assertFalse(Block.objects.filter(id=self.new_block.id).exists())

        self.parentA.refresh_from_db()
        self.parentB.refresh_from_db()
        self.childA.refresh_from_db()
        block_0 = Block.objects.get(id=block_id_0)
        block_1 = Block.objects.get(id=block_id_1)

        self.assertEqual(self.childA.parent_id, block_0.id)
        self.assertNotIn(str(self.childA.id), self.parentA.data['childOrder'])
        self.assertIn(str(block_id_0), self.parentA.data['childOrder'])
        self.assertEqual(block_0.parent_id, self.parentA.id)

        self.assertEqual(self.parentB.parent_id, block_id_1)
        self.assertEqual(self.parentB.data, {
            'text': 'text',
            'childOrder': [str(block_id_2), str(block_id_3), str(self.childB.id)]
        })

        # проверка ссылок
        link_0 = Block.objects.get(id=link_0_id)
        self.assertEqual(link_0.parent_id, block_id_0)
        self.assertTrue(BlockLink.objects.filter(source=self.childB, target=block_0).exists())
        link_1 = Block.objects.get(id=link_1_id)
        self.assertEqual(link_1.parent_id, block_id_1)
        self.assertTrue(BlockLink.objects.filter(source=self.childBB, target=block_1).exists())

        # проверка прав
        self.assertTrue(BlockPermission.objects.filter(block=block_0, user=self.owner, permission='delete').exists())
        self.assertTrue(BlockPermission.objects.filter(block=block_0, user=self.u1, permission='delete').exists())
        self.assertTrue(BlockPermission.objects.filter(block=block_0, user=self.u2, permission='view').exists())
        self.assertTrue(BlockPermission.objects.filter(block=link_0, user=self.u2, permission='view').exists())
        self.assertTrue(BlockPermission.objects.filter(block=link_0, user=self.u2, permission='view').exists())
        self.assertTrue(BlockPermission.objects.filter(block=link_0, user=self.u2, permission='view').exists())
        self.assertEqual(rep.permissions_upserted, (len(permissions) * len(payload)))
