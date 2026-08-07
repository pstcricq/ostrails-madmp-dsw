#!/usr/bin/env bash
# Brings the DSW stack up from nothing, in one command, locally or in a
# Codespace. The same file runs in both, which is the point: running it on a
# laptop is a real rehearsal of what a Codespace does, rather than a second code
# path that only the cloud ever executes.
#
#   1. .env            copied from the example if absent
#   2. the three URLs  computed from where we are
#   3. four secrets    generated if still empty, before the first `up`
#   4. the stack       docker compose up -d
#   5. the bucket      DSW does not create its own
#   6. the admin       your own account, replacing the seeded demo ones
#
# Idempotent throughout. An existing value is never overwritten, which matters
# more than it looks: the Postgres account is created once at the first initdb,
# and regenerating the RSA key would invalidate every token already issued.
set -euo pipefail
cd "$(dirname "$0")/.."

# The stack is always reached through the published port, even in a Codespace
# where the browser uses the forwarded URL. The script runs on the host.
API="http://127.0.0.1:3000/wizard-api"
DEMO_EMAIL="albert.einstein@example.com"
DEMO_PASSWORD="password"
# Seeded by DSW and safe to remove. system@example.com is NOT in this list: it
# is the account DSW uses for its own internal operations.
DEMO_ACCOUNTS="albert.einstein@example.com isaac.newton@example.com nikola.tesla@example.com"

# --- helpers ----------------------------------------------------------------

# Value of a key in .env, empty when unset or empty. Only for single-line keys.
env_get() { sed -n "s/^$1=//p" .env | head -1 | tr -d '"'; }

# Replaces a single-line key in place. `|` as the delimiter because values here
# are URLs and hex strings, never a pipe. The .bak dance keeps GNU and BSD sed
# both happy.
env_set() { sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; }

# Builds JSON with python rather than string interpolation, so a password
# holding a quote or a backslash cannot break the request or inject into it.
json() { python3 -c 'import json,sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }

# Logs in and prints a token, or prints nothing and returns 1.
login() {
  curl -fsS -X POST "$API/tokens" -H 'Content-Type: application/json' \
    -d "$(json email "$1" password "$2")" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])' 2>/dev/null
}

# --- 1. .env ----------------------------------------------------------------
# Gitignored, so a fresh clone does not have one. A new Codespace is exactly
# that, which is why this is created rather than demanded.
if [ ! -f .env ]; then
  [ -f .env.example ] || { echo "error: neither .env nor .env.example found" >&2; exit 1; }
  cp .env.example .env
  chmod 600 .env
  echo "Created .env from .env.example."
fi

# --- 2. Where are we? -------------------------------------------------------
# The three URLs are the only values that depend on where the stack runs,
# because they are what the *browser* resolves, and in a Codespace the browser
# sits on another machine entirely. CODESPACE_NAME is set by GitHub and exists
# nowhere else, so its absence is what "local" means.
if [ -n "${CODESPACE_NAME:-}" ]; then
  DOMAIN="${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
  env_set API_URL    "https://${CODESPACE_NAME}-3000.${DOMAIN}/wizard-api"
  env_set CLIENT_URL "https://${CODESPACE_NAME}-8080.${DOMAIN}/wizard"
  # No path: S3_URL is a bare origin, presigned URLs append bucket and key.
  env_set S3_URL     "https://${CODESPACE_NAME}-9000.${DOMAIN}"
  echo "Codespace detected, the three URLs point at the forwarded domain."
else
  echo "Local run, the three URLs stay on localhost."
fi

# --- 3. Secrets -------------------------------------------------------------
# The four values .env.example ships empty. Filled here, before the first `up`,
# and never touched again.
generated=""

# Lengths are not a matter of taste here. GENERAL_SECRET must be exactly 32
# ASCII characters, which is 16 bytes in hex: the server refuses to start
# otherwise, with "general.secret must have 32 ASCII characters". The two
# passwords have no such constraint, so they get more.
for key in POSTGRES_PASSWORD MINIO_ROOT_PASSWORD; do
  if [ -z "$(env_get "$key")" ]; then
    env_set "$key" "$(openssl rand -hex 24)"
    generated="$generated $key"
  fi
done

if [ -z "$(env_get GENERAL_SECRET)" ]; then
  env_set GENERAL_SECRET "$(openssl rand -hex 16)"
  generated="$generated GENERAL_SECRET"
fi

# The RSA key is the one multi-line value, which docker compose accepts between
# double quotes. It is appended rather than substituted in place, which is why
# .env.example keeps it as its last key.
if ! grep -q '^GENERAL_RSA_PRIVATE_KEY="-----BEGIN' .env; then
  # -traditional forces PKCS#1, the "BEGIN RSA PRIVATE KEY" form DSW expects.
  # OpenSSL 3 writes PKCS#8 without it. LibreSSL, which is what stock macOS
  # ships, does not know the flag but already writes PKCS#1, hence the fallback
  # and the check that follows: a silently wrong format would only surface much
  # later, as a server that refuses to start.
  key="$(openssl genrsa -traditional 4096 2>/dev/null || openssl genrsa 4096 2>/dev/null)"
  case "$key" in
    "-----BEGIN RSA PRIVATE KEY-----"*) ;;
    *) echo "error: openssl produced a key that is not PKCS#1, DSW will reject it" >&2; exit 1 ;;
  esac
  sed -i.bak '/^GENERAL_RSA_PRIVATE_KEY=/d' .env && rm -f .env.bak
  printf 'GENERAL_RSA_PRIVATE_KEY="%s"\n' "$key" >> .env
  generated="$generated GENERAL_RSA_PRIVATE_KEY"
