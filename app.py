# ---------------------------------------------------------------------------
#  app.py   –   Gorsel ile allemtia katalog aramasi (Flask)
# ---------------------------------------------------------------------------
import os, re, time, uuid, threading, traceback
import requests, base64
import tempfile
from datetime import datetime
from dotenv import load_dotenv

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Google Vision API imports
try:
    from google.cloud import vision

    GOOGLE_VISION_AVAILABLE = True
except ImportError:
    GOOGLE_VISION_AVAILABLE = False
    print(
        "Google Vision API not available. Install with: pip install google-cloud-vision"
    )

# Load environment variables
load_dotenv()

# ---------- Genel yapılandırma ---------------------------------------------
app = Flask(__name__)

# SECRET_KEY zorunlu: sabit varsayilan ("your-secret-key-here") ile production'a
# cikmak imza dogrulamasini anlamsiz kilardi.
_secret = os.getenv("SECRET_KEY")
if not _secret:
    if os.getenv("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY tanimli degil (.env dosyasina ekleyin)")
    _secret = os.urandom(32).hex()
    print("UYARI: SECRET_KEY yok, gecici anahtar uretildi (sadece gelistirme).")
app.config["SECRET_KEY"] = _secret

# Yuklenecek gorsel icin ust sinir; istemcideki 10MB kontrolu asilabilir.
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# imghdr Python 3.13'te kaldirildi; sihirli baytlari kendimiz kontrol ediyoruz.
_IMAGE_MAGIC = (
    b"\xff\xd8\xff",       # jpeg
    b"\x89PNG\r\n\x1a\n",  # png
    b"GIF87a",
    b"GIF89a",
    b"BM",                  # bmp
)


