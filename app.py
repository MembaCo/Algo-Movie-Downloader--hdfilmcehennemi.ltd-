# @author: MembaCo.

import sys
import os

# Proje kök dizinini Python'un arama yoluna ekleyerek
# 'database', 'services' gibi yerel modüllerin bulunmasını garantile.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import threading
import time

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    g,
)
from werkzeug.security import check_password_hash, generate_password_hash

# .env dosyasını yeniden yüklemek için eklenen import
from dotenv import load_dotenv

import config

# Veritabanı importlarını güncelliyoruz
from database import (
    get_db,
    setup_database,
    close_db,
    init_settings,
    get_all_settings,
    update_setting,
    get_setting,
)
from logging_config import setup_logging
import services

logger = setup_logging()
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.teardown_appcontext(close_db)

if app.logger.handlers:
    app.logger.removeHandler(app.logger.handlers[0])
app.logger = logger

auto_download_manager_state = {"enabled": False, "thread": None}


def sync_password_hash_from_env():
    """
    Uygulama her başladığında, .env dosyasındaki parola hash'inin
    veritabanındaki ile aynı olmasını sağlar. Bu yöntem, olası önbellekleme
    sorunlarını aşmak için .env dosyasını yeniden yükler.
    """
    logger.info(
        "Ortam değişkenlerindeki parola hash'i veritabanı ile senkronize ediliyor..."
    )
    try:
        # .env dosyasındaki en güncel değeri almak için yeniden yükle
        load_dotenv(override=True)
        env_hash = os.getenv("ADMIN_PASSWORD_HASH")

        if not env_hash:
            logger.error(
                ".env dosyasında ADMIN_PASSWORD_HASH bulunamadı! Lütfen kontrol edin."
            )
            return

        with app.app_context():
            db = get_db()
            # Ayar mevcutsa güncelle, değilse oluştur (UPSERT)
            cursor = db.cursor()
            cursor.execute(
                "REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("ADMIN_PASSWORD_HASH", env_hash),
            )
            db.commit()
        logger.info("Parola hash senkronizasyonu tamamlandı.")
    except Exception as e:
        logger.error(
            f"Parola hash senkronizasyonu sırasında hata oluştu: {e}", exc_info=True
        )


def auto_download_manager():
    """Arka planda çalışarak otomatik indirmeleri yöneten thread."""
    logger.info("Otomatik indirme yöneticisi thread'i başlatıldı.")
    while auto_download_manager_state.get("enabled", False):
        try:
            with app.app_context():
                services.run_auto_download_cycle()
        except Exception as e:
            logger.error(f"Otomatik indirme yöneticisinde hata: {e}", exc_info=True)
        time.sleep(config.AUTO_DOWNLOAD_POLL_INTERVAL)
    logger.info("Otomatik indirme yöneticisi thread'i durduruldu.")


@app.before_request
def require_login():
    if not session.get("logged_in") and request.endpoint not in ["login", "static"]:
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        password_hash = get_setting("ADMIN_PASSWORD_HASH")

        if (
            username == config.ADMIN_USERNAME
            and password_hash
            and check_password_hash(password_hash, password)
        ):
            session["logged_in"] = True
            logger.info(f"Kullanıcı '{username}' başarıyla giriş yaptı.")
            return redirect(url_for("index"))
        else:
            flash("Hatalı kullanıcı adı veya şifre.", "danger")
            logger.warning(f"Başarısız giriş denemesi. Kullanıcı adı: {username}")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Başarıyla çıkış yaptınız.", "success")
    return redirect(url_for("login"))


@app.route("/")
def index():
    db = get_db()
    videos = db.execute("SELECT * FROM videos ORDER BY created_at DESC").fetchall()
    return render_template("index.html", videos=videos, version=config.VERSION)


@app.route("/add", methods=["POST"])
def add_video():
    url = request.form["url"]
    if config.ALLOWED_DOMAIN not in url:
        flash(f"Lütfen geçerli bir {config.ALLOWED_DOMAIN} linki girin.", "warning")
        return redirect(url_for("index"))

    success, message = services.add_video_to_queue(url)
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


