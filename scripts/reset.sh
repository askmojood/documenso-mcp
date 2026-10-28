#!/usr/bin/env bash
# Start over: delete the local Documenso and its data, set it up again, and fill it with the SAMPLE contracts.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f docker/.env ] && docker compose -f docker/compose.yml --env-file docker/.env down -v
rm -f .env docker/.env docker/cert.p12 docker/backdate.sql
./scripts/up.sh
set -a; . ./.env; set +a
uv run documenso-seed
docker compose -f docker/compose.yml --env-file docker/.env exec -T database psql -q -v ON_ERROR_STOP=1 -U documenso documenso \
  < docker/backdate.sql