def is_supported_image(path):
    """Uzantiya degil, dosya icerigine bakarak gorsel olup olmadigini dogrular."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head.startswith(_IMAGE_MAGIC):
        return True
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"  # webp

MAX_IMAGE_BYTES = 10 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES

# CORS: arayuz backend ile ayni origin'den servis ediliyor, bu yuzden
# varsayilan olarak kapali. Farkli origin gerekirse CORS_ORIGINS ile verilir.
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    CORS(app, resources={r"/api/*": {"origins": _origins}})

active_scans: dict[str, dict] = {}  # bellekte iş takibi
_scans_lock = threading.Lock()

# Biten isler surec belleginde birikmesin diye TTL ile temizlenir.
SCAN_TTL_SECONDS = 60 * 60  # biten is 1 saat sonra dusulur
MAX_ACTIVE_SCANS = 200  # ust sinir; asilirsa en eskiler dusulur


def purge_old_scans():
    """Suresi dolmus/fazla birikmis tarama kayitlarini bellekten dusur."""
    now = time.time()
    with _scans_lock:
        for jid, d in list(active_scans.items()):
            fin = d.get("finished_at")
            if fin and now - fin > SCAN_TTL_SECONDS:
                active_scans.pop(jid, None)

        if len(active_scans) > MAX_ACTIVE_SCANS:
            finished = sorted(
                ((jid, d) for jid, d in active_scans.items() if d.get("finished_at")),
                key=lambda kv: kv[1]["finished_at"],
            )
            for jid, _ in finished[: len(active_scans) - MAX_ACTIVE_SCANS]:
                active_scans.pop(jid, None)

# ---------- Ortak urun tanima istemi -----------------------------------------

# Katalog Turkce oldugu icin urun adi da Turkce isteniyor. Ingilizce donseydi
# ("glass cup") Turkce katalogda ("Cam Bardak") hicbir sey eslesmezdi.
# Modelin verdigi tek ad katalogda cogu zaman tutmuyor: model "Chiller" der,
# katalogda "Endustriyel Evaporatif Sogutucu" yazar - ortak kelime yok. Bu yuzden
# tek ad yerine, en spesifikten en genele dogru birkac Turkce esanlam isteniyor;
# arama bunlari sirayla deneyip ilk tutani kullaniyor.
PRODUCT_PROMPT = (
    "Ürünün üzerinde okunabilen bir MARKA veya MODEL yazısı varsa, onu "
    "İLK terim olarak yaz (göründüğü gibi; iki kelimeyse aralarında boşluk "
    "bırak). Marka yazısı yoksa bu adımı atla.\n"
    "Ardından görseldeki ürünü TÜRKÇE olarak adlandır. Tek bir ad yerine, virgülle "
    "ayrılmış 2-4 alternatif ad yaz; en spesifikten en genele doğru sırala ve "
    "Türkiye'de bu ürün için yaygın kullanılan farklı adlandırmaları da ekle "
    "(yabancı kökenli ad kullanılıyorsa hem onu hem Türkçe karşılığını yaz).\n"
    "Örnekler:\n"
    "chiller, su soğutma grubu, soğutucu, soğutma ünitesi\n"
    "alüminyum boru, alüminyum profil, boru\n"
    "cam bardak, bardak\n"
    "Her ad en fazla üç kelime olsun. Açıklama, numaralandırma veya ek cümle "
    "yazma; sadece virgülle ayrılmış adları yaz. "
    "Görselde bir ürün yoksa sadece şunu yaz: Görselde ürün yok"
)

NO_PRODUCT = "Görselde ürün yok"

_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def guess_mime(image_path):
    return _MIME_BY_EXT.get(os.path.splitext(image_path)[1].lower(), "image/jpeg")


def read_b64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def api_error_detail(response):
    """Saglayicinin JSON govdesindeki asil hata mesajini cikarir.

    raise_for_status() sadece "429 Too Many Requests" gibi ciplak bir metin
    veriyordu; "kredi bitti" ile "hiz siniri" ayirt edilemiyordu.
    """
    try:
        err = response.json().get("error", {})
    except ValueError:
        return response.text[:200]
    if isinstance(err, dict):
        code = err.get("code") or err.get("status") or ""
        msg = err.get("message") or ""
        return f"{code}: {msg}".strip(": ") or response.text[:200]
    return str(err)[:200]


# ---------- OpenAI Image Analysis ------------------------------------------


def analyze_image(image_path):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY tanımlı değil")

    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PRODUCT_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{guess_mime(image_path)};base64,"
                            f"{read_b64(image_path)}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 300,
    }

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json=payload,
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI ({r.status_code}) {api_error_detail(r)}")
    return r.json()["choices"][0]["message"]["content"].strip()


# ---------- Gemini Image Analysis ------------------------------------------


def analyze_image_with_gemini(image_path):
    """Google AI Studio (Gemini) ile gorsel analizi.

    Ekstra paket gerektirmez; OpenAI yolundaki gibi duz REST cagrisi yapar.
    Anahtar https://aistudio.google.com/apikey adresinden alinir.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY tanımlı değil")

    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PRODUCT_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": guess_mime(image_path),
                            "data": read_b64(image_path),
                        }
                    },
                ]
            }
        ],
        # gemini-3.6-flash gorunmeyen akil yurutme tokenlari harciyor ve bunlar
        # da bu butceden dusuyor. 300'de cevap ortadan kesilip
        # ("...pecific):* chiller (or endustri") cop uretiyordu.
        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0},
    }

    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=payload,
        timeout=60,
    )
    if r.status_code == 429:
        # Ucretsiz katman dakika basina istek siniri koyuyor. Ham hata
        # metni cok uzun; kullaniciya ne kadar bekleyecegini soylemek yeterli.
        saniye = None
        try:
            for d in r.json().get("error", {}).get("details", []):
                if "retryDelay" in d:
                    saniye = int(float(str(d["retryDelay"]).rstrip("s")))
        except (ValueError, TypeError, AttributeError):
            pass
        if saniye is None:
            m = re.search(r"retry in ([\d.]+)s", r.text)
            saniye = int(float(m.group(1))) if m else None
        bekle = f"~{saniye + 1} saniye" if saniye else "bir dakika"
        raise RuntimeError(
            f"Gemini kota sınırı aşıldı (ücretsiz katman). {bekle} sonra "
            "tekrar deneyin."
        )
    if not r.ok:
        raise RuntimeError(f"Gemini ({r.status_code}) {api_error_detail(r)}")

    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        # Guvenlik filtresi icerigi engellemis olabilir.
        reason = (data.get("promptFeedback") or {}).get("blockReason", "bilinmiyor")
        raise RuntimeError(f"Gemini yanıt döndürmedi (sebep: {reason})")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise RuntimeError("Gemini boş yanıt döndürdü")
    return text


