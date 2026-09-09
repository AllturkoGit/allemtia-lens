// PM2 yapilandirmasi - aaPanel Python project manager yerine.
//
// Kullanim (sunucuda, allturko kullanicisi olarak):
//   cd /home/allturko/wwwroot/search.allemtia.com.tr
//   pm2 start ecosystem.config.js
//   pm2 save
//
// ONCE aaPanel > Python Project ekranindan projeyi DURDURUN (silmeyin!).
// Silerseniz nginx ters vekil ayari da gidebilir ve site tamamen kapanir.

module.exports = {
  apps: [
    {
      name: "allemtia-lens",

      // PM2 python'u degil dogrudan gunicorn'u calistiriyor: app.py'deki
      // app.run() Flask'in gelistirme sunucusudur, production icin uygun degil.
      // Gunicorn'un shebang'i kendi venv python'unu gosterdigi icin
      // interpreter "none".
      script: "./venv/bin/gunicorn",
      interpreter: "none",

      // -w 1 ZORUNLU, tercih degil. Tarama isleri active_scans sozlugunde
      // surec belleginde tutuluyor (app.py). Birden fazla worker'da
      // start_scan bir surece, scan_status baska surece duser ve istemci
      // "Tarama isi bulunamadi" alir. Es zamanlilik thread'lerle saglanir.
      args: [
        "-w", "1",
        "--threads", "8",
        "--timeout", "120",       // tarama uzun surebiliyor
        "--graceful-timeout", "30",
        "-b", "127.0.0.1:5008",   // nginx bu adrese vekillik ediyor
        "app:app",                // aaPanel de app.py icindeki app'i kullaniyordu
      ],

      cwd: "/home/allturko/wwwroot/search.allemtia.com.tr",

      // instances 1 + fork: PM2'nin cluster modu YALNIZCA Node.js icindir.
      // Python'da cluster/instances>1, birbirinden habersiz N ayri surec
      // demektir - yukaridaki -w 1 gerekcesinin aynisi.
      instances: 1,
      exec_mode: "fork",

      autorestart: true,
      max_restarts: 10,
      min_uptime: "20s",         // bu sureden once olurse "basarisiz" sayilir
      max_memory_restart: "500M",

      // .env dosyasi load_dotenv() ile cwd'den okunuyor; PM2 ayrica
      // env gecirmiyoruz ki anahtarlar tek yerde (.env) kalsin.
      out_file: "./logs/pm2-out.log",
      error_file: "./logs/pm2-error.log",
      merge_logs: true,
      time: true,                // log satirlarina zaman damgasi
    },
  ],
};