fi

[ -z "$generated" ] || echo "Generated:$generated"

# --- 4. The stack -----------------------------------------------------------
echo "Starting the stack..."
docker compose up -d

# --- 5. The bucket ----------------------------------------------------------
# DSW does not create it: in the S3 API, CreateBucket and PutObject are separate
# operations, and writing to a missing bucket returns NoSuchBucket. The service
# waits on MinIO's healthcheck by itself, so there is no sleep here.
docker compose run --rm createbucket

# --- 6. Wait for the API ----------------------------------------------------
# `up -d` returns once the containers are created, not once they answer. The
# first boot also runs database migrations, which take a while under emulation.
printf 'Waiting for the DSW API'
for _ in $(seq 1 120); do
  if curl -fs -o /dev/null "$API/configs/bootstrap"; then echo " ready."; break; fi
  printf '.'
  sleep 2
done
curl -fs -o /dev/null "$API/configs/bootstrap" || {
  echo ""
  echo "error: the API did not answer, check 'docker compose logs server'" >&2
  exit 1
}

# --- 7. Your admin account --------------------------------------------------
# DSW seeds three demo accounts whose addresses and password are published. On a
# Codespace with port 3000 public, that is the whole security of the instance.
#
# The order matters and is not negotiable: create yours, prove it logs in, and
# only then delete the seeded ones. The reverse locks you out of your own
# instance with no way back but psql.
notes=""
if ! command -v python3 > /dev/null; then
  notes="$notes
 (!) python3 is missing, so the admin bootstrap was skipped. It is the only
     step that needs it, for building and reading JSON safely."
elif [ -z "${DSW_ADMIN_EMAIL:-}" ] || [ -z "${DSW_ADMIN_PASSWORD:-}" ]; then
  # Only warn if the demo account really answers. Once it has been removed by an
  # earlier run, saying the instance is wide open would be plainly false, and a
  # security warning that cries wolf is worse than none.
  if login "$DEMO_EMAIL" "$DEMO_PASSWORD" > /dev/null 2>&1; then
    notes="$notes
 (!) DSW_ADMIN_EMAIL / DSW_ADMIN_PASSWORD are not set, so the demo accounts
     were left alone. Anyone reaching this instance can log in as
     $DEMO_EMAIL / $DEMO_PASSWORD.
     Set them as Codespaces Secrets before making port 3000 public."
  fi
elif login "$DSW_ADMIN_EMAIL" "$DSW_ADMIN_PASSWORD" > /dev/null 2>&1; then
  echo "Admin account $DSW_ADMIN_EMAIL already in place."
else
  TOKEN="$(login "$DEMO_EMAIL" "$DEMO_PASSWORD" || true)"
  if [ -z "$TOKEN" ]; then
    notes="$notes
 (!) Neither $DSW_ADMIN_EMAIL nor the demo account could log in. The admin
     account was left untouched."
  else
    echo "Creating admin account $DSW_ADMIN_EMAIL..."
    curl -fsS -X POST "$API/users" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d "$(json email "$DSW_ADMIN_EMAIL" firstName "DSW" lastName "Admin" \
                 password "$DSW_ADMIN_PASSWORD" role admin)" > /dev/null

    # The proof, not the assumption.
    NEW_TOKEN="$(login "$DSW_ADMIN_EMAIL" "$DSW_ADMIN_PASSWORD" || true)"
    if [ -z "$NEW_TOKEN" ]; then
      notes="$notes
 (!) The new admin account was created but cannot log in, so the demo accounts
     were kept. Look into it before exposing this instance."
    else
      for email in $DEMO_ACCOUNTS; do
        uuid="$(curl -fsS "$API/users?q=$email" -H "Authorization: Bearer $NEW_TOKEN" \
          | python3 -c 'import json,sys
d = json.load(sys.stdin)
users = d.get("_embedded", {}).get("users", [])
print(next((u["uuid"] for u in users if u["email"] == sys.argv[1]), ""))' "$email")"
        [ -z "$uuid" ] || curl -fsS -X DELETE "$API/users/$uuid" \
          -H "Authorization: Bearer $NEW_TOKEN" > /dev/null
      done
      echo "Demo accounts removed, $DSW_ADMIN_EMAIL is the only admin."
    fi
  fi
fi

# --- 8. Summary -------------------------------------------------------------
echo ""
echo "======================================================================"
echo " DSW is up."
echo " Client : $(env_get CLIENT_URL)"
echo " API    : $(env_get API_URL)"
[ -z "$notes" ] || echo "$notes"
echo ""
echo " Credentials live in .env and nowhere else. Read one with, for example:"
echo "   grep MINIO_ROOT_PASSWORD .env"
echo ""
echo " Re-running this script is safe. Deleting .env to get fresh secrets is"
echo " not: the Postgres account is created once, so that needs a"
echo " 'docker compose down -v' first."
echo "======================================================================"
