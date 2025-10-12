import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'block_api.settings')

import django

django.setup()

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import Block, BlockPermission, BlockLink

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username='deleter', password='password')


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def service_block(user):
    block = Block.objects.create(creator=user, title='service')
    BlockPermission.objects.create(block=block, user=user, permission='delete')
    return block


@pytest.fixture
def deletable_block(user):
    block = Block.objects.create(creator=user, title='target')
    BlockPermission.objects.create(block=block, user=user, permission='delete')
    return block


@pytest.mark.django_db
def test_force_delete_updates_links(auth_client, user, service_block, deletable_block):
    link_block = Block.objects.create(
        creator=user,
        data={'view': 'link', 'source': str(deletable_block.id)}
    )
    BlockPermission.objects.create(block=link_block, user=user, permission='delete')
    BlockLink.objects.create(source=deletable_block, target=link_block)

    url = reverse('api:force-delete-tree', args=[deletable_block.id])

    with override_settings(SERVICE_BLOCK_ID=str(service_block.id)):
        response = auth_client.delete(url)

    assert response.status_code == 200
    link_block.refresh_from_db()
    assert link_block.data['source'] == str(service_block.id)
    link = BlockLink.objects.get(target=link_block)
    assert link.source_id == service_block.id
    assert not Block.objects.filter(id=deletable_block.id).exists()
