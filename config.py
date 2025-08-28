# @author: MembaCo.

import os
from dotenv import load_dotenv

# .env dosyasındaki değişkenleri yükle
load_dotenv()

VERSION = "1.0.0"

# --- Veri Klasörü Tanımı ---
# Kalıcı verilerin (veritabanı, loglar vb.) saklanacağı klasör.
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

# --- Güvenlik Ayarları ---
SECRET_KEY = os.getenv("SECRET_KEY", "cok_gizli_bir_anahtar_kodu_bunu_mutlaka_degistir")

# --- Yönetici Giriş Bilgileri ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "scrypt:32768:8:1$...")

# --- Veritabanı Ayarları ---
DATABASE = os.path.join(DATA_DIR, os.environ.get("DATABASE_FILE", "database.db"))

# --- Web Scraping Ayarları ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
VIDEO_KEYWORDS = [".m3u8", "manifest", ".txt"]
# İndirme klasörü artık Ayarlar'dan yönetildiği için buradan kaldırıldı.

# --- Hedef Site Ayarları ---
ALLOWED_DOMAIN = "hdfilmcehennemi.ltd"

# --- Otomatik İndirme Ayarları ---
AUTO_DOWNLOAD_POLL_INTERVAL = 10