# ---------- Google Vision API Image Analysis -------------------------------


def analyze_image_with_google_vision(image_path):
    """Google Vision API ile resim analizi"""
    try:
        if not GOOGLE_VISION_AVAILABLE:
            raise Exception("Google Vision API not installed")

        # Google Cloud credentials check
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not credentials_path or not os.path.exists(credentials_path):
            raise Exception(
                "Google Cloud credentials not found. Set GOOGLE_APPLICATION_CREDENTIALS environment variable"
            )

        # Google Vision client oluştur
        client = vision.ImageAnnotatorClient()

        # Resmi oku
        with open(image_path, "rb") as image_file:
            content = image_file.read()

        image = vision.Image(content=content)

        # Label detection (nesne tanıma)
        response = client.label_detection(image=image)
        labels = response.label_annotations

        # Text detection (metin tanıma) - ürün ismi için
        text_response = client.text_detection(image=image)
        texts = text_response.text_annotations

        # Object localization (nesne lokalizasyonu)
        objects = client.object_localization(image=image).localized_object_annotations

        # En iyi sonucu seç
        product_candidates = []

        # Objects'ten ürün adlarını al
        for obj in objects:
            if obj.score > 0.5:  # Güven skoru %50'den yüksek olanlar
                product_candidates.append((obj.name, obj.score))

        # Labels'den ürün adlarını al
        for label in labels[:5]:  # İlk 5 label
            if label.score > 0.7:  # Güven skoru %70'den yüksek olanlar
                product_candidates.append((label.description, label.score))

        # Metinlerden ürün ismi çıkarmaya çalış
        if texts and len(texts[0].description.strip()) > 0:
            full_text = texts[0].description.strip()
            # Metin içinden ürün ismi çıkarmak için basit heuristic
            words = full_text.split()
            for word in words:
                if len(word) > 3 and word.isalpha():
                    product_candidates.append((word.lower(), 0.6))

        if not product_candidates:
            return "There is no product in image"

        # En yüksek skora sahip ürünü seç
        best_product = max(product_candidates, key=lambda x: x[1])

        # Ürün ismini temizle ve döndür
        product_name = best_product[0].lower()

        # Çok genel isimleri daha spesifik hale getir
        replacements = {
            "bottle": "water bottle",
            "container": "plastic container",
            "food": "food product",
            "drink": "beverage",
            "clothing": "apparel",
            "footwear": "shoes",
            "furniture": "home furniture",
        }

        product_name = replacements.get(product_name, product_name)

        return product_name

    except Exception as e:
        # Yedekleme artik analyze_image_hybrid'deki zincirde yapiliyor;
        # burada sabit OpenAI'a dusmek Gemini secilmisken de OpenAI'a
        # gitmeye sebep oluyordu.
        raise RuntimeError(f"Google Vision: {e}") from e


# ---------- Saglayici zinciri ----------------------------------------------


def gemini_available():
    return bool(os.getenv("GEMINI_API_KEY"))


def openai_available():
    return bool(os.getenv("OPENAI_API_KEY"))


def google_vision_available():
    return GOOGLE_VISION_AVAILABLE and bool(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )


# lens_type -> (fonksiyon, kullanilabilir mi)
PROVIDERS = {
    "gemini": (analyze_image_with_gemini, gemini_available),
    "google": (analyze_image_with_google_vision, google_vision_available),
    "custom": (analyze_image, openai_available),
}


