#!/usr/bin/env bash
# ASF Presenton: build the asf-custom image from the fork, swap it in, roll back on failure.
# Run on the VM:
#   curl -fsSL -o /tmp/deploy_asf.sh https://raw.githubusercontent.com/tezbizz4u/presenton/asf-custom/deploy_asf.sh && sudo bash /tmp/deploy_asf.sh
set -uo pipefail

REPO_URL="${REPO_URL:-https://github.com/tezbizz4u/presenton}"
BRANCH="${BRANCH:-asf-custom}"
SRC=/opt/presenton-src
BACKUP_DIR=/opt/presenton-backups
STAMP=$(date +%Y%m%d-%H%M%S)
LOG=/var/log/presenton-deploy-$STAMP.log

if [ "${ASF_DETACHED:-0}" != "1" ]; then
  # Re-run ourselves detached so a dropped SSH session cannot kill a 20-minute build.
  SELF=$(mktemp /tmp/deploy_asf.XXXXXX.sh)
  if [ -f "$0" ]; then cp "$0" "$SELF"; else echo "Save the script to a file first, then run it with: sudo bash /tmp/deploy_asf.sh"; exit 1; fi
  ASF_DETACHED=1 nohup bash "$SELF" > "$LOG" 2>&1 &
  echo "Started in background. Follow progress with:  tail -f $LOG"
  exit 0
fi

say() { echo "[$(date +%H:%M:%S)] $*"; }
fail() { say "ERROR: $*"; exit 1; }

command -v docker >/dev/null || fail "docker not found"
command -v git >/dev/null || { apt-get update -y && apt-get install -y git; }

# 1) Find the running Presenton container.
OLD=$(docker ps --format '{{.Names}} {{.Image}}' | awk 'tolower($0) ~ /presenton/ && $1 != "presenton-new" {print $1; exit}')
[ -n "$OLD" ] || fail "no running presenton container found"
say "Current container: $OLD"
docker inspect "$OLD" > /tmp/presenton-old.json || fail "inspect failed"
OLD_IMAGE_ID=$(docker inspect -f '{{.Image}}' "$OLD")
docker tag "$OLD_IMAGE_ID" presenton-rollback:previous
say "Saved current image as presenton-rollback:previous"

# 1b) Free disk before building: keep only the newest stopped rollback container, drop dangling images and build cache.
say "Disk before cleanup: $(df -h / | awk 'NR==2{print $4" free"}')"
for c in $(docker ps -a --format '{{.Names}}' | grep '^presenton-old-' | sort | head -n -1); do docker rm -f "$c" >/dev/null 2>&1 && say "Removed old rollback container $c"; done
# Keep the build cache (fast deploys) unless the disk is getting full.
USED=$(df / | awk 'NR==2{gsub("%","",$5); print $5}')
if [ "${USED:-0}" -ge 70 ]; then
  say "Disk ${USED}% used: pruning images and build cache"
  docker image prune -af >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
else
  docker image prune -f >/dev/null 2>&1 || true   # dangling images only
fi
say "Disk after cleanup: $(df -h / | awk 'NR==2{print $4" free"}')"

# 2) Build the new image (container keeps running meanwhile).
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch --depth 1 origin "$BRANCH" && git -C "$SRC" checkout -q FETCH_HEAD || fail "git update failed"
else
  rm -rf "$SRC"; git clone --depth 1 -b "$BRANCH" "$REPO_URL" "$SRC" || fail "git clone failed"
fi
say "Building image (about 15-25 minutes)..."
docker build -t presenton-asf:"$STAMP" -t presenton-asf:latest "$SRC" || fail "docker build failed (old container untouched)"
say "Build finished"

