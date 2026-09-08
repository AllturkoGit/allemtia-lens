# ---------------------------------------------------------------------------
#  corporate_server.py   –   Sunucu + Europages scraper (TÜM KOD)
# ---------------------------------------------------------------------------
import os, re, json, time, uuid, ssl, math, random, threading, traceback
import unicodedata, concurrent.futures, requests, base64
from datetime import datetime
from urllib.parse import urlparse, urljoin, unquote, urlencode
from functools import wraps
from dotenv import load_dotenv

load_dotenv()

import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS
# Playwright removed - using requests only
from googletrans import Translator
import openai
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------- Genel yapılandırma ---------------------------------------------
BASE_DIR = os.getenv("MAPSCAN_DATA_DIR", r"C:/ProgramData/NexleadTr")
app = Flask(__name__)
CORS(app)                           # Aynen önceki sunucu gibi tamamen açık

active_scans: dict[str, dict] = {}  # bellekte iş takibi

# ---------- OpenAI Image Analysis ------------------------------------------

def analyze_image(image_path):
    try:
        # API anahtarı .env dosyasından okunur (koda gömülmez)
        openai.api_key = os.getenv("OPENAI_API_KEY")
        
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        # Yeni OpenAI API formatı
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {openai.api_key}"
        }

        payload = {
            "model": "gpt-4o",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Only write the product name using minimal words. When the product is a glass cup, explicitly output 'glass cup' (not just 'glass') to avoid confusion with the raw material. Preferably one word; if it's two words, that's acceptable. If a synonym is available, choose the more specific version. If there is no product in image, only write 'There is no product in image.'"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 300
        }

        response = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        
        result = response.json()['choices'][0]['message']['content']
        return result
    except Exception as e:
        print(f"Error in analyze_image: {str(e)}")
        raise

# SSE kaldırıldı - sadece JSON API kullanılıyor

# ---------------------------------------------------------------------------
#  EUROPAGES SCRAPER ( corporate_cli.py içeriği → TAMAMI )
# ---------------------------------------------------------------------------

# Browser path removed - using requests only

CONTACT_KEYWORDS = [
    "contact", "contacts", "contact-us", "contactus", "contact_us",
    "iletisim", "iletişim", "bize-ulasin", "bize_ulasin", "bizeulasin",
    "kontakt", "contacto", "contatto", "contato", "contacte", "kontakty",
    "контакты", "контакт", "контакти",
    "聯絡我們", "联系我们", "お問い合わせ", "연락처",
    "اتصل-بنا", "ارتباط", "संपर्क", "ติดต่อเรา", "liên-hệ"
]

def normalize_text(text):
    text = unicodedata.normalize('NFC', str(text))
    tr_map = {'ş':'s','Ş':'S','ö':'o','Ö':'O','ü':'u','Ü':'U','ğ':'g','Ğ':'G',
              'ı':'i','I':'I','ç':'c','Ç':'C','İ':'I'}
    for o,n in tr_map.items(): text = text.replace(o,n)
    return text.lower().strip()

def clean_email(email):
    if not email: return email
    email = unquote(email)
    email = re.sub(r'u00[0-9a-fA-F]{2}', '', email)
    email = re.sub(r'u0[0-9a-fA-F]{3}', '', email)
    email = email.strip()
    if len(email) > 35: return None
    if email.lower().endswith(('.gif','.png','.jpg','.jpeg','.bmp')): return None
    return email

def clean_phone(phone):
    if not phone: return phone
    phone = unquote(phone)
    out=[]
    for part in re.split(r'[^\d\s]+|[a-zA-Z]+', phone):
        d=re.sub(r'\D','',part.strip())
        if 7<=len(d)<=15: out.append(d)
    return out

def is_contact_url(t): return any(k in normalize_text(t) for k in CONTACT_KEYWORDS)

def extract_contact_info(soup):
    tel,mail=set(),set()
    def links():
        for a in soup.find_all('a',href=lambda x:x and 'mailto:' in x):
            m=clean_email(a['href'].replace('mailto:','').strip())
            if m and '@' in m: mail.add(m)
        for a in soup.find_all('a',href=lambda x:x and 'tel:' in x):
            for t in clean_phone(a['href'].replace('tel:','')): tel.add(t)
    def regex():
        for m in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',str(soup)):
            m=clean_email(m)
            if m and '@' in m: mail.add(m)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(links); ex.submit(regex); ex.shutdown(wait=True)
    return list(tel),list(mail)

