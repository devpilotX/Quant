#!/bin/sh
# Restore the quantsys DB from a pg_dump custom-format file.
#   ./restore.sh backups/quantsys-YYYYMMDD-HHMM.dump
# Stops the writers first so the restore is consistent.
set -eu

DUMP="${1:?usage: restore.sh <dumpfile>}"

docker compose stop engine api
docker compose exec -T postgres dropdb -U quantsys --if-exists quantsys_restore || true
docker compose exec -T postgres createdb -U quantsys quantsys_restore
docker compose exec -T postgres pg_restore -U quantsys -d quantsys_restore < "$DUMP"
echo "Restored into quantsys_restore. Verify, then swap:"
echo "  docker compose exec postgres psql -U quantsys -c 'ALTER DATABASE quantsys RENAME TO quantsys_old; ALTER DATABASE quantsys_restore RENAME TO quantsys;'"
echo "  docker compose start api engine"
