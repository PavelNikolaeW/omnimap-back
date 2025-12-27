# Задачи для omnimap-back

## Незавершённые задачи

### Асинхронная генерация превью через Celery
**Приоритет:** Низкий

Для больших изображений генерировать превью в фоновом режиме.

```python
# api/tasks.py
@shared_task
def generate_thumbnail_task(block_file_id):
    block_file = BlockFile.objects.get(id=block_file_id)
    thumbnail_content = create_thumbnail(block_file.file)
    if thumbnail_content:
        thumb_filename = f"thumb_{block_file.id}.jpg"
        block_file.thumbnail.save(thumb_filename, thumbnail_content, save=True)
```

---

### Email уведомления
**Приоритет:** Средний

- [ ] Сервис отправки email (api/services/email.py)
- [ ] Шаблоны писем для напоминаний и уведомлений

---

### Push уведомления (доработка)
**Приоритет:** Средний

- [ ] Генерация VAPID ключей (команда для генерации)
- [ ] Тесты для push уведомлений

---

### Документация API
**Приоритет:** Низкий

- [ ] Swagger/OpenAPI документация для endpoints напоминаний
- [ ] Swagger/OpenAPI документация для endpoints подписок

---

## Рекомендации из Code Review (PR #7)

### Улучшение обработки ошибок
- [ ] Добавить более детальное логирование ошибок в Celery tasks
- [ ] Логировать неудачные отправки уведомлений

### Circuit Breaker для внешних сервисов
- [ ] Реализовать circuit breaker для Telegram API
- [ ] Реализовать circuit breaker для Push API
- [ ] Автоматическое отключение канала при множественных ошибках

### Производительность
- [ ] Добавить кэширование для UserNotificationSettings
- [ ] Использовать bulk-операции для массовых уведомлений
