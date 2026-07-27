#!/usr/bin/env bash
# Deploy the Kale Forge app + backend to the Oracle VM.
#
# Usage:  ./scripts/deploy-oracle.sh [user@host]
# Default host matches the vercel.json rewrites (kale.150.136.151.230.nip.io).
#
# What it does:
#   1. rsyncs the source tree to ~/kale on the VM (excludes heavyweight/dev dirs)
#   2. rebuilds the all-in-one image and restarts it behind Caddy
#
# Requires: your SSH key for the VM (not stored on this machine), rsync, and
# docker compose already set up on the VM with .env.oracle present in ~/kale.
set -euo pipefail

TARGET="${1:-ubuntu@150.136.151.230}"
REMOTE_DIR="~/kale"

echo "==> Syncing source to ${TARGET}:${REMOTE_DIR}"
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '.next' \
  --exclude 'work' --exclude 'datasets/raw' --exclude 'models/checkpoints' \
  --exclude '__pycache__' --exclude '.DS_Store' --exclude 'tsconfig.tsbuildinfo' \
  ./ "${TARGET}:${REMOTE_DIR}/"

echo "==> Rebuilding and restarting the stack"
ssh "${TARGET}" "cd ${REMOTE_DIR} && docker compose -f docker-compose.oracle.yml up --build -d && docker compose -f docker-compose.oracle.yml ps"

echo "==> Health check"
ssh "${TARGET}" "curl -s -o /dev/null -w 'web HTTP %{http_code}\n' http://localhost:80 || true"
echo "Done. Verify at https://kale.150.136.151.230.nip.io"
