#!/usr/bin/env bash
# GitHub'dan son surumu cek ve servisi yeniden baslat.
#   ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "==> git pull"
git pull --ff-only

echo "==> servis yeniden baslatiliyor"
sudo systemctl restart allemtia-lens

echo "==> saglik kontrolu"
for i in $(seq 1 10); do
    code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5008/api/lens_status || true)
    if [ "$code" = "200" ]; then
        echo "OK - backend ayakta (HTTP $code)"
        curl -s http://127.0.0.1:5008/api/lens_status
        echo
        exit 0
    fi
    sleep 1
done

echo "HATA - backend yanit vermiyor (son kod: ${code:-yok})" >&2
echo "Log:  journalctl -u allemtia-lens -n 30 --no-pager" >&2
exit 1
