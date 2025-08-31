# @author: MembaCo.

import sqlite3
import time
import subprocess
import re
import os
import sys
import logging
import glob
from seleniumwire import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

import config
from logging_config import setup_logging
from database import (
    get_all_settings as get_all_settings_from_db,
)  # İsim çakışmasını önlemek için yeniden adlandır

logger = logging.getLogger(__name__)


# --- YENİ: Worker-içi Veritabanı Güncelleme Fonksiyonu ---
def _update_status_worker(
    conn, video_id, status=None, source_url=None, progress=None, filepath=None
):
    """
    Mevcut bir veritabanı bağlantısını kullanarak videonun durumunu günceller.
    """
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "UPDATE videos SET status = ? WHERE id = ?", (status, video_id)
            )
        if source_url:
            cursor.execute(
                "UPDATE videos SET source_url = ? WHERE id = ?", (source_url, video_id)
            )
        if progress is not None:
            cursor.execute(
                "UPDATE videos SET progress = ? WHERE id = ?", (progress, video_id)
            )
        if filepath is not None:
            cursor.execute(
                "UPDATE videos SET filepath = ? WHERE id = ?", (filepath, video_id)
            )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(
            f"ID {video_id} için worker DB güncellemesinde hata: {e}",
            exc_info=True,
        )


def find_manifest_url(target_url):
    """Selenium ile manifest URL'sini, gerekli headerları ve çerezleri bulur."""
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--log-level=3")
    options.add_argument("--mute-audio")
    options.add_argument(f"user-agent={config.USER_AGENT}")

    driver = None
    try:
        service = Service()
        driver = webdriver.Chrome(service=service, options=options)

        wait = WebDriverWait(driver, 30)
        driver.get(target_url)
        play_button_main = wait.until(EC.element_to_be_clickable((By.ID, "fimcnt")))
        driver.execute_script("arguments[0].click();", play_button_main)

        iframe_locator = (By.CSS_SELECTOR, ".play-box-iframe iframe")
        wait.until(EC.frame_to_be_available_and_switch_to_it(iframe_locator))
        iframe_src = driver.current_url

        play_button_iframe = wait.until(EC.element_to_be_clickable((By.ID, "player")))
        del driver.requests
        driver.execute_script("arguments[0].click();", play_button_iframe)

        request = driver.wait_for_request(
            r".*(" + "|".join(re.escape(k) for k in config.VIDEO_KEYWORDS) + r").*",
            timeout=20,
        )
        logger.info(f"Manifest URL'si bulundu: {request.url}")
        headers = dict(request.headers)
        cookies = driver.get_cookies()
        return request.url, iframe_src, headers, cookies
    except TimeoutException:
        logger.warning(
            f"Manifest URL'si beklenirken zaman aşımına uğradı. URL: {target_url}"
        )
        return None, None, None, None
    finally:
        if driver:
            driver.quit()


# --- GÜNCELLENDİ: Akıllı Hata Yönetimi ---
def download_with_yt_dlp(
    conn,
    video_id,
    manifest_url,
    headers,
    cookie_filepath,
    download_folder,
    filename_template,
    speed_limit,
):
    """yt-dlp ile videoyu indirir ve ilerlemeyi veritabanına yazar."""
    output_template = os.path.join(download_folder, f"{filename_template}.%(ext)s")
    command = [
        "yt-dlp",
        "--cookies",
        cookie_filepath,
        "--newline",
        "--no-check-certificates",
        "--no-color",
        "--progress",
        "--verbose",
        "--hls-use-mpegts",
        "-o",
        output_template,
    ]
    if speed_limit:
        command.extend(["--limit-rate", speed_limit])
    for key, value in headers.items():
        command.extend(["--add-header", f"{key}: {value}"])
    command.append(manifest_url)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid if sys.platform != "win32" else None,
    )
    full_output = ""
    for line_bytes in iter(process.stdout.readline, b""):
        line = line_bytes.decode("utf-8", errors="ignore")
        full_output += line
        progress_match = re.search(r"\[download\]\s+([0-9\.]+)%", line)
        if progress_match:
            try:
                progress = float(progress_match.group(1))
                _update_status_worker(conn, video_id, progress=progress)
            except (ValueError, IndexError):
                continue
    process.wait()

    if process.returncode == 0:
        return True, "İndirme tamamlandı."
    else:
        # Akıllı hata analizi
        if "403 Forbidden" in full_output:
            error_message = (
                "Hata: Sunucu erişimi reddetti (403). Çerezler geçersiz olabilir."
            )
        elif "No space left on device" in full_output:
            error_message = "Hata: Diskte yeterli alan yok."
        elif "HTTP Error 404" in full_output:
            error_message = "Hata: Video kaynağı bulunamadı (404)."
        else:
            last_lines = "\n".join(full_output.strip().split("\n")[-5:])
            error_message = f"Hata: İndirme başarısız oldu. Detay: ...{last_lines}"

        return False, error_message


