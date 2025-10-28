import json
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from api.models import Block, BlockLink, BlockPermission
from api.view_delete_tree import get_all_descendant_ids, delete_tree

User = get_user_model()


@pytest.fixture
def rf():
    return APIRequestFactory()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="tester", email="t@e.st", password="pass")


def make_block(creator, title='test', permission='delete', parent=None, data=None,):
    b = Block.objects.create(
        creator=creator,
        title=title or "blk",
        parent=parent,
        data=(data or {}),
        updated_at=timezone.now(),
    )
    print('permission', permission)
    if permission:
        BlockPermission.objects.create(block=b, user=creator, permission=permission)
    # если указан parent — используем твой метод, чтобы корректно заполнить childOrder/связи
    if parent:
        parent.add_child(b)
    return b


# ------------------ get_all_descendant_ids ------------------

@pytest.mark.django_db
def test_get_all_descendant_ids_returns_subtree_ids(user):
    root = make_block(user, "root")
    c1 = make_block(user, "c1", parent=root)
    c2 = make_block(user, "c2", parent=root)
    g11 = make_block(user, "g11", parent=c1)
    g12 = make_block(user, "g12", parent=c1)
    g21 = make_block(user, "g21", parent=c2)

    got = set(str(x) for x in get_all_descendant_ids(str(root.id)))
    expect = {str(root.id), str(c1.id), str(c2.id), str(g11.id), str(g12.id), str(g21.id)}
    assert got == expect


# ------------------ delete_tree: базовый сценарий ------------------

@pytest.mark.django_db
def test_delete_tree_deletes_subtree_updates_targets_and_parent(rf, user, monkeypatch):
    # Дерево: P -> R -> (A, B)
    P = make_block(user, "parent")
    R = make_block(user, "root", parent=P)
    A = make_block(user, "A", parent=R)
    B = make_block(user, "B", parent=R)

    # Внешние блоки: T — целевой (на него ссылаются удаляемые), S — исходный (ссылается на удаляемые)
    T = make_block(user, "target-outside")
    S = make_block(user, "source-outside")

    # Исходящие ссылки ИЗ удаляемых (R/A/B) НА внешний T — их target надо "пометить"
    BlockLink.objects.create(source=R, target=T)
    BlockLink.objects.create(source=A, target=T)

    # Входящая ссылка ИЗ внешнего S В удаляемый R — должна быть удалена
    BlockLink.objects.create(source=S, target=R)

    # sanity: childOrder у P должен включать R
    assert str(R.id) in (P.data.get("childOrder") or [])

    # Вызов view
    request = rf.delete(f"/fake/force-delete/{R.id}")
    force_authenticate(request, user=user)
    response = delete_tree(request, tree_id=str(R.id))
    assert response.status_code == 200
    payload = json.loads(response.render().content)

    # Ответ должен содержать parent и список удалённых id
    deleted_ids = set(payload["deleted"])
    assert str(R.id) in deleted_ids and str(A.id) in deleted_ids and str(B.id) in deleted_ids

    # Удалённые блоки исчезли
    assert not Block.objects.filter(id__in=[R.id, A.id, B.id]).exists()

    # Все ссылки, где source/target — из удаляемых, удалены
    assert not BlockLink.objects.filter(source_id__in=[R.id, A.id, B.id]).exists()
    assert not BlockLink.objects.filter(target_id__in=[R.id, A.id, B.id]).exists()

    # Внешний target T должен быть помечен (title + data)
    T.refresh_from_db()
    assert T.title == 'The resource of this link has been deleted.'
    assert T.data == {"color": [0, 65, 47, 0]}

    # Родитель P — больше не содержит R ни как child, ни в childOrder
    P.refresh_from_db()
    children_ids = set(str(i) for i in P.children.values_list("id", flat=True))
    assert str(R.id) not in children_ids
    assert str(R.id) not in (P.data.get("childOrder") or [])



# ------------------ delete_tree: корень с parent=None ------------------

@pytest.mark.django_db
def test_delete_tree_root_with_no_parent_marks_targets_only(rf, user, monkeypatch):
    R = make_block(user, "root", parent=None)
    T = make_block(user, "target")
    BlockLink.objects.create(source=R, target=T)  # target должен быть помечен

    request = rf.delete(f"/fake/force-delete/{R.id}")
    force_authenticate(request, user=user)
    response = delete_tree(request, tree_id=str(R.id))

    assert response.status_code == 200
    T.refresh_from_db()
    assert T.title == 'The resource of this link has been deleted.'
    assert T.data == {"color": [0, 65, 47, 0]}

# ------------------ _delete_single_block путь: data["source"] присутствует ------------------

@pytest.mark.django_db
def test_delete_tree_uses_delete_single_block_when_source_in_data(rf, user, monkeypatch):
    parent = make_block(user, "P")
    root = make_block(user, "R", parent=parent)
    src = make_block(user, "SRC")

    # Заглушка "source" хранится как строковый UUID — фильтр FK в Django это примет
    root.data = {"source": str(src.id)}
    root.save(update_fields=["data"])

    # Есть линк SRC -> root (его должен удалить _delete_single_block)
    BlockLink.objects.create(source=src, target=root)

    request = rf.delete(f"/fake/force-delete/{root.id}")
    force_authenticate(request, user=user)
    response = delete_tree(request, tree_id=str(root.id))

    assert response.status_code == 200

    # root удалён
    assert not Block.objects.filter(id=root.id).exists()
    # линк удалён
    assert not BlockLink.objects.filter(source=src, target_id=root.id).exists()


# ------------------ аутентификация обязательна ------------------

@pytest.mark.django_db
def test_delete_tree_requires_auth(rf, user):
    root = make_block(user, "R", )
    request = rf.delete(f"/fake/force-delete/{root.id}")
    # не аутентифицируем
    response = delete_tree(request, tree_id=str(root.id))
    assert response.status_code in (401, 403)



# ------------------ права на уделение обязательны ------------------

@pytest.mark.django_db
def test_delete_tree_not_permissions(rf, user):
    test_perm = ['kek', 'view', 'edit', 'deny', 'edit_ac', 'delete']
    response = {}
    for i, perm in enumerate(test_perm):
        block = make_block(user, perm, permission=perm)
        request = rf.delete(f"/fake/force-delete/{block.id}")
        force_authenticate(request, user=user)
        response = delete_tree(request, tree_id=str(block.id))
        if i != len(test_perm)-1:
            assert response.status_code == 403
    assert response.status_code == 200
