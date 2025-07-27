import uuid
from django.urls import reverse
from django.utils import timezone
import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from api.models import Block, BlockPermission

User = get_user_model()

@pytest.fixture
def user(db):
    return User.objects.create_user(username='user', password='pass')

@pytest.fixture
def other_user(db):
    return User.objects.create_user(username='other', password='pass')

@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client

@pytest.fixture
def parent_block(user):
    block = Block.objects.create(creator=user, title='parent', data={'childOrder': []})
    BlockPermission.objects.create(block=block, user=user, permission='delete')
    return block


def make_block_payload(block_id, parent_id):
    return {
        'id': str(block_id),
        'title': 'title',
        'data': {},
        'parent_id': str(parent_id) if parent_id else None,
        'updated_at': timezone.now().isoformat(),
        'children': []
    }


@pytest.mark.django_db
def test_import_json_missing_fields(auth_client, parent_block):
    url = reverse('api:import-json', kwargs={'block_id': parent_block.id})
    response = auth_client.post(url, {}, format='json')
    assert response.status_code == 400


@pytest.mark.django_db
def test_import_json_create_inherits_permissions(auth_client, parent_block, user):
    new_id = uuid.uuid4()
    payload = make_block_payload(new_id, parent_block.id)
    url = reverse('api:import-json', kwargs={'block_id': parent_block.id})
    response = auth_client.post(
        url, {'root_id': str(new_id), 'data': {str(new_id): payload}}, format='json'
    )
    assert response.status_code == 201
    created = Block.objects.get(id=new_id)
    assert BlockPermission.objects.filter(block=created, user=user, permission='delete').exists()
    assert str(new_id) in created.parent.data.get('childOrder', []) or parent_block.is_my_child(new_id)


@pytest.mark.django_db
def test_import_json_update_requires_permission(parent_block, other_user):
    block = Block.objects.create(creator=parent_block.creator, parent=parent_block, title='child')
    BlockPermission.objects.create(block=block, user=parent_block.creator, permission='delete')
    client = APIClient()
    client.force_authenticate(user=other_user)
    url = reverse('api:import-json', kwargs={'block_id': parent_block.id})
    payload = make_block_payload(block.id, parent_block.id)
    response = client.post(
        url, {'root_id': str(block.id), 'data': {str(block.id): payload}}, format='json'
    )
    assert response.status_code == 403