def analyze_image_hybrid(image_path, lens_type="custom"):
    """Secilen lensi dener, basarisiz olursa yapilandirilmis digerlerine duser.

    (urun_adi, gercekten_kullanilan_lens) doner. Eskiden her hata sabit sekilde
    OpenAI'a dusuyordu; OpenAI kotasi bitince hicbir lens calismiyordu.
    """
    order = [lens_type] + [k for k in PROVIDERS if k != lens_type]
    errors = []
    no_product = False  # en az bir lens goruntuyu okudu ama urun goremedi

    for name in order:
        func, is_available = PROVIDERS[name]
        if not is_available():
            continue
        try:
            ham = (func(image_path) or "").strip()
            if not ham:
                raise RuntimeError("boş yanıt")
            # "Gorselde urun yok" cevabini temizlik bozmasin diye once bakilir.
            result = ham if NO_PRODUCT.lower() in ham.lower() else (
                temizle_model_ciktisi(ham) or ham
            )
            # "Urun yok" teknik bir hata degil, gecerli bir cevap. Yine de
            # baska bir lens tanıyabilir diye zincire devam ediyoruz.
            if NO_PRODUCT.lower() in result.lower():
                print(f"Lens '{name}': görüntüde ürün görülmedi.")
                no_product = True
                continue
            if name != lens_type:
                print(f"'{lens_type}' başarısız oldu, '{name}' ile devam edildi.")
            return result, name
        except Exception as e:
            print(f"Lens '{name}' hatası: {e}")
            errors.append(f"{name}: {e}")

    # Hicbir lens urun bulamadi ama en az biri goruntuyu basariyla okudu:
    # kullaniciya teknik hata degil, anlasilir "urun yok" mesaji donmeli.
    if no_product:
        return NO_PRODUCT, lens_type

    if not errors:
        raise RuntimeError(
            "Hiçbir görsel analiz sağlayıcısı yapılandırılmamış "
            "(GEMINI_API_KEY / OPENAI_API_KEY / GOOGLE_APPLICATION_CREDENTIALS)"
        )

    # Tum saglayicilarin ham hatasini birlestirmek okunmaz bir yigin
    # uretiyordu (Google Vision tek basina 700+ karakter). Kullaniciya
    # SECILEN lensin hatasi gosteriliyor; digerleri zaten loglandi.
    birincil = next(
        (e for e in errors if e.startswith(f"{lens_type}:")), errors[0]
    )
    mesaj = birincil.split(":", 1)[-1].strip()[:200]
    if len(errors) > 1:
        mesaj += f" (diğer {len(errors) - 1} sağlayıcı da başarısız oldu)"
    raise RuntimeError(mesaj)


# ---------------------------------------------------------------------------
#  ALLEMTIA KATALOG ARAMASI
# ---------------------------------------------------------------------------
#
# Onceden burada Europages scraper'i vardi: arama sonuc sayfalarini kazir,
# her firmanin sitesine girip telefon/e-posta toplardi. Iki sebeple kaldirildi:
#   1) Europages aramayi Nuxt SPA'ya tasidi, sunucudan gelen HTML'de artik
#      firma bilgisi yok (uzerine AWS WAF captcha eklendi) - kod her ortamda
#      0 sonuc donuyordu.
#   2) Asil ihtiyac zaten kendi katalogumuzda arama: kullanici urunun
#      fotografini cekip allemtia'da satan magazalari gormeli.

ALLEMTIA_API = os.getenv(
    "ALLEMTIA_API_URL", "https://sadmin.allemtia.com.tr/api"
).rstrip("/")
ALLEMTIA_TENANT_ID = os.getenv("ALLEMTIA_TENANT_ID", "")
ALLEMTIA_SITE = os.getenv("ALLEMTIA_SITE_URL", "https://allemtia.com.tr").rstrip("/")
PRODUCT_PATH = os.getenv("ALLEMTIA_PRODUCT_PATH", "shop-details").strip("/")
SEARCH_PAGE_SIZE = 24  # API varsayilaniyla ayni; buyuk sayfalar yaniti yavaslatiyor


