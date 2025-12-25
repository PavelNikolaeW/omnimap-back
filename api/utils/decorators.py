from functools import wraps
from pprint import pprint

from rest_framework.response import Response
from django.contrib.auth import get_user_model
from rest_framework import status
from api.models import BlockPermission, Block

User = get_user_model()

def subscribe_to_blocks(decorator_task):
    '''Подписка клиента не все переданные ему блоки'''
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            user_id = kwargs.get('user_id')
            if isinstance(response, Response) and response.status_code in [200, 201, 202]:
                if view_func.__name__ == 'load_trees':
                    data = [str(block_id) for tree in response.data.values() for block_id in tree.keys()]
                elif view_func.__name__ == 'create_block':
                    user_id = request.user.id
                    data = [block['id'] for block in response.data]
                else:
                    data = list(str(block_id) for block_id, block in response.data.items() if block['updated_at'] != '2000-01-01T00:00:01.000001Z')
                decorator_task.delay(data, [user_id])
            return response
        return wrapper
    return decorator

def determine_user_id(view_func):
    """
    Декоратор для определения user_id:
    - Если пользователь аутентифицирован, используется его ID.
    - Иначе используется ID пользователя с username 'main_page'.
    - Если 'main_page' не существует, возвращает 403 Forbidden.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated:
            user_id = request.user.id
        else:
            try:
                user_id = User.objects.get(username='main_page').id
            except User.DoesNotExist:
                return Response(
                    {"detail": "Anonymous access is not configured"},
                    status=status.HTTP_403_FORBIDDEN
                )
        kwargs['user_id'] = user_id
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def check_block_permissions(config):
    """
    Декоратор для проверки прав на доступ к одному или нескольким блокам.

    :param config: {'block_id_args': 'permission_values'}
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            for block_id_arg, permission_values in config.items():
                # Извлекаем block_id из kwargs по имени аргумента
                block_id = kwargs.get(block_id_arg)
                if not block_id:
                    raise Exception(f"{block_id_arg} not provided")

                # Проверяем права пользователя на текущий блок
                has_permission = BlockPermission.objects.filter(
                    block__id=block_id,
                    user=request.user,
                    permission__in=permission_values
                ).exists()

                if not has_permission:
                    return Response(
                        {"detail": f"Forbidden access to {block_id_arg}"},
                        status=status.HTTP_403_FORBIDDEN
                    )

            # Если все проверки прошли, продолжаем выполнение вьюхи
            return view_func(request, *args, **kwargs)

        return _wrapped_view
    return decorator