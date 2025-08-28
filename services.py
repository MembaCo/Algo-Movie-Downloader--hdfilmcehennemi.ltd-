# @author: MembaCo.

import logging
import json
import os
import signal
import subprocess
import sys
import threading
from multiprocessing import Process
import requests
from bs4 import BeautifulSoup

import config
from database import get_db, get_setting
from worker import process_video

logger = logging.getLogger(__name__)


def _scrape_from_html(soup):
    """
    JSON-LD başarısız olduğunda doğrudan HTML'den veri kazımak için yedek fonksiyon.
    """
    logger.info("Yedek HTML kazıma yöntemi kullanılıyor.")
    try:
        metadata = {
            "title": "Başlık Bulunamadı",
            "description": "Özet bulunamadı.",
            "year": "Bilinmiyor",
            "genre": "Bilinmiyor",
            "imdb_score": "Bilinmiyor",
            "director": "Bilinmiyor",
            "cast": "Bilinmiyor",
            "poster_url": "",
        }
        header = soup.find("div", class_="sheader")
        if header:
            title_tag = header.find("h1")
            if title_tag:
                metadata["title"] = title_tag.text.strip()
            poster_div = header.find("div", class_="poster")
            if poster_div and poster_div.find("img"):
                metadata["poster_url"] = poster_div.find("img").get("src", "")
        content_div = soup.find("div", class_="wp-content")
        if content_div and content_div.find("p"):
            metadata["description"] = content_div.find("p").text.strip()
        custom_fields = soup.find_all("div", class_="custom_fields")
        for field in custom_fields:
            label_tag = field.find("b", class_="variante")
            value_tag = field.find("span", class_="valor")
            if label_tag and value_tag:
                label = label_tag.text.strip().lower()
                value = value_tag.text.strip()
                if "imdb puanı" in label:
                    imdb_strong_tag = value_tag.find("strong")
                    metadata["imdb_score"] = (
                        imdb_strong_tag.text.strip() if imdb_strong_tag else value
                    )
                elif "yönetmen" in label:
                    metadata["director"] = value
                elif "oyuncular" in label:
                    metadata["cast"] = value
        if header:
            year_tag = header.find("span", class_="C")
            if year_tag and year_tag.find("a"):
                metadata["year"] = year_tag.find("a").text.strip()
            genre_div = header.find("div", class_="sgeneros")
            if genre_div:
                genres = [a.text.strip() for a in genre_div.find_all("a")]
                metadata["genre"] = ", ".join(genres)
        if "HDFilmcehennemi" in metadata["title"]:
            logger.warning("HTML kazıma jenerik bir başlık buldu, veri geçersiz.")
            return None
        return metadata
    except Exception:
        logger.error("Yedek HTML kazıma yöntemi sırasında hata oluştu.", exc_info=True)
        return None

 
def scrape_metadata(url):
    """
    BeautifulSoup ile bir URL'den detaylı film bilgilerini çeker.
    """
    try:
        headers = {"User-Agent": config.USER_AGENT}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        json_ld_script = soup.find("script", type="application/ld+json")
        if json_ld_script:
            logger.info(f"JSON-LD verisi bulundu. URL: {url}")
            data = json.loads(json_ld_script.string)
            movie_data = None

            def find_movie_in_json(item):
                if not isinstance(item, dict):
                    return None
                item_type = item.get("@type")
                if (isinstance(item_type, str) and item_type == "Movie") or (
                    isinstance(item_type, list) and "Movie" in item_type
                ):
                    return item
                return None

            items_to_search = []
            if (
                isinstance(data, dict)
                and "@graph" in data
                and isinstance(data.get("@graph"), list)
            ):
                items_to_search = data["@graph"]
            elif isinstance(data, list):
                items_to_search = data
            elif isinstance(data, dict):
                items_to_search = [data]

            for item in items_to_search:
                movie_data = find_movie_in_json(item)
                if movie_data:
                    break

            if movie_data:
                title = movie_data.get("name", "Başlık Bulunamadı")
                rating = movie_data.get("aggregateRating", {})
                actors = ", ".join(
                    [
                        actor["name"]
                        for actor in movie_data.get("actor", [])
                        if "name" in actor
                    ]
                )
                director_data = movie_data.get("director")
                director = "Bilinmiyor"
                if isinstance(director_data, list):
                    director = ", ".join(
                        [d["name"] for d in director_data if "name" in d]
                    )
                elif isinstance(director_data, dict):
                    director = director_data.get("name", "Bilinmiyor")

                metadata = {
                    "title": title,
                    "description": movie_data.get("description", "Özet bulunamadı."),
                    "year": movie_data.get("datePublished", "Bilinmiyor")[:4],
                    "genre": ", ".join(movie_data.get("genre", []))
                    if isinstance(movie_data.get("genre"), list)
                    else movie_data.get("genre", "Bilinmiyor"),
                    "imdb_score": rating.get("ratingValue", "Bilinmiyor"),
                    "director": director,
                    "cast": actors,
                    "poster_url": movie_data.get("image", ""),
                }
                logger.info("JSON-LD ile film verisi başarıyla çekildi.")
                return metadata

        logger.warning(
            f"JSON-LD ile geçerli veri bulunamadı, HTML kazıma yöntemine geçiliyor. URL: {url}"
        )
        return _scrape_from_html(soup)
    except requests.exceptions.RequestException:
        logger.error(f"Meta veri çekilirken ağ hatası oluştu: {url}", exc_info=True)
        return None
    except Exception:
        logger.error(
            f"Meta veri ayrıştırılırken genel bir hata oluştu: {url}", exc_info=True
        )
        return None


