# Задачи для omnimap-back

## Улучшения загрузки изображений

Рекомендации из code review PR #5.

### 1. Проверка максимальных размеров изображения
**Приоритет:** Средний

Добавить валидацию максимальных размеров (ширина/высота) для предотвращения загрузки очень больших изображений.

```python
# В settings.py
MAX_IMAGE_DIMENSIONS = (4096, 4096)  # максимум 4K

# В validate_image()
if width > settings.MAX_IMAGE_DIMENSIONS[0] or height > settings.MAX_IMAGE_DIMENSIONS[1]:
    return False, f'Image too large. Max dimensions: {settings.MAX_IMAGE_DIMENSIONS}', None
```

---

### 2. Асинхронная генерация превью через Celery
**Приоритет:** Низкий (для больших нагрузок)

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

### 3. Сжатие изображений при загрузке
**Приоритет:** Средний

Автоматическое сжатие JPEG изображений для экономии места.

```python
# В settings.py
JPEG_QUALITY = 85
OPTIMIZE_IMAGES = True

# В views_files.py
def optimize_image(file, quality=85):
    img = Image.open(file)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    output = io.BytesIO()
    img.save(output, format='JPEG', quality=quality, optimize=True)
    output.seek(0)
    return ContentFile(output.read())
```

---

### 4. Type hints для лучшей типизации
**Приоритет:** Низкий

Добавить type hints в `views_files.py`:

```python
from typing import Tuple, Optional, Dict, Any
from django.core.files.uploadedfile import UploadedFile

def validate_image(file: UploadedFile) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    ...

def create_thumbnail(image_file: UploadedFile, max_size: Tuple[int, int] = None) -> Optional[ContentFile]:
    ...
```

---

### 5. Централизованная конфигурация MIME-типов
**Приоритет:** Низкий

Вынести маппинг типов в отдельный конфиг:

```python
# api/constants.py
CONTENT_TYPE_MAP = {
    'image/jpeg': {'extensions': ['jpg', 'jpeg'], 'pillow_format': 'JPEG'},
    'image/png': {'extensions': ['png'], 'pillow_format': 'PNG'},
    'image/gif': {'extensions': ['gif'], 'pillow_format': 'GIF'},
    'image/webp': {'extensions': ['webp'], 'pillow_format': 'WEBP'},
}
```

---

## Чеклист

- [ ] Добавить проверку MAX_IMAGE_DIMENSIONS
- [ ] Реализовать асинхронную генерацию превью (опционально)
- [ ] Добавить сжатие JPEG изображений
- [ ] Добавить type hints в views_files.py
- [ ] Вынести CONTENT_TYPE_MAP в constants.py
