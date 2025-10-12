"""Вьюхи для управления пользовательскими группами и их участниками."""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User

from .models import Group
from .serializers import GroupSerializer, GroupCreateSerializer, UserSerializer


class MyGroupsView(APIView):
    """Возвращает группы, владельцем которых является текущий пользователь."""

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request):
        """Собирает и возвращает список групп пользователя-владельца."""

        groups = Group.objects.filter(owner=request.user)
        serializer = GroupSerializer(groups, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupMembersView(APIView):
    """Позволяет владельцу просматривать участников конкретной группы."""

    permission_classes = (permissions.IsAuthenticated,)

    def get(self, request, group_id):
        """Возвращает сериализованный список участников выбранной группы."""

        group = get_object_or_404(Group, id=group_id, owner=request.user)
        members = group.users.all()
        serializer = UserSerializer(members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GroupCreateView(APIView):
    """Создаёт новую группу и привязывает к ней текущего пользователя."""

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request):
        """Валидирует входные данные и создаёт группу с владельцем."""

        serializer = GroupCreateSerializer(data=request.data)
        if serializer.is_valid():
            group = serializer.save(owner=request.user)
            # Добавляем владельца в участников группы (по желанию)
            group.users.add(request.user)
            return Response(GroupSerializer(group).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GroupDeleteView(APIView):
    """Удаляет группу, если запрос инициировал её владелец."""

    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, group_id):
        """Проверяет право владения и удаляет выбранную группу."""

        group = get_object_or_404(Group, id=group_id)
        if group.owner != request.user:
            return Response({"detail": "Only group owner can delete the group."},
                            status=status.HTTP_403_FORBIDDEN)
        group.delete()
        return Response({"detail": "Group deleted."}, status=status.HTTP_204_NO_CONTENT)


class GroupAddMemberView(APIView):
    """Добавляет пользователя в группу при подтверждении прав владельца."""

    permission_classes = (permissions.IsAuthenticated,)

    def post(self, request, group_id):
        """Добавляет участника по username, если его пригласил владелец группы."""

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
    """Удаляет участника из группы при условии, что действие инициировал владелец."""

    permission_classes = (permissions.IsAuthenticated,)

    def delete(self, request, group_id, username):
        """Удаляет указанного участника, запрещая исключать владельца."""

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
