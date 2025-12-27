# Задачи для omnimap-sync

## WebSocket события для напоминаний и подписок

После мержа PR #7 (notifications system) в omnimap-back, необходимо добавить поддержку WebSocket событий в omnimap-sync.

### Новые события

#### Напоминания (Reminders)

```json
// reminder_created
{
  "type": "reminder_created",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "remind_at": "2025-01-15T10:00:00Z",
    "timezone": "Europe/Moscow",
    "message": "Проверить задачу",
    "repeat": "none"
  }
}

// reminder_updated
{
  "type": "reminder_updated",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "remind_at": "2025-01-16T10:00:00Z",
    "timezone": "Europe/Moscow",
    "message": "Обновлённое сообщение",
    "repeat": "daily"
  }
}

// reminder_deleted
{
  "type": "reminder_deleted",
  "data": {
    "id": "uuid",
    "block_id": "uuid"
  }
}

// reminder_triggered (напоминание сработало)
{
  "type": "reminder_triggered",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "message": "Напоминание сработало"
  }
}

// reminder_snoozed (напоминание отложено)
{
  "type": "reminder_snoozed",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "snoozed_until": "2025-01-15T10:30:00Z"
  }
}
```

#### Подписки (Subscriptions)

```json
// subscription_created
{
  "type": "subscription_created",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "depth": 1,
    "on_text_change": true,
    "on_data_change": true,
    "on_move": true,
    "on_child_add": true,
    "on_child_delete": true
  }
}

// subscription_updated
{
  "type": "subscription_updated",
  "data": {
    "id": "uuid",
    "block_id": "uuid",
    "depth": 2,
    "on_text_change": false,
    "on_data_change": true,
    "on_move": false,
    "on_child_add": true,
    "on_child_delete": true
  }
}

// subscription_deleted
{
  "type": "subscription_deleted",
  "data": {
    "id": "uuid",
    "block_id": "uuid"
  }
}
```

### Реализация

1. **Добавить обработчики сообщений** в RabbitMQ consumer:
   - `reminder_created`
   - `reminder_updated`
   - `reminder_deleted`
   - `reminder_triggered`
   - `reminder_snoozed`
   - `subscription_created`
   - `subscription_updated`
   - `subscription_deleted`

2. **Маршрутизация по user_id** — события должны доставляться только владельцу напоминания/подписки

3. **Формат сообщений** — использовать существующий формат WebSocket сообщений

### Источник событий

Backend (omnimap-back) отправляет события через RabbitMQ:
- Exchange: `omnimap` (direct)
- Routing key: `block_update` (существующий) или новый `notification_event`

### Файлы для изменения

- `consumer.py` — добавить обработчики новых типов сообщений
- `websocket_handler.py` — добавить отправку событий клиентам

### Пример интеграции (backend → sync)

В omnimap-back уже реализована отправка событий через `send_message_block_update`:

```python
# api/tasks.py
send_message_block_update({
    'type': 'reminder_created',
    'user_id': user.id,
    'data': {
        'id': str(reminder.id),
        'block_id': str(reminder.block_id),
        'remind_at': reminder.remind_at.isoformat(),
        ...
    }
})
```

### Тестирование

1. Создать напоминание через API
2. Проверить получение `reminder_created` через WebSocket
3. Обновить напоминание
4. Проверить получение `reminder_updated`
5. Аналогично для подписок
