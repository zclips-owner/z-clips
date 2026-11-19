#!/bin/bash
set -e
# Create tables (safe to run on every start)
python -m alembic upgrade head
# Clip worker in the background (Render free tier has no separate worker service)
python -m app.worker &
# Web server in the foreground
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