# 3) Build the docker run arguments from the old container so ports, env and volumes match.
python3 - <<'PY' > /tmp/presenton-run-args.sh || fail "could not read old container config"
import json, shlex
c = json.load(open('/tmp/presenton-old.json'))[0]
args = []
hc = c['HostConfig']
for cport, binds in (hc.get('PortBindings') or {}).items():
    for b in binds or []:
        host = f"{b.get('HostIp')}:" if b.get('HostIp') else ""
        args += ['-p', f"{host}{b['HostPort']}:{cport}"]
for m in c.get('Mounts', []):
    args += ['-v', f"{m['Name'] if m.get('Type') == 'volume' else m['Source']}:{m['Destination']}" + ("" if m.get('RW', True) else ":ro")]
for e in c['Config'].get('Env') or []:
    args += ['-e', e]
rp = (hc.get('RestartPolicy') or {}).get('Name')
args += ['--restart', rp if rp and rp != 'no' else 'unless-stopped']
if hc.get('NetworkMode') not in (None, 'default', 'bridge'):
    args += ['--network', hc['NetworkMode']]
print('RUN_ARGS=(' + ' '.join(shlex.quote(a) for a in args) + ')')
mounts = [m['Source'] for m in c.get('Mounts', [])]
print('MOUNT_SOURCES=(' + ' '.join(shlex.quote(a) for a in mounts) + ')')
hp = [b['HostPort'] for binds in (hc.get('PortBindings') or {}).values() for b in (binds or [])]
print('HEALTH_PORT=' + shlex.quote(hp[0] if hp else ''))
PY
# shellcheck disable=SC1091
source /tmp/presenton-run-args.sh
[ -n "$HEALTH_PORT" ] || fail "old container has no published port"

# 4) Back up data (new version migrates the database; rollback must be able to restore it).
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/data-$STAMP.tar.gz"
if [ "${#MOUNT_SOURCES[@]}" -gt 0 ]; then
  say "Stopping $OLD for a consistent data backup..."
  docker stop "$OLD" >/dev/null
  tar -czf "$BACKUP" "${MOUNT_SOURCES[@]}" 2>/dev/null || fail "backup failed (restart old container with: docker start $OLD)"
  say "Backup saved: $BACKUP"
else
  docker stop "$OLD" >/dev/null
fi

rollback() {
  say "ROLLING BACK to the previous version"
  docker rm -f presenton-new >/dev/null 2>&1
  if [ -f "$BACKUP" ]; then tar -xzf "$BACKUP" -C / ; fi
  docker start "$OLD" >/dev/null && say "Old container is running again" || say "Could not start $OLD: docker start $OLD"
  exit 1
}

# 5) Start the new container and wait for it to answer.
docker rename "$OLD" "${OLD}-old-$STAMP"
OLD="${OLD}-old-$STAMP"
docker run -d --name presenton-new "${RUN_ARGS[@]}" presenton-asf:"$STAMP" >/dev/null || { docker rename "$OLD" "${OLD%-old-$STAMP}"; OLD="${OLD%-old-$STAMP}"; rollback; }
say "Waiting for Presenton to answer on port $HEALTH_PORT..."
OK=0
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$HEALTH_PORT/" || true)
  if [ "$code" = "200" ] || [ "$code" = "302" ] || [ "$code" = "307" ] || [ "$code" = "401" ]; then OK=1; break; fi
  sleep 5
done
if [ "$OK" != "1" ]; then docker logs --tail 40 presenton-new; rollback; fi

# Check the new API really is the patched one.
if ! docker exec presenton-new python3 -c "import sys; sys.path.insert(0,'/app/servers/fastapi'); import templates.theme_presets" >/dev/null 2>&1; then
  say "Note: could not confirm patched code inside the container (path may differ); site itself is up."
fi

docker rename presenton-new "${OLD%-old-$STAMP}"
say "DONE. New version is live. Old container kept as $OLD (stopped); backup $BACKUP"
say "To roll back later: docker rm -f ${OLD%-old-$STAMP}; docker rename $OLD ${OLD%-old-$STAMP}; docker start ${OLD%-old-$STAMP}; tar -xzf $BACKUP -C /"
