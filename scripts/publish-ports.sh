#!/usr/bin/env bash
# Makes ports 3000 and 9000 public, from inside the Codespace, at every start.
# Called by postStartCommand, and useful to run by hand if that ever fails.
#
# Why anything has to do this: GitHub never implemented `visibility` in the
# devcontainer's portsAttributes. It is an old feature request, acknowledged and
# left there, which is why our declaration is honoured for `label` and dropped
# for `visibility`. So the ports come up private at creation, and private again
# after every wake-up.
#
# Two things this needs, and neither is obvious. `gh` comes from the github-cli
# feature, the base image has none. And `-c` is required, because from inside a
# codespace gh does not find its own and answers "you have no codespaces".
#
# 8080 stays private on purpose. It is a page you navigate to, and the browser
# sends its GitHub cookie on a first-party navigation. The other two are read
# cross-origin, by the client's XHR and by the browser fetching documents from
# MinIO through a presigned URL, and no cookie travels there. Port 3000 being
# public is also what lets a GitHub Actions runner reach the API.
#
# No `set -e`: a failure here is reported at the end, in one place, rather than
# ending the script wherever it happens to occur.
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -z "${CODESPACE_NAME:-}" ]; then
  echo "publish-ports: not running in a Codespace, nothing to do."
  exit 0
fi

# Wait for both services to answer locally before touching visibility. Measured
# on 2026-08-10, on a wake-up where the call was issued before the stack was up:
# 3000 turned public and 9000 silently did not, while gh returned success and the
# tunnel stayed alive. The cause was never identified, so this waits rather than
# claims to explain. Sixty tries at two seconds is two minutes, which covers a
# cold start including image pulls.
wait_for() {
  for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$1" && return 0
    sleep 2
  done
  return 1
}

wait_for http://127.0.0.1:3000/wizard-api/configs/bootstrap \
  || echo "publish-ports: the DSW API never answered, trying anyway."
wait_for http://127.0.0.1:9000/minio/health/live \
  || echo "publish-ports: MinIO never answered, trying anyway."

# Which of the two is not public yet, as GitHub sees it. This is read back rather
# than deduced from gh's exit status, because that status was 0 on the very call
# that had no effect.
still_private() {
  gh codespace ports -c "$CODESPACE_NAME" --json sourcePort,visibility \
    --jq '.[] | select(.sourcePort == 3000 or .sourcePort == 9000)
              | select(.visibility != "public") | .sourcePort' \
    | tr '\n' ' '
}

for attempt in 1 2; do
  gh codespace ports visibility 3000:public 9000:public -c "$CODESPACE_NAME" || true
  left="$(still_private)"
  if [ -z "${left// /}" ]; then
    echo "publish-ports: 3000 and 9000 are public."
    exit 0
  fi
  echo "publish-ports: still private after attempt $attempt: $left"
  sleep 5
done

# Non-zero on purpose. postStartCommand failing is visible in the Codespace, and
# a stack whose API cannot be reached cross-origin only looks broken later, in
# ways that do not name their cause.
echo "publish-ports: giving up. Set them in the PORTS panel, right click a port then Port Visibility." >&2
exit 1
