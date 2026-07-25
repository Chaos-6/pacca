#!/bin/sh
# =============================================================================
# PACCA Docker entrypoint
#
# Alembic is the single source of truth for the schema (C5). Before the app
# starts, apply every pending migration so the schema (including the auth
# `users` table — migration 004) exists before uvicorn accepts traffic.
#
# POSIX sh (not bash): the production image's userland runs whatever shell
# is on PATH, and #!/bin/sh is symlinked to dash on many minimal images —
# bashisms silently break there (see docs/AGENT_LESSONS.md L-023 pattern).
# =============================================================================
set -e

echo "docker-entrypoint: running 'alembic upgrade head'..."
alembic upgrade head

echo "docker-entrypoint: migrations applied, starting app..."
exec "$@"
