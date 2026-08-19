#!/usr/bin/env bash
# Reports the configuration compose will resolve, brings the DSW stack up, then
# creates the bucket. It writes nothing and can be re-run. The two commands it
# wraps:
#
#   docker compose up -d --wait
#   docker compose run --rm createbucket

set -euo pipefail
cd "$(dirname "$0")/.."

# The API, as reached from this host.
API="http://127.0.0.1:3000/wizard-api"
DEMO_EMAIL="albert.einstein@example.com"
DEMO_PASSWORD="password"

# Printed with their value. None is a secret.
SHOWN="DSW_VERSION MADMP_CORE_VERSION POSTGRES_VERSION MINIO_VERSION MC_VERSION
       POSTGRES_DB POSTGRES_USER MINIO_ROOT_USER
       API_URL CLIENT_URL S3_URL S3_BUCKET
       REGISTRY_OWNER REGISTRY_REPO"

# Reported as set or MISSING, never printed.
SECRET="POSTGRES_PASSWORD MINIO_ROOT_PASSWORD SUBMISSION_TOKEN REGISTRY_TOKEN
        GENERAL_SECRET GENERAL_RSA_PRIVATE_KEY"

# --- helpers ----------------------------------------------------------------

# The value compose will use. The environment wins over .env, so reading the
# file alone would report a value the stack never sees.
value_of() {
  if [ -n "${!1:-}" ]; then printf '%s' "${!1}"; return; fi
  sed -n "s/^$1=//p" .env | head -1 | tr -d '"'
}

origin_of() {
  if [ -n "${!1:-}" ]; then echo "environment"; else echo ".env"; fi
}

# --- 1. .env ----------------------------------------------------------------
if [ ! -f .env ]; then
  echo "error: no .env here." >&2
  echo "  cp .env.example .env, then fill in the empty values." >&2
  exit 1
fi

# --- 2. Can compose read it? ------------------------------------------------
# Catches what the key-by-key report below cannot: a multi-line value left
# without its quotes, which makes compose read every following line as a new
# variable.
if ! docker compose config --quiet 2>/dev/null; then
  echo "error: docker compose cannot read .env:" >&2
  docker compose config --quiet 2>&1 | sed 's/^/  /' >&2
  exit 1
fi

# --- 3. What the stack will run with ----------------------------------------
echo "Configuration:"
missing=""

for key in $SHOWN; do
  value="$(value_of "$key")"
  if [ -z "$value" ]; then
    missing="$missing $key"
    printf '  %-24s %-40s %s\n' "$key" "MISSING" "-"
  else
    printf '  %-24s %-40s %s\n' "$key" "$value" "$(origin_of "$key")"
  fi
done

for key in $SECRET; do
  if [ -z "$(value_of "$key")" ]; then
    missing="$missing $key"
    printf '  %-24s %-40s %s\n' "$key" "MISSING" "-"
  else
    printf '  %-24s %-40s %s\n' "$key" "set" "$(origin_of "$key")"
  fi
done

# Keys .env.example has gained since this .env was written. Reported, never
# copied in.
absent=""
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
  key="${line%%=*}"
  grep -q "^$key=" .env || absent="$absent $key"
done < .env.example
[ -z "$absent" ] || {
  echo ""
  echo "(!) .env.example carries keys your .env does not:$absent"
  echo "    Add them, with a value."
}

if [ -n "$missing" ]; then
  echo ""
  echo "error: nothing can start until these are set in .env:$missing" >&2
  exit 1
fi

# --- 4. The stack -----------------------------------------------------------
# --wait returns once every healthcheck passes, not once the containers exist.
echo ""
echo "Starting the stack..."
docker compose up -d --wait || {
  echo "error: the stack did not come up, check 'docker compose logs'" >&2
  exit 1
}

# --- 5. The bucket ----------------------------------------------------------
# DSW does not create the bucket itself. Repeating this is free.
docker compose run --rm createbucket

# --- 6. Summary -------------------------------------------------------------
echo ""
echo "======================================================================"
echo " DSW is up."
echo " Client : $(value_of CLIENT_URL)"
echo " API    : $(value_of API_URL)"

# Warned about only when the account really answers.
if curl -fs -o /dev/null -X POST "$API/tokens" -H 'Content-Type: application/json' \
     -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\"}" 2>/dev/null; then
  echo ""
  echo " (!) The seeded demo accounts still open this instance:"
  echo "         $DEMO_EMAIL / $DEMO_PASSWORD"
  echo "     Create your own admin and delete these three before this instance"
  echo "     is reachable from anywhere but this machine."
fi

echo "======================================================================"