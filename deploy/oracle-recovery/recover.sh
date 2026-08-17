#!/usr/bin/env bash
# Kale.ai Oracle VM recovery — paste this into the OCI Console "Run Command" box.
#
# Runs as root via the Oracle Cloud Agent (no SSH needed). It is NON-DESTRUCTIVE: it does
# not touch the running app, reboot, or change the network. It does two things:
#   1. restores SSH access by appending our recovery public key to the login users
#   2. snapshots the running "Kale.ai — Hardware Engineering AI" app source into a tarball
#      in the login user's home, so the source that lives ONLY on this box is retrievable
#
# Idempotent — safe to run more than once.
set -uo pipefail

# --- our freshly generated recovery public key (private half is on the laptop) -------------
RECOVERY_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEYfzAZJ71K7o7bzbFMmOvR6na+ZpmFrZY1QH4YT6BnE kale-oracle-recovery'

echo "== Kale Oracle recovery =="
echo "host: $(hostname)   date: $(date -u)"

# 1) Restore SSH for whichever login user this image uses (Ubuntu → ubuntu, Oracle Linux → opc)
added_for=""
for u in ubuntu opc; do
  home=$(getent passwd "$u" | cut -d: -f6)
  [ -n "$home" ] && [ -d "$home" ] || continue
  install -d -m 700 -o "$u" -g "$u" "$home/.ssh"
  touch "$home/.ssh/authorized_keys"
  if ! grep -qF "$RECOVERY_KEY" "$home/.ssh/authorized_keys"; then
    echo "$RECOVERY_KEY" >> "$home/.ssh/authorized_keys"
  fi
  chown "$u:$u" "$home/.ssh/authorized_keys"
  chmod 600 "$home/.ssh/authorized_keys"
  added_for="$added_for $u"
done
echo "SSH key installed for:$added_for"

# Pick the primary login user for the tarball drop.
DROP_USER=ubuntu; getent passwd ubuntu >/dev/null || DROP_USER=opc
DROP_HOME=$(getent passwd "$DROP_USER" | cut -d: -f6)
OUT="$DROP_HOME/kale-app-source.tar.gz"

# 2) Snapshot the app source. Prefer the mounted source on the host; fall back to copying it
#    straight out of the running Next.js container (the image is all we may have).
echo "== locating the app source =="
SRC=""
for cand in "$DROP_HOME/kale" /opt/kale /srv/kale /root/kale; do
  if [ -d "$cand" ] && ls "$cand" 2>/dev/null | grep -qiE 'docker-compose|apps|package.json'; then
    SRC="$cand"; break
  fi
done

if [ -n "$SRC" ]; then
  echo "host source: $SRC"
  tar -czf "$OUT" -C "$(dirname "$SRC")" "$(basename "$SRC")" \
      --exclude='node_modules' --exclude='.next' --exclude='.git' --exclude='*.gguf' 2>/dev/null
else
  echo "no host source tree — extracting from the running container instead"
  CID=""
  if command -v docker >/dev/null 2>&1; then
    # the container serving Next.js (has /_next); match by published port or image name
    CID=$(docker ps --format '{{.ID}} {{.Image}} {{.Ports}}' \
          | grep -iE 'kale|web|next|7860|3000|:80->' | awk '{print $1}' | head -1)
    [ -z "$CID" ] && CID=$(docker ps -q | head -1)
  fi
  if [ -n "$CID" ]; then
    echo "container: $CID"
    APPDIR=$(docker exec "$CID" sh -lc 'for d in /app /usr/src/app /srv/app /workspace; do [ -f "$d/package.json" ] && echo "$d" && break; done' 2>/dev/null)
    [ -z "$APPDIR" ] && APPDIR=/app
    echo "container app dir: $APPDIR"
    # tar from inside, excluding the heavy dirs, and stream it straight to the host file
    docker exec "$CID" sh -lc "cd $APPDIR && tar -czf - --exclude=node_modules --exclude=.next --exclude=.git . 2>/dev/null" > "$OUT"
    # also capture how it runs, which the source tree alone won't tell you
    docker inspect "$CID" > "$DROP_HOME/kale-app-container-inspect.json" 2>/dev/null || true
    docker ps -a > "$DROP_HOME/kale-docker-ps.txt" 2>/dev/null || true
    (docker compose -f "$DROP_HOME/kale/docker-compose.oracle.yml" config 2>/dev/null || true) > "$DROP_HOME/kale-compose-resolved.yml"
  else
    echo "WARNING: no docker container found — is the app running under systemd/pm2 instead?"
    command -v pm2 >/dev/null 2>&1 && pm2 list > "$DROP_HOME/kale-pm2.txt" 2>&1 || true
    systemctl list-units --type=service --state=running | grep -iE 'kale|node|next' > "$DROP_HOME/kale-services.txt" 2>&1 || true
  fi
fi

chown "$DROP_USER:$DROP_USER" "$OUT" 2>/dev/null || true
echo "== done =="
[ -f "$OUT" ] && echo "source snapshot: $OUT ($(du -h "$OUT" | cut -f1))"
echo "now, from the laptop:  deploy/oracle-recovery/pull-source.ps1 -VmIp 150.136.151.230"