def product_url(product):
    # Urun detay rotasi frontend'de src/app/shop-details/[id]/page.tsx;
    # yani /shop-details/<slug>. Onceki /urun/<slug> tahminiydi ve 404 veriyordu.
    slug = product.get("slug")
    return f"{ALLEMTIA_SITE}/{PRODUCT_PATH}/{slug}" if slug else ALLEMTIA_SITE


# Kapanis parantezi istege bagli: cevap MAX_TOKENS ile kesilince
# "(or endustri" gibi kapanmamis parca kaliyor, o da atilmali.
_PARANTEZ = re.compile(r"\([^)]*\)?")
_MARKDOWN = re.compile(r"[*_#`]+")


def temizle_model_ciktisi(text):
    """Modelin serbest metnini virgullu ad listesine indirger.

    Istem "sadece adlari yaz" dese de model bazen markdown, aciklama veya
    parantezli not ekliyor. Ayrica cevap MAX_TOKENS ile kesilirse yarim
    kalan satir cop olarak geliyor. Burada bunlar ayiklaniyor.
    """
    if not text:
        return ""

    text = _MARKDOWN.sub("", text)
    satirlar = [l.strip() for l in text.splitlines() if l.strip()]
    if not satirlar:
        return ""
    # Model aciklama yaparsa adlar genelde virgullu satirda olur; yoksa
    # ilk satiri al (son satir, kesilmis cop olma ihtimali en yuksek olan).
    secilen = next((l for l in satirlar if "," in l), satirlar[0])

    adlar = []
    for ham in secilen.split(","):
        ad = _PARANTEZ.sub(" ", ham)          # "chiller (or ...)" -> "chiller"
        ad = ad.split(":")[-1]                # "Most specific: chiller" -> "chiller"
        ad = _MARKDOWN.sub("", ad).strip(" .-\u2013\u2014")
        # Urun adi kisa olur; uzun/cok kelimeli parca aciklamadir, atilir.
        if ad and len(ad) <= 30 and 1 <= len(ad.split()) <= 3:
            adlar.append(ad)

    return ", ".join(dict.fromkeys(adlar))[:200]


def _kok(kelime):
    """Turkce ek kirpma yerine kaba kok: 5 karakterden uzun kelimeyi kisaltir.

    Turkce eklemeli bir dil; model "sogutma" der, katalogda "Sogutucu" yazar.
    Ikisinin de ilk 5 harfi "sogut" oldugu icin bu kaba kirpma ikisini
    esleyebiliyor. Kelime zaten kisaysa oldugu gibi birakilir.
    """
    return kelime[:5] if len(kelime) > 5 else kelime


# Urunu tanimlayan ad degil, onu niteleyen sifatlar. Kok olarak aranirsa
# "endus" gibi parcalar aciklamasinda "endustriyel" gecen her seyi getirir.
# Malzeme adlari (aluminyum, demir, cam...) bilerek DISARIDA: onlar ayirt edici.
# "grubu", "cihazi", "unitesi" gibi genel sozcukler tamlamanin sonunda durur
# ama urunu tanimlamaz: "su sogutma GRUBU"nda anlamli kelime "sogutma"dir.
# Bunlar atlanip bir onceki kelimeye bakilir.
_GENEL_ADLAR = {
    "grubu", "grup", "cihazı", "cihazi", "cihaz", "ünitesi", "unitesi",
    "ünite", "unite", "sistemi", "sistem", "ekipmanı", "ekipmani",
    "ekipman", "aleti", "alet", "aygıtı", "aygiti", "aygıt", "seti", "set",
    "tipi", "tip", "modeli", "model", "ürünü", "urunu", "ürün", "urun",
}

