# Lens - Görsel ile Ürün Arama

Flask tabanlı görsel arama uygulaması. Kullanıcı ürünün fotoğrafını yükler,
yapay zekâ ürün adını çıkarır ve **allemtia kataloğunda** o ürün aranır.

## Özellikler

- 🖼️ Resim ile ürün analizi (Gemini / OpenAI / Google Vision)
- 🔤 Anahtar kelime ile arama
- 🛒 allemtia kataloğunda ürün arama (ad + fiyat + görsel + ürün linki)
- 📊 Gerçek zamanlı sonuç gösterimi

## Veri kaynağı

Sonuçlar `ALLEMTIA_API_URL` üzerinden `GET /home/products?search=...`
ile alınır. Bu uç **backend'de arama desteği gerektirir**; desteklemeyen bir
sürüm bilinmeyen parametreyi yok sayıp tüm katalogu döndüreceği için uygulama
yanıttaki `search` alanını doğrular ve eşleşmezse alakasız sonuç göstermek
yerine hata verir.

Görsel analizinden gelen ad katalog adlandırmasıyla birebir tutmayabilir
(model "metal boru" der, katalogda "Alüminyum Boru 40mm" vardır). Tam terim
sonuç vermezse son kelimeyle ("boru") tekrar aranır ve arayüz bunu bildirir.

> Not: Önceki sürüm Europages'i kazıyıp firma iletişim bilgisi topluyordu.
> Europages aramayı Nuxt SPA'ya taşıdığı (ve AWS WAF captcha eklediği) için o
> kod her ortamda 0 sonuç veriyordu; ayrıca asıl ihtiyaç kendi kataloğumuzda
> arama olduğu için tamamen kaldırıldı.

## Kurulum

1. Repoyu klonlayın:
```bash
git clone <repo-url>
cd business-scanner
```

2. Virtual environment oluşturun:
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. Bağımlılıkları yükleyin:
```bash
pip install -r requirements.txt
```

4. `.env` dosyası oluşturun:
```bash
cp .env.example .env
```

5. `.env` dosyasını düzenleyin ve OpenAI API anahtarınızı ekleyin:
```
SECRET_KEY=<rastgele-uzun-deger>   # python -c "import os;print(os.urandom(32).hex())"

# Gorsel analizi icin en az biri gerekli
GEMINI_API_KEY=...                 # https://aistudio.google.com/apikey (ucretsiz kotasi var)
OPENAI_API_KEY=...                 # https://platform.openai.com (kredi gerektirir)
GOOGLE_APPLICATION_CREDENTIALS=google-vision-credentials.json

# Opsiyonel
GEMINI_MODEL=gemini-3.6-flash      # varsayilan; hesabinizda yoksa degistirin
OPENAI_MODEL=gpt-4o
ALLEMTIA_API_URL=https://sadmin.allemtia.com.tr/api   # katalog API tabani
ALLEMTIA_SITE_URL=https://allemtia.com.tr             # urun linklerinin tabani
ALLEMTIA_TENANT_ID=                                   # bos = tum tenantlar
CORS_ORIGINS=                      # bos = kapali (arayuz ayni origin'den servis ediliyor)
FLASK_ENV=production               # SECRET_KEY yoksa acilista hata verir
```

### Lens secenekleri

Uc gorsel analiz saglayicisi var; ana sayfadan secilir:

| Lens | Anahtar | Not |
|------|---------|-----|
| Gemini | `GEMINI_API_KEY` | Ucretsiz kotasi var, kredi gerektirmez |
| Ozel Lens | `OPENAI_API_KEY` | OpenAI hesabinda kredi gerektirir |
| Google Lens | `GOOGLE_APPLICATION_CREDENTIALS` | Google Cloud Vision servis hesabi |

Secilen lens basarisiz olursa yapilandirilmis diger saglayicilara otomatik
dusulur. Hicbiri yapilandirilmamissa istek acik bir hata mesajiyla doner.

Hesabinizda hangi Gemini modellerinin oldugunu gormek icin:

```bash
curl -s https://generativelanguage.googleapis.com/v1beta/models \
  -H "x-goog-api-key: $GEMINI_API_KEY" | grep -o '"name": "[^"]*"'
```

## Geliştirme Ortamında Çalıştırma

```bash
python app.py
```

Uygulama http://localhost:5008 adresinde çalışacaktır.

## Production'da Çalıştırma

