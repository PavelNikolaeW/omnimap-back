from pprint import pprint

from django.test import override_settings
import pytest
import uuid6
from rest_framework.test import APIClient
from django.urls import reverse
from api.models import Block, BlockPermission
from django.contrib.auth import get_user_model

from api.tests.utils import draw_complex_forest

User = get_user_model()


@pytest.fixture
def users(db):
    """Фикстура для создания пользователей."""
    return [User.objects.create_user(username=f"testuser{n}", password="testpass") for n in range(10)]


@pytest.fixture
def auth_clients(users):
    """Фикстура для аутентифицированного клиента."""
    clients = []
    for user in users:
        client = APIClient()
        client.force_authenticate(user=user)
        clients.append(client)
    return clients


@pytest.fixture
def id_links(users):
    """Fixture for creating block trees with larger hierarchies for multiple users."""
    u1, u2, u3 = users[0:3]

    # User 1's tree
    root_u1 = Block.objects.create(creator=u1, title="Root U1", data={"level": 0})
    level1_1_u1 = Block.objects.create(creator=u1, title="L1-1 U1", data={"level": 1})
    level1_2_u1 = Block.objects.create(creator=u1, title="L1-2 U1", data={"level": 1})
    level2_1_u1 = Block.objects.create(creator=u1, title="L2-1 U1", data={"level": 2})
    level2_2_u1 = Block.objects.create(creator=u1, title="L2-2 U1", data={"level": 2})
    level2_3_u1 = Block.objects.create(creator=u1, title="L2-3 U1", data={"level": 2})
    level3_1_u1 = Block.objects.create(creator=u1, title="L3-1 U1", data={"level": 3})
    level3_2_u1 = Block.objects.create(creator=u1, title="Link3-2 U1", data={"level": 3})

    level1_1_u1.add_children([level2_1_u1, level2_2_u1])
    level1_2_u1.add_children([level2_3_u1])
    level2_2_u1.add_children([level3_1_u1, level3_2_u1])
    root_u1.add_children([level1_1_u1, level1_2_u1])
    #
    # User 2's tree
    root_u2 = Block.objects.create(creator=u2, title="Root U2 source lionk", data={"level": 0})
    level1_1_u2 = Block.objects.create(creator=u2, title="L1-1 U2", data={"level": 1})
    level2_1_u2 = Block.objects.create(creator=u2, title="L2-1 U2", data={"level": 2})
    level1_1_u2.add_children([level2_1_u2])
    root_u2.add_children([level1_1_u2, ])

    # level3_2_u1.add_children([level1_1_u2])

    #
    level2_2_u2 = Block.objects.create(creator=u2, title="L2-2 U2", data={"level": 2})
    # level3_1_u2 = Block.objects.create(creator=u2, title="L3-1 U2", data={"level": 3})
    # level1_1_u2.add_children([level2_1_u2, level2_2_u2, level2_2_u1])
    # level2_2_u2.add_children([level3_1_u2])
    #
    # level3_2_u1.add_children([root_u2])

    # return [str(level1_1_u2.id), str(level2_2_u2.id)]
    return [str(level3_2_u1.id)]


@pytest.mark.django_db
def test_load_root_blocks(id_links, auth_clients, users):
    c1, c2, c3 = auth_clients[0:3]
    url = reverse("api:load-links")
    res = c1.post(url, {
        "block_ids": id_links
    }, format="json")
    print(res.status_code)
    draw_complex_forest(res.data)
