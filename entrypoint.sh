#!/bin/sh

python manage.py migrate
python manage.py create_initial_data
#python manage.py runserver 0.0.0.0:8000
gunicorn block_api.wsgi --workers 3 --bind 0.0.0.0:8000
celery -A block_api worker --loglevel=info --concurrency=10