# --- GÜNCELLENDİ: Ana Worker Fonksiyonu (Tek Veritabanı Bağlantısı) ---
def process_video(video_id):
    """Tek bir video için tüm bulma ve indirme sürecini yönetir."""
    global logger
    logger = setup_logging()  # Her proseste loglamayı yeniden kur

    conn = None
    cookie_filepath = f"cookies_{video_id}.txt"
    try:
        # Fonksiyon başında tek bir veritabanı bağlantısı aç
        conn = sqlite3.connect(config.DATABASE)
        conn.row_factory = sqlite3.Row

        video = conn.execute(
            "SELECT * FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        # Ayarları alırken db.py'daki fonksiyonu kullan, bu da yeni bir bağlantı açabilir.
        # Bu yüzden worker için özel bir get_settings yazmak daha iyi olabilir ya da
        # mevcut bağlantıyı ona geçebiliriz. Şimdilik bu şekilde bırakıyoruz.
        settings = get_all_settings_from_db(conn)

        if not video or not settings:
            logger.error(f"Veritabanında video ID {video_id} veya ayarlar bulunamadı.")
            return

        download_folder = settings.get("DOWNLOADS_FOLDER", "downloads")
        if not os.path.exists(download_folder):
            os.makedirs(download_folder)

        logger.info(f"ID {video_id}: Kaynak adresi aranıyor... URL: {video['url']}")
        manifest_url, iframe_src, headers, cookies = find_manifest_url(video["url"])

        if manifest_url:
            with open(cookie_filepath, "w", encoding="utf-8") as f:
                f.write("# Netscape HTTP Cookie File\n")
                for cookie in cookies:
                    if "name" not in cookie or "value" not in cookie:
                        continue
                    f.write(
                        f"{cookie.get('domain', '')}\t{'TRUE' if cookie.get('domain', '').startswith('.') else 'FALSE'}\t{cookie.get('path', '/')}\t{'TRUE' if cookie.get('secure') else 'FALSE'}\t{int(cookie.get('expiry', 0))}\t{cookie['name']}\t{cookie['value']}\n"
                    )

            _update_status_worker(
                conn, video_id, status="İndiriliyor", source_url=manifest_url
            )

            # Dosya adı şablonu
            filename_template_str = settings.get("FILENAME_TEMPLATE", "{title}")
            try:
                filename_base = filename_template_str.format(
                    title=video["title"] or "Bilinmeyen",
                    year=video["year"] or "YYYY",
                    genre=video["genre"] or "Tur",
                    imdb_score=video["imdb_score"] or "N/A",
                    director=video["director"] or "Yonetmen",
                )
            except KeyError as e:
                logger.warning(
                    f"Dosya adı şablonunda geçersiz değişken: {e}. Varsayılan kullanılıyor."
                )
                filename_base = video["title"] or "Bilinmeyen Film"

            # Güvenli dosya adı
            def to_ascii_safe(text):
                text = (
                    str(text)
                    .replace("ı", "i")
                    .replace("İ", "I")
                    .replace("ğ", "g")
                    .replace("Ğ", "G")
                    .replace("ü", "u")
                    .replace("Ü", "U")
                    .replace("ş", "s")
                    .replace("Ş", "S")
                    .replace("ö", "o")
                    .replace("Ö", "O")
                    .replace("ç", "c")
                    .replace("Ç", "C")
                )
                text = re.sub(r"[^\x00-\x7F]+", "", text)
                text = re.sub(r'[<>:"/\\|?*]', "", text).strip()
                return text

            safe_filename_base = to_ascii_safe(filename_base)

            logger.info(
                f"ID {video_id}: İndirme başlıyor. Dosya adı: {safe_filename_base}"
            )

            success, message = download_with_yt_dlp(
                conn,
                video_id,
                manifest_url,
                headers,
                cookie_filepath,
                download_folder,
                safe_filename_base,
                settings.get("SPEED_LIMIT"),
            )

            if success:
                search_pattern = os.path.join(
                    download_folder, f"{safe_filename_base}.*"
                )
                files = glob.glob(search_pattern)
                if files:
                    final_filepath = files[0]
                    _update_status_worker(
                        conn,
                        video_id,
                        status="Tamamlandı",
                        progress=100,
                        filepath=final_filepath,
                    )
                    logger.info(
                        f"ID {video_id}: İndirme başarıyla tamamlandı. Dosya: {final_filepath}"
                    )
                else:
                    _update_status_worker(
                        conn,
                        video_id,
                        status="Hata: İndirilen dosya bulunamadı",
                        progress=100,
                    )
                    logger.error(
                        f"ID {video_id}: Dosya bulunamadı. Aranan: {search_pattern}"
                    )
            else:
                # message, download_with_yt_dlp'den gelen akıllı hata mesajıdır.
                _update_status_worker(conn, video_id, status=message)
                logger.error(f"ID {video_id}: İndirme hatası - {message}")
        else:
            _update_status_worker(
                conn, video_id, status="Hata: Video kaynağı bulunamadı"
            )
            logger.warning(f"ID {video_id}: Manifest URL bulunamadı.")

    except Exception as e:
        logger.exception(
            f"ID {video_id}: process_video içinde beklenmedik bir genel hata oluştu: {e}"
        )
        if conn:  # Hata durumunda da durumu güncellemeye çalış
            _update_status_worker(
                conn, video_id, status="Hata: Beklenmedik Sistem Hatası"
            )
    finally:
        # Her durumda (başarı veya hata) bağlantıyı kapat ve çerez dosyasını sil
        if conn:
            conn.close()
        if os.path.exists(cookie_filepath):
            os.remove(cookie_filepath)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        video_id_arg = int(sys.argv[1])
        process_video(video_id_arg)
    else:
        setup_logging()
        logger.warning(
            "Bu script, app.py tarafından bir video ID'si ile çağrılmalıdır."
        )