Uygulama **PM2** ile yönetiliyor (`ecosystem.config.js`).

```bash
mkdir -p logs
pm2 start ecosystem.config.js
pm2 save                       # yeniden baslatmada ayaga kalksin
```

Deploy:
```bash
./deploy.sh                    # git pull + pm2 reload + dogrulama
```

`deploy.sh` her çalıştığında **tek süreç kontrolü** yapar: bir tarama başlatıp
durumunu 10 kez sorgular, biri bile "bulunamadı" derse hata verip çıkar.

> **Tek süreç zorunludur, tercih değil.** Tarama işleri `active_scans`
> sözlüğünde süreç belleğinde tutulur. Birden fazla süreçte `start_scan` bir
> sürece, `scan_status` başka bir sürece düşer ve istemci "Tarama işi
> bulunamadı" hatası alır. Eş zamanlılık süreçle değil, gunicorn thread'leriyle
> sağlanır.
>
> Bu yüzden `ecosystem.config.js` içinde `instances: 1` ve
> `exec_mode: "fork"` şarttır. **PM2'nin cluster modu yalnızca Node.js
> içindir**; Python'da `instances > 1` birbirinden habersiz N ayrı süreç
> demektir, yani aynı hatanın tekrarı.

Doğrudan gunicorn ile (PM2 olmadan):
```bash
gunicorn -w 1 --threads 8 --timeout 120 -b 127.0.0.1:5008 app:app
```

### Geçmiş: bu hatayı iki kez yaşadık

Uygulama önce aaPanel'in Python project manager'ıyla çalışıyordu. Panelin
"Number of processes" alanı komut satırına `-w 4` ekleyip `gunicorn_conf.py`
içindeki `workers = 1` ayarını **eziyordu**. Sonuç: aramalar sunucuda
çalışıyor ama sonuçlar tarayıcıya ulaşmıyordu ve kodda hiçbir izi yoktu.
`deploy.sh`'daki otomatik kontrol bunun tekrarını yakalamak için var.

## API Endpoints

- `GET /` - Ana sayfa
- `POST /api/analyze_image` - Resim analizi
- `POST /api/start_scan` - Katalog araması başlatma
- `GET /api/scan_status/<job_id>` - Arama durumu / sonuçlar
- `POST /api/stop_scan/<job_id>` - Aramayı durdurma

## İzleme (Nabız Hub)

Sunucu hataları, 5xx yanıtlar ve yavaş istekler
[nabiz-python](../../proje-izleme-sistemi/nabiz-python) paketiyle nabız hub'a raporlanır.
Kurulum `app.py` içinde tek satır (`NabizFlask(app)`); paket kurulu değilse ya da
`NABIZ_*` değişkenleri tanımlı değilse uygulama etkilenmez, hiçbir veri gönderilmez.

Etkinleştirmek için:

1. Nabız panelinde `search-allemtia-com-tr` projesini açın; `NABIZ_KEY` ve
   `NABIZ_SECRET` oradan gelir. Anahtar PM2'deki uygulama adıyla (`allemtia-lens`)
   aynı olmak zorunda değil.
2. Paketi kurun: `pip install -r requirements.txt` (paket `requirements.txt` içinde
   git URL'i ile tanımlı).
3. `.env` dosyasına ekleyin:

```env
NABIZ_ENABLED=true
NABIZ_URL=https://monitor.allturko.erkpa.com.tr
NABIZ_KEY=search-allemtia-com-tr
NABIZ_SECRET=<panelden alinan secret>
NABIZ_ENV=production
```

4. Servisi yeniden başlatın: `pm2 restart allemtia-lens`. Gunicorn ortam
   değişkenlerini süreç başlarken okur; yeniden başlatılmadan `.env` değişikliği
   görünmez.
5. Doğrulayın: `nabiz-durum --test` — ardından panelde proje satırının `Bağlı`
   göründüğünü kontrol edin. Hub geçersiz imzaya da `204` döndüğü için komut tek
   başına yeterli değildir.

Toplanmayanlar: IP, User-Agent, istek gövdesi, query string değerleri, oturum verisi.
Hata mesajlarındaki e-posta/telefon/TCKN/IBAN gönderilmeden önce maskelenir.

## Güvenlik Notları

- API anahtarlarınızı asla commit etmeyin
- Production'da DEBUG modunu kapatın
- CORS ayarlarını production için kısıtlayın

## Lisans

MIT