FROM python:3.10-slim
#FROM python:3.10

WORKDIR /block_api

COPY . ./
RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=block_api.settings

RUN chmod +x /block_api/entrypoint.sh
ENTRYPOINT ["/bin/sh", "/block_api/entrypoint.sh"]
