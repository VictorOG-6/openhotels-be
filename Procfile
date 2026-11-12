release: python -c "from database import init_db; init_db()"
web: uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level warning --no-access-log