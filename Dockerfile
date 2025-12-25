FROM python:3.10-slim

WORKDIR /block_api

# Установим зависимости системы для supervisor и прочего
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc-dev libffi-dev \
    curl supervisor \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Скопируем проект
COPY . ./

# Установим зависимости Python
RUN pip install --no-cache-dir -r requirements.txt

# Установим переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=block_api.settings \
    PATH="/block_api/venv/bin:$PATH"

# Открываем порт
EXPOSE 8000

# Старт Django
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]