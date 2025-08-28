# @author: MembaCo.

import sqlite3
import logging
from flask import g

import config

logger = logging.getLogger(__name__)


def get_db():
    """
    Uygulama bağlamı (g) içinde mevcut veritabanı bağlantısını açar veya döndürür.
    """
    if "db" not in g:
        try:
            g.db = sqlite3.connect(
                config.DATABASE, detect_types=sqlite3.PARSE_DECLTYPES
            )
            g.db.row_factory = sqlite3.Row
            logger.debug("Yeni veritabanı bağlantısı oluşturuldu.")
        except sqlite3.Error as e:
            logger.error(f"Veritabanı bağlantısı kurulamadı: {e}", exc_info=True)
            raise e
    return g.db


def close_db(e=None):
    """
    Uygulama bağlamındaki mevcut veritabanı bağlantısını kapatır.
    """
    db = g.pop("db", None)
    if db is not None:
        db.close()
        logger.debug("Veritabanı bağlantısı kapatıldı.")


def setup_database():
    """Veritabanı şemasını (tabloları) oluşturur veya günceller."""
    try:
        db = sqlite3.connect(config.DATABASE)
        cursor = db.cursor()
        logger.info("Veritabanı tabloları kontrol ediliyor/oluşturuluyor...")

        # Video tablosu
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'Sırada',
            title TEXT, year TEXT, genre TEXT, description TEXT,
            imdb_score TEXT, director TEXT, cast TEXT, poster_url TEXT,
            source_site TEXT, source_url TEXT,
            progress REAL DEFAULT 0.0,
            filepath TEXT,
            pid INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # --- YENİ: Ayarlar tablosu ---
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        db.commit()
        db.close()
        logger.info("Veritabanı kurulumu başarıyla tamamlandı.")
    except sqlite3.Error as e:
        logger.error(f"Veritabanı kurulumu sırasında hata oluştu: {e}", exc_info=True)
        raise e


def init_settings():
    """Varsayılan ayarları veritabanına yazar (eğer mevcut değillerse)."""
    defaults = {
        "DOWNLOADS_FOLDER": "downloads",
        "FILENAME_TEMPLATE": "{title} - {year}",
        "CONCURRENT_DOWNLOADS": "1",
        "SPEED_LIMIT": "",  # Örn: '2M' (2 Megabyte/s), '500K' (500 Kilobyte/s)
        "ADMIN_PASSWORD_HASH": config.ADMIN_PASSWORD_HASH,  # Başlangıç parolasını config'den alır
    }

    try:
        db = sqlite3.connect(config.DATABASE)
        cursor = db.cursor()
        for key, value in defaults.items():
            cursor.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        db.commit()
        db.close()
        logger.info("Varsayılan ayarlar veritabanına yüklendi.")
    except sqlite3.Error as e:
        logger.error(f"Varsayılan ayarlar yüklenirken hata oluştu: {e}", exc_info=True)


# --- YENİ: Ayar yardımcı fonksiyonları ---
def get_setting(key, db_conn=None):
    """Veritabanından tek bir ayar değerini alır."""
    close_conn = False
    if db_conn is None:
        db_conn = sqlite3.connect(config.DATABASE)
        close_conn = True

    cursor = db_conn.cursor()
    row = cursor.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()

    if close_conn:
        db_conn.close()

    return row[0] if row else None


def get_all_settings(db_conn=None):
    """Veritabanından tüm ayarları bir sözlük olarak alır."""
    close_conn = False
    if db_conn is None:
        db_conn = sqlite3.connect(config.DATABASE)
        db_conn.row_factory = sqlite3.Row
        close_conn = True

    rows = db_conn.execute("SELECT key, value FROM settings").fetchall()

    if close_conn:
        db_conn.close()

    return {row["key"]: row["value"] for row in rows}


def update_setting(key, value, db_conn=None):
    """Veritabanındaki bir ayar değerini günceller."""
    close_conn = False
    if db_conn is None:
        db_conn = sqlite3.connect(config.DATABASE)
        close_conn = True

    cursor = db_conn.cursor()
    cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))

    if close_conn:
        db_conn.commit()
        db_conn.close()
