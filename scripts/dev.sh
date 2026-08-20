#!/usr/bin/env bash
set -euo pipefail
# Local dev server. Expects a local .env and a reachable PostgreSQL and Redis.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
