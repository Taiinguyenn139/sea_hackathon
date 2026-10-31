#!/usr/bin/env bash
set -Eeuo pipefail
# Runs as root on the single GCE instance. Never prints application secrets.
IMAGE=${1:?Image digest required}
REVISION=${2:?Commit SHA required}
[[ "$IMAGE" =~ ^[a-z0-9-]+-docker\.pkg\.dev/[a-z0-9-]+/[a-z0-9-]+/app@sha256:[a-f0-9]{64}$ ]] || exit 2
[[ "$REVISION" =~ ^[a-f0-9]{40}$ ]] || exit 2
exec 9>/var/lock/memory-harness-release.lock
flock -w 300 9
ROOT=/var/lib/memory-harness
test -f /etc/memory-harness/runtime.env
test -d "$ROOT/data"
mountpoint -q "$ROOT" || { echo 'Persistent disk is not mounted'; exit 1; }
mkdir -p "$ROOT/backups"
install -d -m 755 /var/lib/memory-harness/release
REGISTRY=${IMAGE%%/*}
# VM runtime service account only has repository-reader permissions.
curl -fsS --connect-timeout 5 --max-time 15 -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | \
  python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])' | \
  docker login -u oauth2accesstoken --password-stdin "https://$REGISTRY" >/dev/null
docker pull "$IMAGE"
PREVIOUS=$(docker inspect --format '{{.Config.Image}}' memory-harness-app 2>/dev/null || true)
PREVIOUS_REV=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' memory-harness-app 2>/dev/null || true)
BACKUP="$ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-$REVISION.tar.gz"
start_app() {
  docker run -d --name memory-harness-app --restart unless-stopped \
    --network bridge --env-file /etc/memory-harness/runtime.env \
    --env "DEPLOY_REVISION=$2" --label "org.opencontainers.image.revision=$2" \
    --env HARNESS_RELEASE_GATE=/release/ready \
    --env DEMO_PHONE_IDENTITY=true \
    --mount type=bind,source=/var/lib/memory-harness/release,target=/release,readonly \
    --mount "type=bind,source=$ROOT/data,target=/data" \
    --publish 127.0.0.1:8080:8080 --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --cap-drop ALL --security-opt no-new-privileges \
    --log-opt max-size=10m --log-opt max-file=3 "$1" >/dev/null
}
healthy() {
  for attempt in $(seq 1 30); do
    if curl -fsS --connect-timeout 2 --max-time 5 http://127.0.0.1:8080/healthz | python3 -c \
      'import json,sys; r=json.load(sys.stdin); assert r["status"] == "ok" and r["revision"] == sys.argv[1]' "$1" 2>/dev/null; then return 0; fi
    sleep 2
  done
  return 1
}
STOPPED=0
DIRTY=0
rollback() {
  trap - ERR INT TERM HUP
  set +e
  # Promotion is the commit point. Restoring a snapshot after traffic resumes
  # would lose newly accepted writes, even if a signal interrupts the last echo.
  if test "$DIRTY" = 1 && test -f "$ROOT/release/ready"; then
    echo 'Release already promoted; preserving current data and revision'
    exit 1
  fi
  echo 'Release failed; recovering previous revision'
  if test "$STOPPED" = 1; then
    if test "$DIRTY" = 1; then
      docker stop --time 30 memory-harness-app >/dev/null 2>&1
      docker rm memory-harness-app >/dev/null 2>&1
      mv "$ROOT/data" "$ROOT/failed-data-$(date -u +%s)"
      tar -C "$ROOT" -xzf "$BACKUP" || { echo 'Snapshot recovery failed'; exit 1; }
      if test -n "$PREVIOUS"; then
        start_app "$PREVIOUS" "$PREVIOUS_REV" || exit 1
        healthy "$PREVIOUS_REV" || exit 1
        docker network connect memory-harness memory-harness-app || exit 1
      fi
    elif test -n "$PREVIOUS"; then
      docker start memory-harness-app >/dev/null || exit 1
      healthy "$PREVIOUS_REV" || exit 1
    fi
    if test -n "$PREVIOUS"; then touch "$ROOT/release/ready"; fi
  fi
  exit 1
}
trap rollback ERR INT TERM HUP
rm -f "$ROOT/release/ready"
STOPPED=1
docker stop --time 30 memory-harness-app >/dev/null 2>&1 || test -z "$PREVIOUS"
# No writers during backup; keep all WAL and checkpoint files together.
tar -C "$ROOT" -czf "$BACKUP" data
chmod 600 "$BACKUP"
DIRTY=1
docker rm memory-harness-app >/dev/null 2>&1 || test -z "$PREVIOUS"
start_app "$IMAGE" "$REVISION"
healthy "$REVISION"
docker network connect memory-harness memory-harness-app
# Proxy exposes only health until ready; no traffic/workers before this check.
DOMAIN=$(cat /etc/memory-harness/domain)
curl --fail --silent --show-error --connect-timeout 5 --max-time 15 --retry 12 --retry-delay 5 "https://$DOMAIN/healthz" | \
  python3 -c 'import json,sys; r=json.load(sys.stdin); assert r["status"]=="ok" and r["revision"]==sys.argv[1]' "$REVISION"
printf '%s\n' "$IMAGE" > "$ROOT/current-image"
printf '%s\n' "$PREVIOUS" > "$ROOT/previous-image"
printf '%s\n' "$BACKUP" > "$ROOT/last-backup"
touch "$ROOT/release/ready"
trap - ERR INT TERM HUP
echo "Healthy HTTPS revision $REVISION"