_NITELEYICILER = {
    "endüstriyel", "endustriyel", "sanayi", "sanayii", "profesyonel",
    "otomatik", "dijital", "elektrikli", "elektronik", "portatif",
    "taşınabilir", "tasinabilir", "ticari", "standart", "özel", "ozel",
    "genel", "yüksek", "yuksek", "büyük", "buyuk", "küçük", "kucuk",
    "mini", "ağır", "agir", "hafif", "yeni", "modern",
}


def arama_adaylari(keyword):
    """Anahtar kelimeyi denenecek arama terimleri listesine cevirir.

    Girdi virgullu olabilir ("chiller cihazi, su sogutma grubu") - gorsel
    analizi esanlam listesi donduruyor. Once tam esanlamlar denenir; hicbiri
    tutmazsa kelime kokleri denenir (uzun kelimeler once, daha ayirt edici).
    """
    esanlamlar = [t.strip() for t in keyword.split(",") if t.strip()]
    if not esanlamlar:
        return []

    adaylar = list(esanlamlar)

    # Tam esanlamlar tutmazsa: kelime kokleri. Turkcede tamlamanin ana adi
    # SONDA oldugu icin ("endustriyel CHILLER") kelimeler tersten taranir;
    # eskiden uzunluga gore siralaniyordu ve "endustriyel" gibi sifatlar one
    # gecip alakasiz sonuc getiriyordu. Niteleyiciler ve 4 harften kisa
    # parcalar hic denenmez.
    # SADECE ana ad (son kelime). Eskiden tum kelimeler deneniyordu ve
    # "kalip sartlandirici" tutmayinca niteleyen kisim "kalip" araniyor,
    # chiller fotografina 6 kalip urunu geliyordu. Niteleyen kelime urunu
    # tanimlamaz; ana ad tutmuyorsa sonuc yok demektir.
    # Her esanlamdan TEK kok: sondan basa dogru ilk "anlamli" kelime.
    # Sadece son kelimeyi almak yetmiyordu ("su sogutma grubu" -> "grubu",
    # sonuc yok); tum kelimeleri denemek ise gurultu yapiyordu
    # ("kalip sartlandirici" -> "kalip" -> 6 alakasiz kalip urunu).
    kokler = []
    for t in esanlamlar:
        parcalar = t.split()
        anlamli = next(
            (
                k for k in reversed(parcalar)
                if len(k) >= 4
                and k.lower() not in _GENEL_ADLAR
                and k.lower() not in _NITELEYICILER
            ),
            None,
        )
        if anlamli:
            kokler.append(_kok(anlamli))
    adaylar.extend(kokler)

    gorulen, benzersiz = set(), []
    for a in adaylar:
        k = a.lower()
        if k not in gorulen:
            gorulen.add(k)
            benzersiz.append(a)
    return benzersiz


def search_catalog(keyword, limit=SEARCH_PAGE_SIZE, on_page=None):
    """allemtia katalogunda arar; ilk tutan terimin sonuclarini dondurur.

    Gorsel analizinden gelen ad katalog adlandirmasiyla birebir tutmuyor:
    model "Chiller" der, katalogda "Endustriyel Evaporatif Sogutucu" yazar.
    Bu yuzden model esanlam listesi donduruyor ve hepsi sirayla deneniyor.

    (urunler, kullanilan_terim) doner; arayuz hangi terimin tuttugunu gosterir.
    """
    adaylar = arama_adaylari(keyword)
    if not adaylar:
        return [], keyword

    for aday in adaylar:
        sonuc = _search_once(aday, limit, None)
        if sonuc:
            if on_page:
                on_page(sonuc)
            return sonuc, aday
    return [], adaylar[0]