def add_video_to_queue(url):
    """Tek bir video URL'sini veritabanına ekler."""
    db = get_db()
    if db.execute("SELECT id FROM videos WHERE url = ?", (url,)).fetchone():
        logger.warning(f"Video zaten kuyrukta mevcut, atlanıyor: {url}")
        return False, "Bu video zaten kuyrukta mevcut."

    logger.info(f"Yeni video için meta veri çekiliyor: {url}")
    metadata = scrape_metadata(url)
    if not metadata:
        logger.error(f"Meta veri çekilemedi, video eklenemedi: {url}")
        return (
            False,
            "Film bilgileri çekilemedi. Linki kontrol edin veya site yapısı değişmiş olabilir.",
        )

    db.execute(
        "INSERT INTO videos (url, status, title, year, genre, description, imdb_score, director, cast, poster_url, source_site) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            url,
            "Sırada",
            metadata["title"],
            metadata["year"],
            metadata["genre"],
            metadata["description"],
            metadata["imdb_score"],
            metadata["director"],
            metadata["cast"],
            metadata["poster_url"],
            "hdfilmcehennemi",
        ),
    )
    db.commit()
    logger.info(f"'{metadata['title']}' adlı film sıraya eklendi.")
    return True, f'"{metadata["title"]}" başarıyla sıraya eklendi.'


