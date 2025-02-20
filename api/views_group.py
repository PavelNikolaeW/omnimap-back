# views_group.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User

from .models import Group
from .serializers import GroupSerializer, GroupCreateSerializer, UserSerializer


class MyGroupsView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        # Возвращаем группы, которыми владеет пользователь
        groups = Group.objects.filter(owner=request.user)
        serializer = GroupSerializer(groups, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupMembersView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, group_id):
        # Получаем группу, принадлежащую инициатору
        group = get_object_or_404(Group, id=group_id, owner=request.user)
        members = group.users.all()
        serializer = UserSerializer(members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupCreateView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        serializer = GroupCreateSerializer(data=request.data)
        if serializer.is_valid():
            group = serializer.save(owner=request.user)
            # Добавляем владельца в участников группы (по желанию)
            group.users.add(request.user)
            return Response(GroupSerializer(group).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GroupDeleteView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        if group.owner != request.user:
            return Response({"detail": "Only group owner can delete the group."},
                            status=status.HTTP_403_FORBIDDEN)
        group.delete()
        return Response({"detail": "Group deleted."}, status=status.HTTP_204_NO_CONTENT)


class GroupAddMemberView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, group_id):
        group = get_object_or_404(Group, id=group_id)
        if group.owner != request.user:
            return Response({"detail": "Only group owner can add members."},
                            status=status.HTTP_403_FORBIDDEN)
        username = request.data.get('username')
        if not username:
            return Response({"detail": "username is required."}, status=status.HTTP_400_BAD_REQUEST)
        user = get_object_or_404(User, username=username)
        group.users.add(user)
        return Response({"detail": f"User {username} added to group."}, status=status.HTTP_200_OK)


class GroupRemoveMemberView(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, group_id, username):
        print(request.data)
        group = get_object_or_404(Group, id=group_id)
        if group.owner != request.user:
            return Response({"detail": "Only group owner can remove members."},
                            status=status.HTTP_403_FORBIDDEN)

        user = get_object_or_404(User, username=username)
        if user == group.owner:
            return Response({"detail": "Cannot remove group owner."}, status=status.HTTP_400_BAD_REQUEST)
        group.users.remove(user)
        return Response({"detail": f"User {username} removed from group."}, status=status.HTTP_200_OK)
