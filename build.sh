#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate --noinput

if [ "$CREATE_SUPERUSER_ON_DEPLOY" = "True" ]; then
  python manage.py createsuperuser --noinput || true
fi
