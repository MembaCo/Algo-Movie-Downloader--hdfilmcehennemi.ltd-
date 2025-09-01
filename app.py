# @author: MembaCo.

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging
import threading
import time
from multiprocessing import Process

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

import config
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

active_processes = {}
auto_download_manager_state = {"enabled": False, "thread": None}


def sync_password_hash_from_env():
    logger.info(
        "Ortam değişkenlerindeki parola hash'i veritabanı ile senkronize ediliyor..."
    )
    try:
        load_dotenv(override=True)
        env_hash = os.getenv("ADMIN_PASSWORD_HASH")
        if not env_hash:
            logger.error(".env dosyasında ADMIN_PASSWORD_HASH bulunamadı!")
            return
        with app.app_context():
            db = get_db()
            cursor = db.cursor()
            cursor.execute(
                "REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("ADMIN_PASSWORD_HASH", env_hash),
            )
            db.commit()
        logger.info("Parola hash senkronizasyonu tamamlandı.")
    except Exception as e:
        logger.error(f"Parola hash senkronizasyonu sırasında hata: {e}", exc_info=True)


def auto_download_manager():
    """Arka planda çalışarak otomatik indirmeleri yöneten thread."""
    logger.info("Otomatik indirme yöneticisi thread'i başlatıldı.")
    while auto_download_manager_state.get("enabled", False):
        try:
            with app.app_context():
                services.run_auto_download_cycle(active_processes)
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
    return render_template("index.html", version=config.VERSION)


# --- FİLM ROTALARI ---
@app.route("/add_movie", methods=["POST"])
def add_movie():
    url = request.form["url"]
    if config.ALLOWED_DOMAIN not in url:
        flash(f"Lütfen geçerli bir {config.ALLOWED_DOMAIN} linki girin.", "warning")
        return redirect(url_for("index"))
    success, message = services.add_movie_to_queue(url)
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


@app.route("/add_list", methods=["POST"])
def add_list():
    list_url = request.form["list_url"]
    if config.ALLOWED_DOMAIN not in list_url:
        flash(f"Lütfen geçerli bir {config.ALLOWED_DOMAIN} linki girin.", "warning")
        return redirect(url_for("index"))
    thread = threading.Thread(
        target=services.add_movies_from_list_page_async, args=(app, list_url)
    )
    thread.daemon = True
    thread.start()
    flash("Toplu ekleme işlemi arka planda başlatıldı...", "info")
    return redirect(url_for("index"))


@app.route("/movie/start/<int:movie_id>", methods=["POST"])
def start_movie_download(movie_id):
    success, message = services.start_download(movie_id, "movie", active_processes)
    flash(message, "info" if success else "warning")
    return redirect(url_for("index"))


@app.route("/movie/stop/<int:movie_id>", methods=["POST"])
def stop_movie_download(movie_id):
    success, message = services.stop_download(movie_id, "movie")
    flash(message, "info" if success else "danger")
    return redirect(url_for("index"))


@app.route("/movie/delete/<int:movie_id>", methods=["POST"])
def delete_movie(movie_id):
    services.delete_record(movie_id, "movie", active_processes)
    flash("Film kaydı başarıyla silindi.", "success")
    return redirect(url_for("index"))


@app.route("/movie/delete_file/<int:movie_id>", methods=["POST"])
def delete_movie_file(movie_id):
    success, message = services.delete_item_file(movie_id, "movie")
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


# --- DİZİ ROTALARI ---
@app.route("/add_series", methods=["POST"])
def add_series():
    series_url = request.form["series_url"]
    if config.ALLOWED_DOMAIN not in series_url:
        flash(f"Lütfen geçerli bir {config.ALLOWED_DOMAIN} linki girin.", "warning")
        return redirect(url_for("index"))

    thread = threading.Thread(
        target=services.add_series_to_queue_async, args=(app, series_url)
    )
    thread.daemon = True
    thread.start()

    flash(
        "Dizi ekleme işlemi arka planda başlatıldı. Bölümler kısa süre içinde listelenecektir.",
        "info",
    )
    return redirect(url_for("index"))


