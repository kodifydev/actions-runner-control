#!/usr/bin/env bash
set -euo pipefail
# Disposable local test DB only. No published ports, host mounts or production credentials.
# Force UTF-8 even when the minimal runner image defaults to the C locale.
# SQL_ASCII makes psycopg return bytes and breaks SQLAlchemy dialect startup.
initdb -D "$HOME/test-postgres" --username=postgres --auth=trust --encoding=UTF8 --locale=C.UTF-8 >/dev/null
pg_ctl -D "$HOME/test-postgres" -l "$HOME/postgres.log" \
  -o "-h 127.0.0.1 -k $HOME/test-postgres-socket -c shared_buffers=128MB -c max_connections=100 -c statement_timeout=300000" -w start >/dev/null
if [[ "${1:-}" == "--probe" ]]; then
  python -c 'import platform; print(platform.machine())'
  psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION vector; SHOW statement_timeout; SELECT version();'
  test ! -e /var/run/docker.sock
  exit 0
fi
if ! IFS= read -r config || [[ -z "$config" ]]; then
  printf '%s\n' 'Missing ephemeral runner configuration' >&2
  exit 1
fi
exec ./run.sh --jitconfig "$config"
