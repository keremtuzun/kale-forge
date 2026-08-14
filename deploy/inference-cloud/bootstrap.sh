#!/usr/bin/env bash
# One-time VM setup: Docker, firewall, bring the stack up. Run ON the VM from ~/kale:
#   bash bootstrap.sh
# Idempotent — safe to re-run after a git pull or a model swap.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "no .env here — copy .env.example to .env and fill KALE_HOST + KALE_API_TOKEN first" >&2
  exit 1
fi
if [ ! -f models/kale-design-v18-q4_k_m.gguf ]; then
  echo "model missing: models/kale-design-v18-q4_k_m.gguf (push-from-laptop.ps1 uploads it)" >&2
  exit 1
fi

# Docker + compose plugin (official convenience script; no-op if already installed).
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

# Oracle's Ubuntu images ship host iptables that REJECT everything but SSH, ahead of any
# rule the cloud security list allows. Opening 80/443 in the console is not enough — the
# packets die here on the box. Insert ACCEPTs before the REJECT line and persist them.
open_port() {
  local port="$1" proto="$2"
  if ! sudo iptables -C INPUT -p "$proto" --dport "$port" -j ACCEPT 2>/dev/null; then
    local reject_line
    reject_line=$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/ {print $1; exit}')
    if [ -n "$reject_line" ]; then
      sudo iptables -I INPUT "$reject_line" -p "$proto" --dport "$port" -j ACCEPT
    else
      sudo iptables -A INPUT -p "$proto" --dport "$port" -j ACCEPT
    fi
  fi
}
open_port 80 tcp
open_port 443 tcp
open_port 443 udp
sudo netfilter-persistent save 2>/dev/null || sudo sh -c 'iptables-save > /etc/iptables/rules.v4' || true

sudo docker compose up -d --build

echo
echo "waiting for the API (llama-server needs ~1-2 min to map the model)..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8001/health >/dev/null 2>&1 \
     || sudo docker compose exec -T api curl -sf http://localhost:8001/health >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

KALE_HOST=$(grep '^KALE_HOST=' .env | cut -d= -f2)
echo
echo "== status =="
sudo docker compose ps
echo
echo "public health check (TLS certificate is issued on the first request, give it ~30 s):"
echo "  curl https://${KALE_HOST}/health"
echo
echo "then, back on the laptop:  deploy/inference-cloud/flip-vercel.ps1 -VmHost ${KALE_HOST}"
