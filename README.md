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

Gunicorn ile çalıştırma:
```bash
gunicorn -w 1 --threads 8 --timeout 120 -b 127.0.0.1:5008 wsgi:app
```

> **`-w 1` zorunludur, tercih değil.** Tarama işleri `active_scans` sözlüğünde
> süreç belleğinde tutulur. Birden fazla worker'da `start_scan` bir sürece,
> `scan_status` başka bir sürece düşer ve istemci "Tarama işi bulunamadı"
> hatası alır. Eş zamanlılık worker ile değil thread ile sağlanır.
> Ayarlar `gunicorn.conf.py` dosyasında da var; komut satırında `-w` verirseniz
> dosyadaki değeri ezer.

## Deployment

### Heroku
```bash
heroku create your-app-name
heroku config:set OPENAI_API_KEY=your-api-key
git push heroku main
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:5008", "wsgi:app"]
```

## API Endpoints

- `GET /` - Ana sayfa
- `POST /api/analyze_image` - Resim analizi
- `POST /api/start_scan` - Katalog araması başlatma
- `GET /api/scan_status/<job_id>` - Arama durumu / sonuçlar
- `POST /api/stop_scan/<job_id>` - Aramayı durdurma

## Güvenlik Notları

- API anahtarlarınızı asla commit etmeyin
- Production'da DEBUG modunu kapatın
- CORS ayarlarını production için kısıtlayın

## Lisans

MIT