def _search_once(keyword, limit=SEARCH_PAGE_SIZE, on_page=None):
    headers = {"Accept": "application/json"}
    found = []
    page = 1

    while len(found) < limit:
        params = {
            "search": keyword,
            "per_page": min(SEARCH_PAGE_SIZE, limit - len(found)),
            "page": page,
        }
        if ALLEMTIA_TENANT_ID:
            params["tenant_id"] = ALLEMTIA_TENANT_ID

        r = requests.get(
            f"{ALLEMTIA_API}/home/products",
            params=params,
            headers=headers,
            timeout=45,
        )
        if not r.ok:
            raise RuntimeError(
                f"Katalog API ({r.status_code}) {api_error_detail(r)}"
            )

        payload = r.json()

        # Backend aramayi gercekten uyguladi mi? Eski surum bilinmeyen
        # parametreyi sessizce yok sayip TUM katalogu donduruyor; bu durumda
        # kullaniciya alakasiz urunleri "sonuc" diye gostermektense hata
        # vermek dogru.
        if page == 1 and payload.get("search") != keyword:
            raise RuntimeError(
                "Katalog API'si arama parametresini desteklemiyor "
                "(backend guncellenmeli). Alakasiz sonuc donmemesi icin "
                "arama durduruldu."
            )

        items = payload.get("data") or []
        if not items:
            break

        batch = [
            {
                "id": it.get("id"),
                "ad": it.get("name") or "",
                "fiyat": it.get("price"),
                "gorsel": it.get("productImg") or "",
                "url": product_url(it),
                "aciklama": (it.get("description") or "")[:160],
            }
            for it in items
        ]
        found.extend(batch)
        if on_page:
            on_page(batch)

        meta = payload.get("meta") or {}
        if page >= (meta.get("last_page") or page):
            break
        page += 1

    return found[:limit]


# ---------------------------------------------------------------------------
#  ROUTES
# ---------------------------------------------------------------------------


@app.route("/")
def lens_selection():
    """Ana sayfa - Lens seçim ekranı"""
    return send_from_directory(".", "lens_selection.html")


@app.route("/scanner")
def scanner():
    """Scanner sayfası"""
    return send_from_directory(".", "index.html")


@app.route("/api/lens_status", methods=["GET"])
def lens_status():
    """Lens durumlarını kontrol et"""
    return jsonify(
        {
            "openai_available": openai_available(),
            "google_vision_available": google_vision_available(),
            "gemini_available": gemini_available(),
        }
    )


@app.route("/api/start_scan", methods=["POST"])
def start_scan():
    if not request.is_json:
        return jsonify({"success": False, "error": "JSON verisi gerekli"}), 400
    data = request.get_json()
    keyword = (data.get("keyword") or "").strip()
    if not keyword:
        return jsonify({"success": False, "error": "keyword parametresi gerekli"}), 400

    purge_old_scans()

    job_id = str(uuid.uuid4())
    active_scans[job_id] = {
        "status": "running",
        "start_time": datetime.now().isoformat(),
        "keyword": keyword,
        "rows": [],
        "rows_found": 0,
        "new_rows": [],
        "error": None,
        "finished_at": None,
    }

    def run_search(term: str, job_id: str):
        def _should_stop():
            return active_scans.get(job_id, {}).get("status") in (
                "stopping",
                "stopped",
            )

        def _on_page(batch):
            d = active_scans.get(job_id)
            if d is None:
                return
            d["rows"].extend(batch)
            d["new_rows"].extend(batch)
            d["rows_found"] = len(d["rows"])

        try:
            _, used_term = search_catalog(term, on_page=_on_page)
            d = active_scans.get(job_id)
            if d is not None:
                d["used_keyword"] = used_term
                d["status"] = "stopped" if _should_stop() else "completed"
                d["finished_at"] = time.time()
        except Exception as e:
            traceback.print_exc()
            d = active_scans.get(job_id)
            if d is not None:
                d["status"] = "error"
                d["error"] = str(e)
                d["finished_at"] = time.time()

    t = threading.Thread(target=run_search, args=(keyword, job_id))
    t.daemon = True
    t.start()
    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/scan_status/<job_id>", methods=["GET"])