@app.route("/add_list", methods=["POST"])
def add_list():
    list_url = request.form["list_url"]
    if config.ALLOWED_DOMAIN not in list_url:
        flash(f"Lütfen geçerli bir {config.ALLOWED_DOMAIN} linki girin.", "warning")
        return redirect(url_for("index"))

    thread = threading.Thread(
        target=services.add_videos_from_list_page_async, args=(app, list_url)
    )
    thread.daemon = True
    thread.start()

    flash(
        "Toplu ekleme işlemi arka planda başlatıldı. Videolar kısa süre içinde kuyruğa eklenecektir.",
        "info",
    )
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    if request.method == "POST":
        # Diğer ayarları kaydet
        update_setting("DOWNLOADS_FOLDER", request.form["downloads_folder"], db)
        update_setting("FILENAME_TEMPLATE", request.form["filename_template"], db)
        update_setting("CONCURRENT_DOWNLOADS", request.form["concurrent_downloads"], db)
        update_setting("SPEED_LIMIT", request.form["speed_limit"], db)

        # Parola değişikliği mantığı
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        password_fields_used = any([current_password, new_password, confirm_password])

        if password_fields_used:
            password_hash = get_setting("ADMIN_PASSWORD_HASH", db)
            if not (current_password and new_password and confirm_password):
                flash(
                    "Parolayı değiştirmek için lütfen tüm alanları doldurun.", "warning"
                )
            elif not check_password_hash(password_hash, current_password):
                flash("Mevcut parolanız hatalı!", "danger")
            elif new_password != confirm_password:
                flash("Yeni parolalar eşleşmiyor!", "danger")
            else:
                new_hash = generate_password_hash(new_password)
                update_setting("ADMIN_PASSWORD_HASH", new_hash, db)
                flash("Parolanız başarıyla güncellendi.", "success")
        else:
            flash("Ayarlar başarıyla kaydedildi.", "success")

        db.commit()
        return redirect(url_for("settings"))

    current_settings = get_all_settings(db)
    return render_template("settings.html", settings=current_settings)


@app.route("/start/<int:video_id>", methods=["POST"])
def start_download(video_id):
    success, message = services.start_download_for_video(video_id)
    flash(message, "info" if success else "warning")
    return redirect(url_for("index"))


@app.route("/stop/<int:video_id>", methods=["POST"])
def stop_download(video_id):
    success, message = services.stop_download_for_video(video_id)
    flash(message, "info" if success else "danger")
    return redirect(url_for("index"))


@app.route("/delete/<int:video_id>", methods=["POST"])
def delete_video(video_id):
    services.delete_video_record(video_id)
    flash("Video kaydı başarıyla silindi.", "success")
    return redirect(url_for("index"))


@app.route("/delete_file/<int:video_id>", methods=["POST"])
def delete_file(video_id):
    success, message = services.delete_video_file(video_id)
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


@app.route("/toggle_auto_download", methods=["POST"])
def toggle_auto_download():
    is_enabled = auto_download_manager_state["enabled"]
    if is_enabled:
        auto_download_manager_state["enabled"] = False
        flash("Otomatik indirme pasif hale getirildi.", "info")
        logger.info("Otomatik indirme durumu: PASİF")
    else:
        auto_download_manager_state["enabled"] = True
        thread = threading.Thread(target=auto_download_manager, daemon=True)
        auto_download_manager_state["thread"] = thread
        thread.start()
        flash("Otomatik indirme aktif hale getirildi.", "info")
        logger.info("Otomatik indirme durumu: AKTİF")
    return redirect(url_for("index"))


@app.route("/status")
def status_api():
    if not session.get("logged_in"):
        return jsonify({"error": "Unauthorized"}), 401
    videos_data = services.get_all_videos_status()
    return jsonify(
        {
            "videos": videos_data,
            "auto_download_enabled": auto_download_manager_state["enabled"],
        }
    )


if __name__ == "__main__":
    with app.app_context():
        setup_database()
        init_settings()

    # Parolayı .env dosyasından veritabanına senkronize et
    sync_password_hash_from_env()

    with app.app_context():
        downloads_folder = get_setting("DOWNLOADS_FOLDER")
        if not os.path.exists(downloads_folder):
            os.makedirs(downloads_folder)

    logger.info("Uygulama başlatılıyor...")
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
