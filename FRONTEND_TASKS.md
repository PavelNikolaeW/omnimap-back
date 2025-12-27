# Задачи для omnimap-front

## Интеграция загрузки изображений

Backend поддерживает загрузку изображений в блоки. Необходимо реализовать UI для этой функциональности.

---

### API для работы с файлами блоков

#### Загрузка изображения

```http
POST /api/v1/blocks/<block_id>/file/
Content-Type: multipart/form-data
Authorization: Bearer <token>

file: <binary>
```

**JavaScript пример:**

```javascript
async function uploadBlockImage(blockId, file, token) {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`/api/v1/blocks/${blockId}/file/`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`
    },
    body: formData
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  return response.json();
}
```

**Ответ при успехе (201):**

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "filename": "photo.jpg",
  "content_type": "image/jpeg",
  "size": 102400,
  "width": 1920,
  "height": 1080,
  "url": "http://localhost:8000/media/blocks/block-uuid/file.jpg",
  "thumbnail_url": "http://localhost:8000/media/blocks/block-uuid/thumbs/thumb_file.jpg",
  "created_at": "2025-12-27T16:00:00.000000Z"
}
```

---

#### Получение информации о файле

```http
GET /api/v1/blocks/<block_id>/file/
Authorization: Bearer <token>
```

**JavaScript пример:**

```javascript
async function getBlockImage(blockId, token) {
  const response = await fetch(`/api/v1/blocks/${blockId}/file/`, {
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });

  if (response.status === 404) {
    return null; // Нет файла
  }

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  return response.json();
}
```

---

#### Удаление файла

```http
DELETE /api/v1/blocks/<block_id>/file/
Authorization: Bearer <token>
```

**JavaScript пример:**

```javascript
async function deleteBlockImage(blockId, token) {
  const response = await fetch(`/api/v1/blocks/${blockId}/file/`, {
    method: 'DELETE',
    headers: {
      'Authorization': `Bearer ${token}`
    }
  });

  if (!response.ok && response.status !== 204) {
    const error = await response.json();
    throw new Error(error.detail);
  }

  return true;
}
```

---

### Ограничения

| Параметр | Значение |
|----------|----------|
| Максимальный размер файла | 5 MB |
| Допустимые форматы | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Максимальные размеры | 4096x4096 пикселей |
| Файлов на блок | 1 (повторная загрузка заменяет) |

---

### Коды ошибок

| Код | Причина |
|-----|---------|
| 400 | Файл не передан, слишком большой, неверный формат, размеры превышают лимит |
| 401 | Не авторизован |
| 403 | Нет прав на редактирование блока |
| 404 | Блок не найден или файл отсутствует (для GET/DELETE) |

**Примеры ошибок:**

```json
{"detail": "No file provided"}
{"detail": "File too large. Max size: 5 MB"}
{"detail": "Invalid file type. Allowed: image/jpeg, image/png, image/gif, image/webp"}
{"detail": "Image dimensions too large. Max: 4096x4096"}
{"detail": "Permission denied"}
```

---

### Превью (thumbnail)

При загрузке автоматически создаётся уменьшенная версия изображения:
- Максимальный размер: 300x300 пикселей
- Формат: JPEG
- Сохраняются пропорции

Используйте `thumbnail_url` для отображения в списках и превью, `url` — для полноразмерного просмотра.

---

### Рекомендации по UI

1. **Валидация на клиенте** — проверяйте размер и тип файла до отправки:

```javascript
function validateImage(file) {
  const maxSize = 5 * 1024 * 1024; // 5 MB
  const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];

  if (file.size > maxSize) {
    return { valid: false, error: 'Файл слишком большой (макс. 5 MB)' };
  }

  if (!allowedTypes.includes(file.type)) {
    return { valid: false, error: 'Недопустимый формат файла' };
  }

  return { valid: true };
}
```

2. **Проверка размеров изображения:**

```javascript
function checkImageDimensions(file) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(img.src);
      if (img.width > 4096 || img.height > 4096) {
        resolve({ valid: false, error: 'Изображение слишком большое (макс. 4096x4096)' });
      } else {
        resolve({ valid: true, width: img.width, height: img.height });
      }
    };
    img.onerror = () => resolve({ valid: false, error: 'Не удалось загрузить изображение' });
    img.src = URL.createObjectURL(file);
  });
}
```

3. **Drag & Drop:**

```javascript
function setupDropZone(element, onFile) {
  element.addEventListener('dragover', (e) => {
    e.preventDefault();
    element.classList.add('dragover');
  });

  element.addEventListener('dragleave', () => {
    element.classList.remove('dragover');
  });

  element.addEventListener('drop', (e) => {
    e.preventDefault();
    element.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) {
      onFile(file);
    }
  });
}
```

4. **Прогресс загрузки** (XMLHttpRequest):

```javascript
function uploadWithProgress(blockId, file, token, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status === 201) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(JSON.parse(xhr.responseText).detail));
      }
    });

    xhr.addEventListener('error', () => reject(new Error('Ошибка сети')));

    xhr.open('POST', `/api/v1/blocks/${blockId}/file/`);
    xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.send(formData);
  });
}
```

---

### Чеклист

- [ ] Добавить кнопку загрузки изображения в UI блока
- [ ] Реализовать валидацию файла на клиенте (размер, тип, размеры)
- [ ] Добавить Drag & Drop зону
- [ ] Показывать прогресс загрузки
- [ ] Отображать превью после загрузки (использовать `thumbnail_url`)
- [ ] Добавить возможность удаления изображения
- [ ] Показывать полноразмерное изображение по клику
- [ ] Обработка ошибок с понятными сообщениями
