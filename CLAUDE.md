# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git Workflow

**IMPORTANT: Разработка ведётся в отдельных ветках!**

1. **Создай ветку** для задачи:
   ```bash
   git checkout -b feature/название-задачи
   # или
   git checkout -b fix/описание-бага
   ```

2. **Работай в своей ветке** — никогда не коммить напрямую в `main`

3. **После завершения задачи** создай Pull Request:
   ```bash
   git push -u origin feature/название-задачи
   gh pr create --title "Описание" --body "Детали изменений"
   ```

4. **Дождись ревью** от Claude Code Action перед мержем

## Project Overview

OmniMap Backend is a Django REST API service for managing hierarchical block-based data structures (trees). Part of the OmniMap platform microservices architecture.

**Stack:** Django 4.2, Django REST Framework, Celery, PostgreSQL (psqlextra), RabbitMQ, Redis, SimpleJWT

## Common Commands

```bash
# Development server
python manage.py runserver

# Run all tests
pytest

# Run specific test file
pytest api/tests/test_views_copy_block.py

# Run specific test
pytest api/tests/test_views_copy_block.py::TestClassName::test_method_name -v

# Migrations
python manage.py makemigrations
python manage.py migrate

# Celery worker (required for async tasks like permissions propagation)
celery -A block_api worker --loglevel=info

# Collect static files
python manage.py collectstatic
```

## Environment

Requires `.env` file with:
- `SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
- `SQL_NAME`, `SQL_USER`, `SQL_PASSWD`, `SQL_HOST`, `SQL_PORT`
- `RABBITMQ_USER`, `RABBITMQ_PASS`, `RABBITMQ_HOST_PORT`, `RABBITMQ_EXCHANGES`, `RABBITMQ_QUEUE`, `RABBITMQ_ROUTING_KEY`
- `REDIS_HOST`, `SERVICE_BLOCK_ID`, `FRONTEND_HOST`

## Architecture

### Django Project Structure
- `block_api/` - Django project settings, Celery config, URL routing
- `api/` - Main application with models, views, serializers, Celery tasks

### Core Models (`api/models.py`)
- **Block** - Hierarchical tree node with UUID primary key, parent reference (adjacency list), JSON `data` field containing `childOrder` array
- **BlockPermission** - User-level permissions on blocks (`view`, `edit`, `deny`, `edit_ac`, `delete`)
- **BlockLink** - Source→target references between blocks (link blocks)
- **BlockUrlLinkModel** - Public URL slugs for blocks
- **Group** - User groups for batch permission assignment

### Views Organization
- `api/views.py` - Main block CRUD, tree operations, import, search
- `api/views_group.py` - Group management endpoints
- `api/views_url.py` - Public URL/slug management
- `api/views_history.py` - Block history and undo functionality
- `api/view_delete_tree.py` - Tree deletion logic

### Async Tasks (`api/tasks.py`)
Celery tasks handle:
- `send_message_block_update` / `send_message_blocks_update` - WebSocket notifications via RabbitMQ
- `set_block_permissions_task` / `set_block_group_permissions_task` - Recursive permission propagation
- `import_blocks_task` - Bulk block import

### Key Utilities (`api/utils/`)
- `query.py` - Raw SQL for recursive permission updates
- `decorators.py` - Permission check decorators
- `calc_custom_grid.py` - Custom grid layout calculations

### Serializers (`api/serializers.py`)
Uses `get_object_for_block()` and `get_forest_serializer()` for block/tree serialization with depth limiting.

## API Prefix

All endpoints under `/api/v1/`. See `docs/api_overview.md` for complete API documentation.

## Settings Notes (`block_api/settings.py`)

- `MAX_DEPTH_LOAD = 5` - Max tree depth loaded per request
- `LINK_LOAD_DEPTH_LIMIT = 10` - Max depth for link block loading
- `LIMIT_BLOCKS = 1000` - Max blocks per operation
- Database uses `psqlextra.backend` for PostgreSQL-specific features
- `simple_history` tracks block changes

## Testing

Tests in `api/tests/`:
- Use `pytest-django` with fixtures
- Test files: `test_delete_tree.py`, `test_import_blocks.py`, `test_load_views.py`, `test_views_copy_block.py`
- `locustfile.py` for load testing

## Cross-Service Changes (ВАЖНО!)

**НИКОГДА не изменяй код других сервисов напрямую!**

Если изменения в backend требуют изменений в других сервисах:

1. **НЕ редактируй** файлы в `omnimap-front`, `llm-gateway` или `omnimap-sync`
2. **Создай файл задач** `FRONTEND_TASKS.md` или `SERVICE_TASKS.md` в корне этого репозитория:
   ```markdown
   # Задачи для других сервисов

   ## omnimap-front
   - [ ] Обновить вызов API /api/v1/blocks (новый формат ответа)
   - [ ] Добавить обработку нового поля "metadata"

   ## llm-gateway
   - [ ] Синхронизировать модель пользователя

   ## omnimap-sync
   - [ ] Обновить формат сообщения block_update
   ```
3. **В PR укажи**, что требуются изменения в других сервисах
4. Агент, работающий над соответствующим сервисом, выполнит задачи и создаст отдельный PR

**Причина:** Каждый сервис имеет свои тесты. Изменения без прогона тестов ломают CI/CD.

## Incoming Tasks

Проверь файл `BACKEND_TASKS.md` (если существует) — там могут быть задачи от фронтенда или других сервисов.
