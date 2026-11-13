#!/bin/sh
set -e

echo "Ensuring database schema..."
python -c "from database import init_db; init_db()"

echo "Starting application..."
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 4 --log-level warning --no-access-log