def scrape_movie_links_from_list_page(list_url):
    """Bir liste sayfasındaki tüm film linklerini kazır."""
    logger.info(f"Toplu liste sayfasından film linkleri çekiliyor: {list_url}")
    try:
        headers = {"User-Agent": config.USER_AGENT}
        response = requests.get(list_url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        movie_links = []
        for movie_card in soup.find_all("article", class_="item"):
            link_tag = movie_card.find("a")
            if link_tag and link_tag.get("href"):
                movie_links.append(link_tag["href"])

        if not movie_links:
            logger.warning(f"Hiç film linki bulunamadı. Site yapısı değişmiş olabilir.")

        logger.info(f"{len(movie_links)} adet film linki bulundu.")
        return movie_links, None
    except requests.exceptions.RequestException as e:
        error_message = f"Liste sayfası çekilirken ağ hatası oluştu: {list_url}"
        logger.error(error_message, exc_info=True)
        return [], error_message
    except Exception as e:
        error_message = (
            f"Liste sayfası ayrıştırılırken genel bir hata oluştu: {list_url}"
        )
        logger.error(error_message, exc_info=True)
        return [], error_message


def add_videos_from_list_page_async(app, list_url):
    """Arka planda bir liste sayfasındaki tüm filmleri kuyruğa ekler."""
    with app.app_context():
        movie_links, error = scrape_movie_links_from_list_page(list_url)
        if error:
            logger.error(f"Toplu ekleme işlemi başarısız: {error}")
            return

        added, skipped, failed = 0, 0, 0
        for link in movie_links:
            try:
                success, message = add_video_to_queue(link)
                if success:
                    added += 1
                elif "zaten kuyrukta mevcut" in message.lower():
                    skipped += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
                logger.error(
                    f"Toplu ekleme sırasında bir video ({link}) işlenirken hata oluştu.",
                    exc_info=True,
                )

        logger.info(
            f"Toplu ekleme tamamlandı. Eklenen: {added}, Atlanan: {skipped}, Başarısız: {failed}"
        )


def start_download_for_video(video_id):
    """Bir indirme işlemini başlatır."""
    db = get_db()
    video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not video:
        return False, "Video bulunamadı."
    if video["status"] in ["Kaynak aranıyor...", "İndiriliyor"]:
        return False, "Bu indirme zaten devam ediyor."

    p = Process(target=process_video, args=(video_id,))
    p.start()
    pid = p.pid

    db.execute(
        "UPDATE videos SET status = ?, pid = ?, progress = 0, filepath = NULL WHERE id = ?",
        ("Kaynak aranıyor...", pid, video_id),
    )
    db.commit()
    logger.info(
        f"ID {video_id} ('{video['title']}') için indirme başlatıldı. PID: {pid}"
    )
    return True, f'"{video["title"]}" için indirme başlatıldı.'


def stop_download_for_video(video_id):
    """Bir indirme işlemini durdurur."""
    db = get_db()
    video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not (video and video["pid"]):
        return False, "Durdurulacak bir işlem bulunamadı."

    pid = video["pid"]
    logger.info(f"ID {video_id} ('{video['title']}') için durdurma isteği. PID: {pid}")
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                check=True,
                capture_output=True,
            )
        message = "İndirme durdurma isteği gönderildi."
    except (ProcessLookupError, subprocess.CalledProcessError):
        message = "İşlem zaten sonlanmış."
        logger.warning(f"Durdurulmaya çalışılan işlem (PID: {pid}) bulunamadı.")
    except OSError as e:
        message = f"İşlem durdurulurken bir hata oluştu: {e}"
        logger.error(f"İşlem durdurulurken hata oluştu (PID: {pid})", exc_info=True)

    db.execute(
        "UPDATE videos SET status = 'Duraklatıldı', pid = NULL WHERE id = ?",
        (video_id,),
    )
    db.commit()
    return True, message


def delete_video_record(video_id):
    """Bir videonun veritabanı kaydını siler."""
    db = get_db()
    video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if video and video["pid"]:
        stop_download_for_video(video_id)

    title_to_log = video["title"] if video else "Bilinmeyen"
    db.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    db.commit()
    logger.info(f"ID {video_id} ('{title_to_log}') veritabanından silindi.")
    return True, "Video kaydı başarıyla silindi."


def delete_video_file(video_id):
    """İndirilmiş bir video dosyasını diskten siler."""
    db = get_db()
    video = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not video:
        return False, "Video kaydı bulunamadı."

    filepath = video["filepath"]
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            db.execute("UPDATE videos SET filepath = NULL WHERE id = ?", (video_id,))
            db.commit()
            logger.info(f"Dosya diskten silindi: {filepath}")
            return True, f'"{os.path.basename(filepath)}" diskten başarıyla silindi.'
        except OSError:
            logger.error(f"Dosya silinemedi: {filepath}", exc_info=True)
            return False, "Dosya silinirken bir hata oluştu."
    else:
        db.execute("UPDATE videos SET filepath = NULL WHERE id = ?", (video_id,))
        db.commit()
        return False, "Silinecek dosya bulunamadı veya zaten silinmiş."


def get_all_videos_status():
    """Tüm videoların durum bilgilerini API için döndürür."""
    db = get_db()
    videos = db.execute(
        "SELECT id, status, progress, filepath, poster_url FROM videos"
    ).fetchall()
    return {v["id"]: dict(v) for v in videos}


def run_auto_download_cycle():
    """Otomatik indirme yöneticisinin bir döngüsünü çalıştırır."""
    db = get_db()
    try:
        concurrent_limit = int(get_setting("CONCURRENT_DOWNLOADS", db))
    except (ValueError, TypeError):
        concurrent_limit = 1

    active_downloads = db.execute(
        "SELECT COUNT(id) FROM videos WHERE status = 'İndiriliyor' OR status = 'Kaynak aranıyor...'"
    ).fetchone()[0]

    if active_downloads < concurrent_limit:
        next_video = db.execute(
            "SELECT id FROM videos WHERE status = 'Sırada' ORDER BY created_at ASC"
        ).fetchone()
        if next_video:
            logger.info(
                f"[Auto-Download] Sırada bekleyen video bulundu (ID: {next_video['id']}). İndirme başlatılıyor."
            )
            start_download_for_video(next_video["id"])
