# Business Scanner

Flask tabanlı kurumsal şirket arama uygulaması. Resim veya anahtar kelime ile Europages üzerinden şirket araması yapabilirsiniz.

## Özellikler

- 🖼️ Resim ile ürün analizi (OpenAI GPT-4 Vision)
- 🔤 Anahtar kelime ile arama
- 🌍 Çoklu ülke desteği (Türkiye, Almanya, Hollanda, Fransa)
- 📊 Gerçek zamanlı sonuç gösterimi
- 📧 Şirket iletişim bilgileri (telefon, email)

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
OPENAI_API_KEY=your-api-key-here
SECRET_KEY=your-secret-key-here
```

## Geliştirme Ortamında Çalıştırma

```bash
python app.py
```

Uygulama http://localhost:5008 adresinde çalışacaktır.

## Production'da Çalıştırma

Gunicorn ile çalıştırma:
```bash
gunicorn -w 4 -b 0.0.0.0:5008 wsgi:app
```

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
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5008", "wsgi:app"]
```

## API Endpoints

- `GET /` - Ana sayfa
- `POST /api/analyze_image` - Resim analizi
- `POST /api/start_scan` - Tarama başlatma
- `GET /api/scan_status/<job_id>` - Tarama durumu kontrolü
- `POST /api/stop_scan/<job_id>` - Taramayı durdurma

## Güvenlik Notları

- API anahtarlarınızı asla commit etmeyin
- Production'da DEBUG modunu kapatın
- CORS ayarlarını production için kısıtlayın

## Lisans

MIT