@app.route("/series/delete/<int:series_id>", methods=["POST"])
def delete_series(series_id):
    success, message = services.delete_series_record(series_id, active_processes)
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


@app.route("/series/start/<int:series_id>", methods=["POST"])
def start_series_download(series_id):
    success, message = services.start_all_episodes_for_series(series_id)

    # Eğer bölümler başarıyla sıraya eklendiyse...
    if success:
        # Otomatik indirme yöneticisi çalışmıyor olsa bile,
        # kuyruğu bir kez kontrol etmesi için döngüyü manuel olarak tetikleyelim.
        # Bu, anında bir indirme başlatılmasını sağlar.
        try:
            logger.info(f"Kuyruğa ekleme sonrası indirme döngüsü tetikleniyor...")
            services.run_auto_download_cycle(active_processes)
        except Exception as e:
            logger.error(f"İndirme döngüsü tetiklenirken hata: {e}", exc_info=True)

    flash(message, "success" if success else "warning")
    return redirect(url_for("index"))


@app.route("/episode/start/<int:episode_id>", methods=["POST"])
def start_episode_download(episode_id):
    success, message = services.start_download(episode_id, "episode", active_processes)
    flash(message, "info" if success else "warning")
    return redirect(url_for("index"))


@app.route("/episode/stop/<int:episode_id>", methods=["POST"])
def stop_episode_download(episode_id):
    success, message = services.stop_download(episode_id, "episode")
    flash(message, "info" if success else "danger")
    return redirect(url_for("index"))


@app.route("/episode/delete/<int:episode_id>", methods=["POST"])
def delete_episode(episode_id):
    services.delete_record(episode_id, "episode", active_processes)
    flash("Bölüm kaydı başarıyla silindi.", "success")
    return redirect(url_for("index"))


@app.route("/episode/delete_file/<int:episode_id>", methods=["POST"])
def delete_episode_file(episode_id):
    success, message = services.delete_item_file(episode_id, "episode")
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()
    if request.method == "POST":
        settings_updated = False
        update_setting("DOWNLOADS_FOLDER", request.form["downloads_folder"], db)
        update_setting("FILENAME_TEMPLATE", request.form["filename_template"], db)
        update_setting(
            "SERIES_FILENAME_TEMPLATE", request.form["series_filename_template"], db
        )
        update_setting("CONCURRENT_DOWNLOADS", request.form["concurrent_downloads"], db)
        update_setting("SPEED_LIMIT", request.form["speed_limit"], db)
        settings_updated = True

        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if any([current_password, new_password, confirm_password]):
            password_hash = get_setting("ADMIN_PASSWORD_HASH", db)
            if not all([current_password, new_password, confirm_password]):
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
                settings_updated = False

        if settings_updated:
            flash("Ayarlar başarıyla kaydedildi.", "success")
        db.commit()
        return redirect(url_for("settings"))

    current_settings = get_all_settings(db)
    return render_template("settings.html", settings=current_settings)


@app.route("/toggle_auto_download", methods=["POST"])
def toggle_auto_download():
    is_enabled = auto_download_manager_state["enabled"]
    if is_enabled:
        auto_download_manager_state["enabled"] = False
        if auto_download_manager_state["thread"]:
            auto_download_manager_state["thread"].join()  # Thread'in bitmesini bekle
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

    movies_data = services.get_all_movies_status()
    series_data = services.get_all_series_status()

    return jsonify(
        {
            "movies": movies_data,
            "series": series_data,
            "auto_download_enabled": auto_download_manager_state["enabled"],
        }
    )


if __name__ == "__main__":
    with app.app_context():
        setup_database()
        init_settings()
    sync_password_hash_from_env()
    with app.app_context():
        downloads_folder = get_setting("DOWNLOADS_FOLDER")
        if downloads_folder and not os.path.exists(downloads_folder):
            os.makedirs(downloads_folder)
    logger.info("Uygulama başlatılıyor...")
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
