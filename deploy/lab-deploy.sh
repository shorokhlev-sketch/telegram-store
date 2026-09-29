#!/usr/bin/env bash
# Staging ("lab") deploy to a shared VPS under a sub-path: systemd + nginx, no docker.
# Production uses docker compose + caddy (deploy/PROD.md); lab is for live testing in Telegram.
#
# Required env:
#   DEPLOY_HOST   ssh host or alias of the staging server
#   LAB_URL       public URL of the app on that server, no trailing slash,
#                 e.g. https://staging.example.com/shop
# Optional env:
#   PUSH_ENV=1    also upload backend/.env (paths and PUBLIC_BASE_URL rewritten for lab)
#
# Paths and port below must match deploy/apple-store-tg.service.
set -uo pipefail
: "${DEPLOY_HOST:?set DEPLOY_HOST to the ssh host of the staging server}"
: "${LAB_URL:?set LAB_URL, e.g. https://staging.example.com/shop}"
HOST="$DEPLOY_HOST"
BASE_PATH="/$(printf '%s' "$LAB_URL" | sed -E 's#^https?://[^/]+/?##; s#/+$##')/"
[ "$BASE_PATH" = "//" ] && BASE_PATH="/"
REMOTE=/opt/apple-store-tg
DATA=/var/lib/apple-store-tg
PORT=8090
cd "$(dirname "$0")/.."
retry() { local n=0; until "$@"; do n=$((n+1)); [ $n -ge 6 ] && { echo "FAILED after $n: $*"; return 1; }; echo "retry $n..."; sleep $((n*3)); done; }

echo "-> build web (VITE_BASE=$BASE_PATH)"
( cd web && npm ci --no-audit --no-fund && VITE_BASE="$BASE_PATH" npm run build ) || { echo "FAILED: web build"; exit 1; }
[ -f web/dist/index.html ] || { echo "FAILED: web/dist/index.html missing"; exit 1; }

echo "-> rsync backend"
retry ssh -o ConnectTimeout=20 "$HOST" "mkdir -p $REMOTE/web/dist $DATA/media"
retry rsync -az --delete -e "ssh -o ConnectTimeout=20" \
  --exclude .venv --exclude data --exclude .env --exclude __pycache__ --exclude .pytest_cache \
  ./backend/ "$HOST:$REMOTE/backend/"
retry rsync -az -e "ssh -o ConnectTimeout=20" ./deploy/apple-store-tg.service "$HOST:$REMOTE/"

echo "-> rsync web/dist"
# dist only, into $REMOTE/web/dist - never anything under $DATA.
retry rsync -az --delete -e "ssh -o ConnectTimeout=20" ./web/dist/ "$HOST:$REMOTE/web/dist/"

if [ -f backend/.env ] && [ "${PUSH_ENV:-0}" = "1" ]; then
  echo "-> push .env (PUSH_ENV=1)"
  sed -e "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=$LAB_URL#" \
      -e "s#^DB_PATH=.*#DB_PATH=$DATA/app.db#" \
      -e "s#^MEDIA_DIR=.*#MEDIA_DIR=$DATA/media#" \
      -e "s#^SET_WEBHOOK_ON_START=.*#SET_WEBHOOK_ON_START=1#" backend/.env \
    | retry ssh -o ConnectTimeout=20 "$HOST" "cat > $REMOTE/backend/.env && chmod 600 $REMOTE/backend/.env"
fi

echo "-> remote install + migrate + restart"
retry ssh -o ConnectTimeout=20 "$HOST" bash -s <<REMOTE
set -e
cd $REMOTE/backend
uv sync --frozen --no-dev -q 2>&1 | tail -2 || uv sync --no-dev -q 2>&1 | tail -2
uv run --no-dev alembic upgrade head 2>&1 | tail -1
install -m 644 $REMOTE/apple-store-tg.service /etc/systemd/system/apple-store-tg.service
systemctl daemon-reload
systemctl enable apple-store-tg >/dev/null 2>&1 || true
systemctl restart apple-store-tg
sleep 3
systemctl is-active apple-store-tg
# nginx: the location block goes into a snippet; include it once in the server
# block of the staging site:  include /etc/nginx/snippets/apple-store-tg.conf;
mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/apple-store-tg.conf <<'NGINX'
location ^~ $BASE_PATH {
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    client_max_body_size 6m;
}
NGINX
nginx -t && systemctl reload nginx
journalctl -u apple-store-tg -n 5 --no-pager | tail -5
REMOTE
curl -s -o /dev/null -w "health via nginx: %{http_code}\n" "$LAB_URL/api/health"