def scan_status(job_id):
    # Erisim kontrolu job_id'nin kendisidir: uuid4 tahmin edilemez.
    # Eski user_id kontrolu istemcinin gonderdigi degere bakiyordu ve
    # user_id="admin" ile herkes her isi gorebiliyordu; guvenlik saglamiyordu.
    d = active_scans.get(job_id)
    if d is None:
        return jsonify({"status": "error", "error": "Tarama işi bulunamadı"})
    new_rows = d["new_rows"][:]
    d["new_rows"] = []
    resp = {
        "status": d["status"],
        "used_keyword": d.get("used_keyword"),
        "rows_found": d["rows_found"],
        "new_rows": new_rows,
        "rows": d["rows"],
    }
    if d["status"] in ("completed", "error", "stopped"):
        resp.update(
            {"total_rows": len(d["rows"]), "error": d["error"]}
        )
    return jsonify(resp)


@app.route("/api/stop_scan/<job_id>", methods=["POST"])
def stop_scan(job_id):
    d = active_scans.get(job_id)
    if d is None:
        return jsonify({"success": False, "error": "Tarama işi bulunamadı"})
    if d["status"] == "running":
        d["status"] = "stopping"
    return jsonify({"success": True, "status": d["status"]})


@app.route("/api/analyze_image", methods=["POST"])
def analyze_image_endpoint():
    if "image" not in request.files:
        return jsonify({"success": False, "error": "Resim dosyası gerekli"}), 400

    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400

    # Uzantiyi kullanicinin dosya adindan degil, sadece izin verilen listeden al.
    # Eskiden file.filename dogrudan temp yola gomuluyordu; "../" iceren bir ad
    # dosyayi temp dizininin disina yazabilirdi.
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return (
            jsonify(
                {
                    "success": False,
                    "error": f"Desteklenmeyen dosya türü. İzin verilenler: "
                    f"{', '.join(sorted(ALLOWED_IMAGE_EXTS))}",
                }
            ),
            400,
        )

    lens_type = request.form.get("lens_type", "custom")
    if lens_type not in PROVIDERS:
        lens_type = "custom"

    fd, temp_path = tempfile.mkstemp(prefix="lens_", suffix=ext)
    os.close(fd)
    try:
        file.save(temp_path)

        # Icerigin gercekten gorsel oldugunu sihirli baytlardan dogrula:
        # uzanti degistirilerek rastgele dosya gonderilebilir.
        if not is_supported_image(temp_path):
            return (
                jsonify({"success": False, "error": "Dosya geçerli bir resim değil"}),
                400,
            )

        product_name, used_lens = analyze_image_hybrid(temp_path, lens_type)

        if NO_PRODUCT.lower() in product_name.lower():
            return (
                jsonify({"success": False, "error": "Resimde ürün bulunamadı"}),
                400,
            )

        # Model esanlam listesi donduruyor ("chiller, su sogutma grubu, ...").
        # Arayuzde ilkini gosteriyoruz; arama ise listenin tamamini deniyor.
        adaylar = arama_adaylari(product_name)
        return jsonify(
            {
                "success": True,
                "product": adaylar[0] if adaylar else product_name,
                "query": product_name,
                "candidates": adaylar,
                "lens_used": used_lens,
                "google_vision_available": GOOGLE_VISION_AVAILABLE,
            }
        )
    except Exception as e:
        traceback.print_exc()
        return (
            jsonify({"success": False, "error": f"Resim analiz hatası: {str(e)}"}),
            500,
        )
    finally:
        # Tek cikis noktasi: eskiden temizlik uc ayri dalda tekrarlaniyor,
        # bazi hatalarda temp dosya diskte kaliyordu.
        try:
            os.remove(temp_path)
        except OSError:
            pass


@app.errorhandler(413)
def too_large(_e):
    return (
        jsonify({"success": False, "error": "Dosya çok büyük (en fazla 10MB)"}),
        413,
    )


if __name__ == "__main__":
    print(f"Google Vision API Available: {GOOGLE_VISION_AVAILABLE}")
    if GOOGLE_VISION_AVAILABLE:
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        print(
            f"Google Credentials: {'Found' if creds_path and os.path.exists(creds_path) else 'Not found'}"
        )
    print("Starting server...")
    app.run(host="0.0.0.0", port=5008, debug=False, threaded=True)
