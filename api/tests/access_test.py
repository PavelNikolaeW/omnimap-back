# api/tests.py

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from .models import Block, BlockPermission
import uuid

User = get_user_model()

class BlockPermissionAssignTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', password='pass')
        self.user = User.objects.create_user(username='user', password='pass')
        self.block = Block.objects.create(creator=self.owner, title='Test Block')
        self.client.force_authenticate(user=self.owner)

    def test_assign_permission(self):
        url = f'/api/blocks/{self.block.id}/permissions/assign/'
        data = {
            'user_id': str(self.user.id),
            'permission': 'edit'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(BlockPermission.objects.count(), 1)
        self.assertEqual(BlockPermission.objects.get().permission, 'edit')

    def test_update_permission(self):
        BlockPermission.objects.create(block=self.block, user=self.user, permission='view')
        url = f'/api/blocks/{self.block.id}/permissions/assign/'
        data = {
            'user_id': str(self.user.id),
            'permission': 'deny'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(BlockPermission.objects.count(), 1)
        self.assertEqual(BlockPermission.objects.get().permission, 'deny')

    def test_assign_permission_non_owner(self):
        self.client.force_authenticate(user=self.user)
        url = f'/api/blocks/{self.block.id}/permissions/assign/'
        data = {
            'user_id': str(self.owner.id),
            'permission': 'view'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)