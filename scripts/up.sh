#!/usr/bin/env bash
# Start the local Documenso, then the first time: sign up the demo's user "Claude Connector" through Documenso's own
# sign-up endpoint, mark its email verified (Mailpit holds the email; nothing goes out), and give its team an API
# token, stored the way Documenso stores them (SHA-512 of the token). Every secret is generated here and stays in the
# git-ignored files docker/.env, docker/cert.p12 and .env; none is printed.
set -euo pipefail
cd "$(dirname "$0")/.."
dc() { docker compose -f docker/compose.yml --env-file docker/.env "$@"; }
rnd() { openssl rand -hex 16; }

if [ ! -f docker/.env ]; then
  cat > docker/.env <<ENV
POSTGRES_USER=documenso
POSTGRES_PASSWORD=$(rnd)
POSTGRES_DB=documenso
NEXTAUTH_SECRET=$(openssl rand -hex 32)
NEXT_PRIVATE_ENCRYPTION_KEY=$(openssl rand -hex 32)
NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY=$(openssl rand -hex 32)
SIGNING_PASSPHRASE=$(rnd)
ADMIN_PASSWORD=$(rnd)Aa1!
ENV
fi
if [ ! -f docker/cert.p12 ]; then
  pass=$(grep '^SIGNING_PASSPHRASE=' docker/.env | cut -d= -f2)
  tmp=$(mktemp -d)
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=Harbor Trading (SAMPLE) signing" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
  openssl pkcs12 -export -legacy -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -out docker/cert.p12 -passout "pass:$pass" 2>/dev/null \
    || openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" -out docker/cert.p12 -passout "pass:$pass"
  rm -rf "$tmp"; chmod 644 docker/cert.p12
fi

dc up -d
echo "waiting for Documenso…"
for _ in $(seq 1 120); do
  curl -fsS -o /dev/null http://127.0.0.1:3020/signin 2>/dev/null && break
  sleep 3
done

if ! grep -q '^DOCUMENSO_TOKEN=' .env 2>/dev/null; then
  pw=$(grep '^ADMIN_PASSWORD=' docker/.env | cut -d= -f2-)
  email=admin@harbor-trading.example
  curl -fsS -o /dev/null -H 'Content-Type: application/json' -H 'Origin: http://localhost:3020' \
    http://localhost:3020/api/auth/email-password/signup \
    -d "{\"name\":\"Claude Connector\",\"email\":\"$email\",\"password\":\"$pw\"}"
  token="api_$(openssl rand -hex 12)"
  hash=$(printf %s "$token" | openssl dgst -sha512 | awk '{print $NF}')
  psql() { dc exec -T database psql -v ON_ERROR_STOP=1 -qtA -U documenso documenso; }
  team=$(psql <<SQL
UPDATE "User" SET "emailVerified" = now() WHERE email = '$email';
SELECT t.id FROM "Team" t JOIN "Organisation" o ON o.id = t."organisationId" JOIN "User" u ON u.id = o."ownerUserId"
 WHERE u.email = '$email' ORDER BY t.id LIMIT 1;
SQL
)
  team=$(echo "$team" | tail -1)
  psql <<SQL
UPDATE "Team" SET name = 'Harbor Trading (SAMPLE)' WHERE id = $team;
UPDATE "Organisation" SET name = 'Harbor Trading (SAMPLE)' WHERE id = (SELECT "organisationId" FROM "Team" WHERE id = $team);
SQL
  psql <<SQL
INSERT INTO "ApiToken" (name, token, "userId", "teamId", "createdAt")
SELECT 'mojood-mcp', '$hash', u.id, $team, now() FROM "User" u WHERE u.email = '$email';
SQL
  { echo "DOCUMENSO_URL=http://127.0.0.1:3020"; echo "DOCUMENSO_TOKEN=$token"; } >> .env
  echo "user, team $team and API token created (in docker/.env and .env)"
fi
echo "Documenso is up at http://localhost:3020 (mail caught at http://localhost:8026)"
