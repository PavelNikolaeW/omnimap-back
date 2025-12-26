import pytest
from unittest.mock import patch, MagicMock
from django.conf import settings


@pytest.fixture(scope='session', autouse=True)
def celery_eager_mode():
    """Run Celery tasks synchronously in tests (no broker needed)."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


@pytest.fixture(autouse=True)
def mock_rabbitmq():
    """Mock RabbitMQ connections for all tests."""
    with patch('api.tasks.Connection') as mock_conn:
        mock_connection = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=mock_connection)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_conn
