#!/bin/sh

python manage.py migrate
python manage.py create_initial_data
python manage.py runserver 0.0.0.0:8000