def find_contact_links(soup, base_url):
    out=[]
    for a in soup.find_all('a',href=True):
        h=a['href']
        if h and is_contact_url(h):
            out.append(h if h.startswith(('http://','https://')) else urljoin(base_url,h))
    return out

# chrome_driver_mutex removed - no longer needed

# -------------------- KURUMSAL WORKER – TAM GÖVDE ---------------------------
def kurumsal_arama_worker(kurumsal_sektor, kurumsal_dosya, kurumsal_lokasyon,
                          selected_types=None, callback=None):
    try:
        translator = Translator()
        kurumsal_sektor_en = translator.translate(kurumsal_sektor, dest='en').text
    except Exception:
        kurumsal_sektor_en = kurumsal_sektor
    
    df = pd.DataFrame(columns=['Adı','Website','Email','Telefon'])
    original_ctx = ssl._create_default_https_context
    ssl._create_default_https_context = ssl._create_unverified_context
    all_urls=[]
    
    # Ülke konfigürasyonları
    country_configs = {
        "TR": {
            "locationCountryCode": "TR",
            "locationLatitude": "38.9637",
            "locationLongitude": "35.2433",
            "locationName": "Turkey"
        },
        "DE": {
            "locationCountryCode": "DE",
            "locationLatitude": "51.1657",
            "locationLongitude": "10.4515",
            "locationName": "Germany"
        },
        "GB": {
            "locationCountryCode": "GB",
            "locationLatitude": "55.3781",
            "locationLongitude": "-3.4360",
            "locationName": "United Kingdom"
        },
        "NL": {
            "locationCountryCode": "NL",
            "locationLatitude": "52.1326",
            "locationLongitude": "5.2913",
            "locationName": "Netherlands"
        },
        "FR": {
            "locationCountryCode": "FR",
            "locationLatitude": "46.2276",
            "locationLongitude": "2.2137",
            "locationName": "France"
        },
        "ES": {
            "locationCountryCode": "ES",
            "locationLatitude": "40.4637",
            "locationLongitude": "-3.7492",
            "locationName": "Spain"
        },
        "IT": {
            "locationCountryCode": "IT",
            "locationLatitude": "41.8719",
            "locationLongitude": "12.5674",
            "locationName": "Italy"
        },
        "CZ": {
            "locationCountryCode": "CZ",
            "locationLatitude": "49.8175",
            "locationLongitude": "15.4730",
            "locationName": "Czech Republic"
        },
        "DK": {
            "locationCountryCode": "DK",
            "locationLatitude": "56.2639",
            "locationLongitude": "9.5018",
            "locationName": "Denmark"
        },
        "EE": {
            "locationCountryCode": "EE",
            "locationLatitude": "58.5953",
            "locationLongitude": "25.0136",
            "locationName": "Estonia"
        },
        "GR": {
            "locationCountryCode": "GR",
            "locationLatitude": "39.0742",
            "locationLongitude": "21.8243",
            "locationName": "Greece"
        },
        "LT": {
            "locationCountryCode": "LT",
            "locationLatitude": "55.1694",
            "locationLongitude": "23.8813",
            "locationName": "Lithuania"
        },
        "HU": {
            "locationCountryCode": "HU",
            "locationLatitude": "47.1625",
            "locationLongitude": "19.5033",
            "locationName": "Hungary"
        },
        "NO": {
            "locationCountryCode": "NO",
            "locationLatitude": "60.4720",
            "locationLongitude": "8.4689",
            "locationName": "Norway"
        },
        "PL": {
            "locationCountryCode": "PL",
            "locationLatitude": "51.9194",
            "locationLongitude": "19.1451",
            "locationName": "Poland"
        },
        "PT": {
            "locationCountryCode": "PT",
            "locationLatitude": "39.3999",
            "locationLongitude": "-8.2245",
            "locationName": "Portugal"
        },
        "RO": {
            "locationCountryCode": "RO",
            "locationLatitude": "45.9432",
            "locationLongitude": "24.9668",
            "locationName": "Romania"
        },
        "SI": {
            "locationCountryCode": "SI",
            "locationLatitude": "46.1512",
            "locationLongitude": "14.9955",
            "locationName": "Slovenia"
        },
        "SK": {
            "locationCountryCode": "SK",
            "locationLatitude": "48.6690",
            "locationLongitude": "19.6990",
            "locationName": "Slovakia"
        },
        "FI": {
            "locationCountryCode": "FI",
            "locationLatitude": "61.9241",
            "locationLongitude": "25.7482",
            "locationName": "Finland"
        },
        "BG": {
            "locationCountryCode": "BG",
            "locationLatitude": "42.7339",
            "locationLongitude": "25.4858",
            "locationName": "Bulgaria"
        }
    }
    
    # Ülke kodunu al (locationName'den veya varsayılan)
    country_code = "TR"  # Varsayılan
    if kurumsal_lokasyon in country_configs:
        country_code = kurumsal_lokasyon
    elif kurumsal_lokasyon == "Turkey":
        country_code = "TR"
    
    # Seçilen ülke konfigürasyonunu al
    country_config = country_configs.get(country_code, country_configs["TR"])
    
    base_url = "https://www.europages.co.uk/en/search"
    params = {
        "isPserpFirst": "1",
        "locationCountryCode": country_config["locationCountryCode"],
        "locationKind": "country",
        "locationLatitude": country_config["locationLatitude"],
        "locationLongitude": country_config["locationLongitude"],
        "locationName": country_config["locationName"],
        "locationRadius": "50km",
        "q": kurumsal_sektor_en
    }
    
    # İlk sayfa URL'ini oluştur - URL encoding kullan
    search_url = base_url + "?" + urlencode(params)
    
    # İlk sayfadan pagination bilgisini al
    try:
        hdr = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'tr,en-US;q=0.7,en;q=0.3',
            'Connection': 'keep-alive'
        }
        
        print(f"Arama URL'i: {search_url}")  # Debug için
        
        r = requests.get(search_url, headers=hdr, verify=False, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        # Pagination numaralarını bul
        pagination_numbers = soup.select('[data-test="pagination-number"]')
        if pagination_numbers:
            digits = []
            for num_elem in pagination_numbers:
                text = num_elem.get_text(strip=True)
                if re.fullmatch(r"\d+", text):
                    digits.append(int(text))
            last_page = max(digits) if digits else 1
        else:
            last_page = 1
        
        print(f"Bulunan sayfa sayısı: {last_page}")  # Debug için
        
        # Tüm sayfa URL'lerini oluştur
        all_urls.append(search_url)
        if last_page > 1:
            for page_num in range(2, last_page + 1):
                page_url = f"{base_url}/page/{page_num}?" + urlencode(params)
                all_urls.append(page_url)
        
        if callback: 
            callback(f"Toplam {len(all_urls)} sayfa bulundu", "", "", "")
            
    except Exception as e:
        traceback.print_exc()
        # Hata durumunda en azından ilk sayfayı dene
        all_urls = [search_url]

    def sayfa_isle(url,session,hdr):
        l=[]
        try:
            r=session.get(url,headers=hdr,verify=False,timeout=10); r.raise_for_status()
            s=BeautifulSoup(r.content,'html.parser')
            for link in s.select('a[data-test=\"company-name\"]'):
                h=link.get('href'); 
                if h: l.append(h)
            time.sleep(random.uniform(0.3,0.7))
        except: pass
        return l

    def sirket_linklerini_topla(liste):
        hdr={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                           ' AppleWebKit/537.36 (KHTML, like Gecko)'
                           ' Chrome/91.0.4472.124 Safari/537.36',
             'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
             'Accept-Language':'tr,en-US;q=0.7,en;q=0.3','Connection':'keep-alive'}
        tum=[]
        with requests.Session() as sess:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                fut={ex.submit(sayfa_isle,u,sess,hdr):u for u in liste}
                for f in concurrent.futures.as_completed(fut): tum.extend(f.result())
        if callback: callback(f"Toplam {len(tum)} şirket linki bulundu","","","")
        return tum

    def process_contact_page(link,hdr):
        try:
            r=requests.get(link,headers=hdr,verify=False,timeout=10)
            return extract_contact_info(BeautifulSoup(r.text,'html.parser'))
        except: return [],[]

    def istek_gonder(url):
        nonlocal df
        try:
            ep_url=f"https://www.europages.co.uk{url}"
            hdr={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                              ' AppleWebKit/537.36 (KHTML, like Gecko)'
                              ' Chrome/91.0.4472.124 Safari/537.36',
                 'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                 'Accept-Language':'tr,en-US;q=0.7,en;q=0.3','Connection':'keep-alive'}
            r=requests.get(ep_url,headers=hdr,verify=False,timeout=15); r.raise_for_status()
            soup=BeautifulSoup(r.content,'html.parser')
            sirket_adi=""
            span=soup.select_one('span.font-display-400.overflow-hidden.text-ellipsis[title]')
            if span: sirket_adi=span.get('title','').strip()
            web_btn=soup.select_one('a.btn.btn--subtle.btn--md.website-button')
            if not web_btn: return
            web_link=web_btn.get('href')
            if not web_link: return
            add_phones=[]
            for ph in re.findall(r'\+\d{9,}', str(r.content)):
                d=re.sub(r'\D','',ph)
                if 7<=len(d)<=15: add_phones.append(d)
            try:
                wr=requests.get(web_link,headers=hdr,verify=False,timeout=10)
                wsoup=BeautifulSoup(wr.text,'html.parser')
                phones,mails=extract_contact_info(wsoup)
                phones.extend([p for p in add_phones if p not in phones])
                base=f"{urlparse(web_link).scheme}://{urlparse(web_link).netloc}"
                contact_links=find_contact_links(wsoup,base)[:3]
                if contact_links:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                        fut={ex.submit(process_contact_page,l,hdr):l for l in contact_links}
                        for futr in concurrent.futures.as_completed(fut):
                            cp,cm=futr.result()
                            phones.extend([t for t in cp if t not in phones])
                            mails.extend([m for m in cm if m not in mails])
                e_list=[m for m in mails if m and '@' in m and len(m)<=35
                        and not m.lower().endswith(('.gif','.png','.jpg','.jpeg','.bmp'))]
                p_list=phones
                if e_list or p_list:
                    new=pd.DataFrame({'Adı':[sirket_adi],'Website':[web_link],
                                      'Email':[e_list[0] if e_list else ""],
                                      'Telefon':[p_list[0] if p_list else ""]})
                    df=pd.concat([df,new],ignore_index=True)
                    max_len=max(len(e_list[1:]),len(p_list[1:]))
                    for i in range(max_len):
                        df=pd.concat([df,pd.DataFrame({
                            'Adı':[sirket_adi],'Website':[web_link],
                            'Email':[e_list[i+1] if i+1<len(e_list) else ""],
                            'Telefon':[p_list[i+1] if i+1<len(p_list) else ""]
                        })],ignore_index=True)
                if callback:
                    callback(sirket_adi,web_link,", ".join(e_list),", ".join(p_list))
            except Exception:
                if add_phones:
                    df=pd.concat([df,pd.DataFrame({'Adı':[sirket_adi],'Website':[web_link],
                                                   'Email':[""],'Telefon':[add_phones[0]]})],ignore_index=True)
                    for i in range(1,len(add_phones)):
                        df=pd.concat([df,pd.DataFrame({'Adı':[sirket_adi],'Website':[web_link],
                                                       'Email':[""],'Telefon':[add_phones[i]]})],ignore_index=True)
                if callback: callback(sirket_adi,web_link,"",", ".join(add_phones))
            time.sleep(random.uniform(0.3,0.7))
        except: pass

    sirket_linkleri=sirket_linklerini_topla(all_urls)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        ex.map(istek_gonder,sirket_linkleri)

    def clean_name(ad): return re.sub(r'-\d+$',' ',ad).replace('-',' ').title()
    if not df.empty: df['Adı']=df['Adı'].apply(clean_name)

    data_list=[{'ad':r['Adı'],'website':r['Website'],
                'email':r['Email'],'phone':r['Telefon']} for _,r in df.iterrows()]
    if callback: callback("İşlem tamamlandı","","","")
    return None, data_list
# ------------------- WORKER SONU --------------------------------------------

# ---------------------------------------------------------------------------
#  SUNUCU TARAFI : Playwright işini thread’de çalıştırır
# ---------------------------------------------------------------------------
def scan_europages(sector: str, country: str = "TR", job_id: str | None = None):
    if job_id:
        active_scans[job_id]["status"] = "running"

    def _cb(name, web, mail, phone):
        if not job_id or name.startswith(("Toplam", "İşlem")):
            return
        row = [name, web, phone, mail]           # Şirket, Website, Telefon, Mail
        d = active_scans[job_id]
        d["rows"].append(row)
        d["new_rows"].append(row)
        d["rows_found"] += 1

    try:
        _, data_rows = kurumsal_arama_worker(
            kurumsal_sektor   = sector,
            kurumsal_dosya    = "",
            kurumsal_lokasyon = country,  # Ülke kodu
            selected_types    = None,
            callback          = _cb
        )
        if job_id:
            if data_rows and not active_scans[job_id]["rows"]:
                for r in data_rows:
                    active_scans[job_id]["rows"].append(
                        [r["ad"], r["website"], r["phone"], r["email"]])
                active_scans[job_id]["rows_found"] = len(active_scans[job_id]["rows"])
            
            
            active_scans[job_id]["status"] = "completed"
    except Exception as e:
        traceback.print_exc()
        if job_id:
            active_scans[job_id]["status"] = "error"
            active_scans[job_id]["error"] = str(e)

# SSE endpoint kaldırıldı - sadece JSON API kullanılıyor

# ---------- JSON API --------------------------------------------------------
@app.route('/start_scan', methods=['POST'])
def start_scan():
    if not request.is_json:
        return jsonify({"success": False, "error": "JSON verisi gerekli"}), 400
    data = request.get_json()
    if "keyword" not in data:
        return jsonify({"success": False, "error": "keyword parametresi gerekli"}), 400

    user_id=data.get("user_id","anonymous")
    country=data.get("country", "TR")  # Varsayılan Türkiye
    job_id=str(uuid.uuid4())
    active_scans[job_id]={
        "status":"running","start_time":datetime.now().isoformat(),
        "keyword":data["keyword"],"country":country,
        "rows":[], "rows_found":0,"new_rows":[],
        "file":None,"error":None,"last_error":None,"user_id":user_id
    }
    t=threading.Thread(target=scan_europages,
                       args=(data["keyword"].strip(), country, job_id))
    t.daemon=True; t.start()
    return jsonify({"success":True,"job_id":job_id})

@app.route('/scan_status/<job_id>', methods=['GET'])
def scan_status(job_id):
    user_id=request.args.get("user_id","anonymous")
    if job_id not in active_scans:
        return jsonify({"status":"error","error":"Tarama işi bulunamadı"})
    d=active_scans[job_id]
    if d["user_id"]!=user_id and user_id!="admin":
        return jsonify({"status":"error","error":"Bu tarama işine erişim izniniz yok"})
    new_rows=d["new_rows"][:]; d["new_rows"]=[]
    resp={"status":d["status"],"rows_found":d["rows_found"],
          "new_rows":new_rows,"rows":d["rows"]}
    if d["status"] in ("completed","error"):
        resp.update({"file":d["file"],"total_rows":len(d["rows"]),"error":d["error"]})
    return jsonify(resp)

@app.route('/stop_scan/<job_id>', methods=['POST'])
def stop_scan(job_id):
    user_id=request.json.get("user_id","anonymous") if request.is_json else "anonymous"
    if job_id not in active_scans:
        return jsonify({"success":False,"error":"Tarama işi bulunamadı"})
    if active_scans[job_id]["user_id"]!=user_id and user_id!="admin":
        return jsonify({"success":False,"error":"Durdurma yetkiniz yok"})
    active_scans[job_id]["status"]="stopping"
    return jsonify({"success":True})

@app.route('/analyze_image', methods=['POST'])
def analyze_image_endpoint():
    try:
        if 'image' not in request.files:
            return jsonify({"success": False, "error": "Resim dosyası gerekli"}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({"success": False, "error": "Dosya seçilmedi"}), 400
        
        # Save temporary file - Windows için temp dizini
        import tempfile
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{file.filename}")
        file.save(temp_path)
        
        # Analyze image
        try:
            product_name = analyze_image(temp_path)
            
            # Check if no product found
            if "There is no product in image" in product_name:
                os.remove(temp_path)
                return jsonify({"success": False, "error": "Resimde ürün bulunamadı"}), 400
            
            # Clean up
            os.remove(temp_path)
            
            return jsonify({"success": True, "product": product_name})
            
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({"success": False, "error": f"Resim analiz hatası: {str(e)}"}), 500
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# /scan SSE endpoint kaldırıldı - /start_scan ve /scan_status kullanın

# ---------- Sunucu ----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5008, threaded=True)
