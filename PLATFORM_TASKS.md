# Задачи для omnimap-platform (инфраструктура)

## Контекст
В omnimap-back добавлена загрузка изображений. Требуется обновить конфигурации Docker и Kubernetes.

---

## 1. Docker Compose (локальная разработка)

### Новые переменные окружения в `.env.example`:

```env
# File Storage
FILE_STORAGE_BACKEND=local  # 'local' для разработки, 's3' для прода

# S3 Storage (опционально, для тестирования S3)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_STORAGE_BUCKET_NAME=
AWS_S3_ENDPOINT_URL=
AWS_S3_REGION_NAME=ru-central1
```

### Изменения в `docker-compose.yml`:

```yaml
services:
  omnimap-back:
    # ... existing config ...
    environment:
      - FILE_STORAGE_BACKEND=${FILE_STORAGE_BACKEND:-local}
    volumes:
      # Добавить volume для медиа-файлов
      - media_data:/app/media

volumes:
  # Добавить новый volume
  media_data:
```

### Nginx конфиг (если используется):

```nginx
# Раздача медиа-файлов
location /media/ {
    alias /app/media/;
    expires 30d;
    add_header Cache-Control "public, immutable";
}
```

---

## 2. Kubernetes (production)

### Новые секреты для S3:

```yaml
# deploy/kubernetes/base/secrets.yaml (или через sealed-secrets)
apiVersion: v1
kind: Secret
metadata:
  name: s3-credentials
type: Opaque
stringData:
  AWS_ACCESS_KEY_ID: "<value>"
  AWS_SECRET_ACCESS_KEY: "<value>"
  AWS_STORAGE_BUCKET_NAME: "omnimap-media"
  AWS_S3_ENDPOINT_URL: "https://s3.cloud.ru"  # для cloud.ru
  AWS_S3_REGION_NAME: "ru-central1"
```

### Обновить Deployment omnimap-back:

```yaml
# deploy/kubernetes/base/deployments/omnimap-back.yaml
spec:
  template:
    spec:
      containers:
        - name: omnimap-back
          env:
            # Добавить новые переменные
            - name: FILE_STORAGE_BACKEND
              value: "s3"
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: AWS_ACCESS_KEY_ID
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: AWS_SECRET_ACCESS_KEY
            - name: AWS_STORAGE_BUCKET_NAME
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: AWS_STORAGE_BUCKET_NAME
            - name: AWS_S3_ENDPOINT_URL
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: AWS_S3_ENDPOINT_URL
            - name: AWS_S3_REGION_NAME
              valueFrom:
                secretKeyRef:
                  name: s3-credentials
                  key: AWS_S3_REGION_NAME
```

---

## 3. Зависимости (requirements.txt)

Для S3 хранилища нужно добавить:

```
django-storages==1.14.2
boto3==1.34.0
```

**Примечание:** Эти пакеты нужны только если `FILE_STORAGE_BACKEND=s3`. Для локальной разработки они не обязательны.

---

## 4. Миграции

После деплоя нужно применить миграцию:

```bash
python manage.py migrate api 0002_add_blockfile_model
```

---

## 5. S3 Bucket настройка (cloud.ru)

Создать bucket с настройками:
- Имя: `omnimap-media`
- Публичный доступ на чтение (для URL изображений)
- CORS policy:

```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["https://omnimap.cloud.ru", "http://localhost:3000"],
      "AllowedMethods": ["GET"],
      "AllowedHeaders": ["*"],
      "MaxAgeSeconds": 3600
    }
  ]
}
```

---

## Чеклист

- [ ] Обновить `infrastructure/.env.example`
- [ ] Обновить `infrastructure/docker-compose.yml` (volume для media)
- [ ] Добавить S3 секреты в Kubernetes
- [ ] Обновить Deployment omnimap-back
- [ ] Создать S3 bucket в cloud.ru
- [ ] Добавить django-storages и boto3 в requirements.txt
- [ ] Протестировать локально с `FILE_STORAGE_BACKEND=local`
- [ ] Протестировать с S3 (опционально)
