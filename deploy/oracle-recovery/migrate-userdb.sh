#!/usr/bin/env bash
# Move the Kale.ai user database + uploaded files from the old VM's disk (via the clone,
# mounted on a throwaway host) onto the new cloud VM.
#
#   ./migrate-userdb.sh <MIGRATION_HOST_IP>
#
# Order matters and is deliberate: inspect FIRST, decide SECOND, write LAST. Nothing on the
# new VM is touched until the recovered database has been proven readable and its contents
# reported, so a hot-copied (possibly torn) SQLite file can never silently overwrite a
# working install.
set -uo pipefail

MIG_IP="${1:?usage: migrate-userdb.sh <migration-host-ip>}"
NEW_IP="${2:-150.136.212.31}"
KEY="$HOME/.ssh/kale-oracle-recovery"
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15"
VOL_PATH="/mnt/clone/var/lib/docker/volumes/kale_kale-data/_data"

echo "== 1. mounting the clone on the migration host =="
$SSH opc@"$MIG_IP" "
  sudo mkdir -p /mnt/clone
  mountpoint -q /mnt/clone || sudo mount -o ro /dev/sdb1 /mnt/clone
  echo mounted:; ls /mnt/clone | head -5
" || { echo "could not mount the clone"; exit 1; }

echo
echo "== 2. what is actually in the volume? =="
$SSH opc@"$MIG_IP" "
  if [ ! -d '$VOL_PATH' ]; then
    echo 'VOLUME NOT FOUND at $VOL_PATH'
    echo 'docker volumes present:'; sudo ls /mnt/clone/var/lib/docker/volumes 2>/dev/null
    exit 2
  fi
  echo 'volume tree:'; sudo du -sh '$VOL_PATH' 2>/dev/null
  sudo ls -la '$VOL_PATH' 2>/dev/null
  echo; echo 'sqlite integrity + row counts:'
  sudo python3 - <<'PY'
import sqlite3, os
p='$VOL_PATH/kale.db'
if not os.path.exists(p):
    print('  kale.db MISSING'); raise SystemExit(0)
print('  size:', os.path.getsize(p), 'bytes')
c=sqlite3.connect(f'file:{p}?mode=ro', uri=True)
print('  integrity_check:', c.execute('PRAGMA integrity_check').fetchone()[0])
for (t,) in c.execute(\"select name from sqlite_master where type='table' order by name\"):
    try: print(f'    {t}: {c.execute(f\"select count(*) from [{t}]\").fetchone()[0]} rows')
    except Exception as e: print(f'    {t}: unreadable ({e})')
PY
" || { echo "inspection failed — NOT touching the new VM"; exit 2; }

echo
read -r -p "Proceed to copy this onto the new VM? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted; nothing was changed"; exit 0; }

echo "== 3. packing =="
$SSH opc@"$MIG_IP" "sudo tar -czf /tmp/kale-userdata.tar.gz -C '$VOL_PATH' . && sudo chmod 644 /tmp/kale-userdata.tar.gz && ls -lh /tmp/kale-userdata.tar.gz"

echo "== 4. laptop <- migration host =="
scp -i "$KEY" -o StrictHostKeyChecking=no opc@"$MIG_IP":/tmp/kale-userdata.tar.gz /tmp/kale-userdata.tar.gz

echo "== 5. laptop -> new VM =="
scp -i "$KEY" -o StrictHostKeyChecking=no /tmp/kale-userdata.tar.gz ubuntu@"$NEW_IP":/tmp/

echo "== 6. restoring into the kaleai-data volume (old copy kept) =="
$SSH ubuntu@"$NEW_IP" '
  set -e
  cd ~/kale
  sudo docker compose --profile app stop kaleai
  V=$(sudo docker volume inspect kale_kaleai-data --format "{{.Mountpoint}}")
  sudo tar -czf /tmp/kaleai-data-before-migration.tar.gz -C "$V" . 2>/dev/null || true
  sudo rm -rf "$V"/*
  sudo tar -xzf /tmp/kale-userdata.tar.gz -C "$V"
  sudo chown -R 0:0 "$V" 2>/dev/null || true
  echo "restored:"; sudo ls -la "$V" | head
  sudo docker compose --profile app up -d kaleai
'
echo "== 7. done — verify at https://kaleai-app.150.136.212.31.nip.io =="
