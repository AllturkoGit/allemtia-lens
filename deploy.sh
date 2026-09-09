#!/usr/bin/env bash
# GitHub'dan son surumu cek, PM2 ile yeniden yukle ve dogrula.
#   ./deploy.sh
#
# Uygulama PM2 ile yonetiliyor (bkz. ecosystem.config.js). Eskiden burada
# "sudo systemctl restart allemtia-lens" vardi; o servis bu sunucuda hic
# kurulu olmadigi icin script sessizce ise yaramiyordu.
set -euo pipefail

cd "$(dirname "$0")"

UYGULAMA="allemtia-lens"
TABAN="http://127.0.0.1:5008"

echo "==> git pull"
git pull --ff-only

echo "==> PM2 yeniden yukleniyor"
if pm2 describe "$UYGULAMA" >/dev/null 2>&1; then
    # reload: eski surec yenisi hazir olana kadar ayakta kalir.
    pm2 reload "$UYGULAMA" --update-env
else
    echo "    ($UYGULAMA PM2'de yok, ilk kez baslatiliyor)"
    mkdir -p logs
    pm2 start ecosystem.config.js
    pm2 save
fi

echo "==> saglik kontrolu"
kod=""
for _ in $(seq 1 20); do
    kod=$(curl -s -o /dev/null -w '%{http_code}' "$TABAN/api/lens_status" || true)
    [ "$kod" = "200" ] && break
    sleep 1
done

if [ "$kod" != "200" ]; then
    echo "HATA - backend yanit vermiyor (son kod: ${kod:-yok})" >&2
    echo "Log:  pm2 logs $UYGULAMA --lines 40" >&2
    exit 1
fi

echo "    lens durumu: $(curl -s "$TABAN/api/lens_status")"

# Tek surec kontrolu.
#
# Tarama isleri active_scans sozlugunde SUREC BELLEGINDE tutuluyor. Birden
# fazla surec olursa start_scan bir surece, scan_status baska surece duser ve
# kullanici "Tarama isi bulunamadi" hatasi alir. Bu hata gecmiste gunlerce
# fark edilmedi cunku kodda hicbir izi yok - sadece calisan surec sayisindan
# kaynaklaniyor. O yuzden her deploy'da otomatik dogrulaniyor.
echo "==> tek surec dogrulamasi"
is_id=$(curl -s -X POST "$TABAN/api/start_scan" \
        -H 'Content-Type: application/json' \
        -d '{"keyword":"boru"}' \
    | sed -n 's/.*"job_id":"\([^"]*\)".*/\1/p')

if [ -z "$is_id" ]; then
    echo "HATA - tarama baslatilamadi, is kimligi alinamadi" >&2
    exit 1
fi

bulunamadi=0
for _ in $(seq 1 10); do
    yanit=$(curl -s "$TABAN/api/scan_status/$is_id" || true)
    case "$yanit" in
        *bulunamad*) bulunamadi=$((bulunamadi + 1)) ;;
    esac
done

if [ "$bulunamadi" -gt 0 ]; then
    echo "HATA - $bulunamadi/10 sorguda is bulunamadi: BIRDEN FAZLA SUREC var." >&2
    echo "  ecosystem.config.js icinde instances 1 ve exec_mode 'fork' olmali." >&2
    echo "  PM2'nin cluster modu Python'da calismaz: her surec kendi" >&2
    echo "  active_scans kopyasini tutar." >&2
    echo "  Kontrol:  pm2 describe $UYGULAMA | grep -E 'instances|exec mode'" >&2
    exit 1
fi

echo "OK - backend ayakta ve tek surec (10/10)"
