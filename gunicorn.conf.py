# gunicorn bu dosyayi calisma dizininde otomatik okur (-c vermeye gerek yok).
# Komut satirinda ayni ayar verilirse komut satiri kazanir.

# Worker sayisi bilerek 1: tarama isleri active_scans sozlugunde surec
# bellegindedir (app.py:40). Birden fazla worker'da start_scan bir surece,
# scan_status baska bir surece dusebilir ve is bulunamaz.
# Es zamanlilik thread'lerle saglanir.
workers = 1
threads = 8

# Tarama uzun surebiliyor; varsayilan 30 sn worker'i olduruyordu.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
