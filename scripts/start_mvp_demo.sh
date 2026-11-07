#!/usr/bin/env bash
set -euo pipefail
task_project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_project_dir"
export DJANGO_DB_PATH="$task_project_dir/.mvp-demo.sqlite3"
export DJANGO_DEBUG=true
export DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
uv sync --frozen
uv run python manage.py migrate --noinput
uv run python manage.py seed_mvp_demo
exec uv run python manage.py runserver "127.0.0.1:${MVP_PORT:-8011}" --noreload
