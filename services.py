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


# --- FİLM İŞLEMLERİ ---


def _scrape_movie_from_html(soup):
    logger.info("Film için yedek HTML kazıma yöntemi kullanılıyor.")
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


def scrape_movie_metadata(url):
    try:
        headers = {"User-Agent": config.USER_AGENT}
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        json_ld_script = soup.find("script", type="application/ld+json")
        if json_ld_script:
            data = json.loads(json_ld_script.string)
            movie_data = None
            items_to_search = (
                data.get("@graph", [])
                if isinstance(data, dict)
                else (data if isinstance(data, list) else [data])
            )
            for item in items_to_search:
                item_type = item.get("@type", "")
                if (isinstance(item_type, str) and item_type == "Movie") or (
                    isinstance(item_type, list) and "Movie" in item_type
                ):
                    movie_data = item
                    break
            if movie_data:
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
                    "title": movie_data.get("name", "Başlık Bulunamadı"),
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
            f"JSON-LD ile film verisi bulunamadı, HTML kazımaya geçiliyor. URL: {url}"
        )
        return _scrape_movie_from_html(soup)
    except requests.exceptions.RequestException:
        logger.error(f"Meta veri çekilirken ağ hatası oluştu: {url}", exc_info=True)
        return None
    except Exception:
        logger.error(
            f"Meta veri ayrıştırılırken genel bir hata oluştu: {url}", exc_info=True
        )
        return None


def add_movie_to_queue(url):
    db = get_db()
    if db.execute("SELECT id FROM movies WHERE url = ?", (url,)).fetchone():
        return False, "Bu film zaten kuyrukta mevcut."
    metadata = scrape_movie_metadata(url)
    if not metadata:
        return False, "Film bilgileri çekilemedi."
    db.execute(
        "INSERT INTO movies (url, status, title, year, genre, description, imdb_score, director, cast, poster_url, source_site) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    return True, f'"{metadata["title"]}" başarıyla sıraya eklendi.'


def scrape_movie_links_from_list_page(list_url):
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
            logger.warning(f"Hiç film linki bulunamadı.")
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


def add_movies_from_list_page_async(app, list_url):
    with app.app_context():
        movie_links, error = scrape_movie_links_from_list_page(list_url)
        if error:
            logger.error(f"Toplu ekleme işlemi başarısız: {error}")
            return
        added, skipped, failed = 0, 0, 0
        for link in movie_links:
            try:
                success, message = add_movie_to_queue(link)
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


# --- DİZİ İŞLEMLERİ ---


def scrape_series_data(series_url):
    try:
        logger.info(f"Dizi verisi çekiliyor: {series_url}")
        headers = {"User-Agent": config.USER_AGENT}
        response = requests.get(series_url, headers=headers, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        series_info = {
            "title": soup.select_one("div.data > h1").text.strip(),
            "poster_url": soup.select_one("div.poster > img").get("src"),
            "description": soup.select_one("div#info div.wp-content").text.strip(),
            "source_url": series_url,
            "seasons": [],
        }

        season_blocks = soup.select("div#seasons > div.se-c")
        if not season_blocks:
            logger.warning(f"Dizi için sezon bilgisi bulunamadı. URL: {series_url}")
            return None

        for block in season_blocks:
            season_number_text = block.select_one(".se-q .se-t").text.strip()
            try:
                season_number = int(season_number_text)
            except (ValueError, TypeError):
                logger.warning(f"Geçersiz sezon numarası: {season_number_text}")
                continue

            season_data = {"season_number": season_number, "episodes": []}
            episode_list_items = block.select("ul.episodios > li")
            for item in episode_list_items:
                episode_title_element = item.select_one("h2.episodiotitle > a")
                if not episode_title_element:
                    continue

                episode_url = episode_title_element.get("href")
                episode_title = episode_title_element.contents[0].strip()
                numerando = item.select_one("div.numerando").text.split("-")
                episode_number = int(numerando[1].strip())

                season_data["episodes"].append(
                    {
                        "episode_number": episode_number,
                        "title": episode_title,
                        "url": episode_url,
                    }
                )
            series_info["seasons"].append(season_data)

        logger.info(
            f"'{series_info['title']}' dizisi için {len(series_info['seasons'])} sezon bulundu."
        )
        return series_info
    except requests.exceptions.RequestException as e:
        logger.error(f"Dizi sayfası çekilirken ağ hatası: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Dizi sayfası ayrıştırılırken hata: {e}", exc_info=True)
        return None


def add_series_to_queue(series_url):
    db = get_db()
    series_data = scrape_series_data(series_url)
    if not series_data:
        return (
            False,
            "Dizi bilgileri çekilemedi. Linki kontrol edin veya site yapısı değişmiş olabilir.",
        )

    cursor = db.cursor()
    cursor.execute(
        "SELECT id FROM series WHERE source_url = ?", (series_data["source_url"],)
    )
    series_row = cursor.fetchone()

    if not series_row:
        cursor.execute(
            "INSERT INTO series (title, poster_url, description, source_url) VALUES (?, ?, ?, ?)",
            (
                series_data["title"],
                series_data["poster_url"],
                series_data["description"],
                series_data["source_url"],
            ),
        )
        series_id = cursor.lastrowid
    else:
        series_id = series_row["id"]

    added_count = 0
    for season in series_data["seasons"]:
        cursor.execute(
            "SELECT id FROM seasons WHERE series_id = ? AND season_number = ?",
            (series_id, season["season_number"]),
        )
        season_row = cursor.fetchone()

        if not season_row:
            cursor.execute(
                "INSERT INTO seasons (series_id, season_number) VALUES (?, ?)",
                (series_id, season["season_number"]),
            )
            season_id = cursor.lastrowid
        else:
            season_id = season_row["id"]

        for episode in season["episodes"]:
            res = cursor.execute(
                "INSERT OR IGNORE INTO episodes (season_id, episode_number, title, url) VALUES (?, ?, ?, ?)",
                (
                    season_id,
                    episode["episode_number"],
                    episode["title"],
                    episode["url"],
                ),
            )
            if res.rowcount > 0:
                added_count += 1

    db.commit()
    return (
        True,
        f'"{series_data["title"]}" dizisi için {added_count} yeni bölüm sıraya eklendi.',
    )


def add_series_to_queue_async(app, series_url):
    with app.app_context():
        success, message = add_series_to_queue(series_url)
        if not success:
            logger.error(f"Dizi ekleme hatası ({series_url}): {message}")
        else:
            logger.info(message)


# --- ORTAK İŞLEMLER ---


def start_download(item_id, item_type, active_processes):
    db = get_db()
    table = "movies" if item_type == "movie" else "episodes"

    item = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return False, "Kayıt bulunamadı."
    if item["status"] in ["Kaynak aranıyor...", "İndiriliyor"]:
        return False, "Bu indirme zaten devam ediyor."

    p = Process(target=process_video, args=(item_id, item_type))
    p.start()
    pid = p.pid
    active_processes[pid] = p

    db.execute(
        f"UPDATE {table} SET status = ?, pid = ?, progress = 0, filepath = NULL WHERE id = ?",
        ("Kaynak aranıyor...", pid, item_id),
    )
    db.commit()
    title = item["title"] if item_type == "movie" else f"Bölüm {item['episode_number']}"
    logger.info(f"ID {item_id} ('{title}') için indirme başlatıldı. PID: {pid}")
    return True, f'"{title}" için indirme başlatıldı.'


def stop_download(item_id, item_type):
    db = get_db()
    table = "movies" if item_type == "movie" else "episodes"
    item = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if not (item and item["pid"]):
        return False, "Durdurulacak bir işlem bulunamadı."
    pid = item["pid"]

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
    except OSError as e:
        message = f"İşlem durdurulurken bir hata oluştu: {e}"

    db.execute(
        f"UPDATE {table} SET status = 'Duraklatıldı', pid = NULL WHERE id = ?",
        (item_id,),
    )
    db.commit()
    return True, message


def delete_record(item_id, item_type, active_processes):
    db = get_db()
    table = "movies" if item_type == "movie" else "episodes"
    item = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()

    if item and item["pid"]:
        pid = item["pid"]
        stop_download(item_id, item_type)
        if pid in active_processes:
            del active_processes[pid]

    db.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
    db.commit()
    return True, "Kayıt başarıyla silindi."


def delete_series_record(series_id, active_processes):
    """Bir diziyi, tüm sezonlarını ve bölümlerini veritabanından siler."""
    db = get_db()

    episodes_to_delete = db.execute(
        """
        SELECT e.id, e.pid FROM episodes e
        JOIN seasons s ON e.season_id = s.id
        WHERE s.series_id = ?
    """,
        (series_id,),
    ).fetchall()

    for episode in episodes_to_delete:
        if episode["pid"]:
            pid = episode["pid"]
            stop_download(episode["id"], "episode")
            if pid in active_processes:
                del active_processes[pid]

    series = db.execute(
        "SELECT title FROM series WHERE id = ?", (series_id,)
    ).fetchone()
    if series:
        cursor = db.cursor()
        cursor.execute("DELETE FROM series WHERE id = ?", (series_id,))
        db.commit()
        logger.info(f"'{series['title']}' dizisi ve tüm bölümleri başarıyla silindi.")
        return True, f"'{series['title']}' dizisi başarıyla silindi."
    else:
        return False, "Silinecek dizi bulunamadı."


def start_all_episodes_for_series(series_id):
    """Bir diziye ait indirilebilir durumdaki tüm bölümleri 'Sırada' olarak işaretler."""
    db = get_db()

    episodes_to_queue = db.execute(
        """
        SELECT e.id FROM episodes e
        JOIN seasons s ON e.season_id = s.id
        WHERE s.series_id = ? AND e.status NOT IN ('Tamamlandı', 'İndiriliyor', 'Kaynak aranıyor...')
    """,
        (series_id,),
    ).fetchall()

    if not episodes_to_queue:
        return False, "Sıraya eklenecek yeni bölüm bulunamadı."

    count = 0
    for episode in episodes_to_queue:
        db.execute(
            "UPDATE episodes SET status = 'Sırada' WHERE id = ?", (episode["id"],)
        )
        count += 1

    db.commit()

    series_title = db.execute(
        "SELECT title FROM series WHERE id = ?", (series_id,)
    ).fetchone()["title"]

    logger.info(f"'{series_title}' dizisi için {count} bölüm indirme sırasına alındı.")
    return True, f"'{series_title}' dizisi için {count} bölüm indirme sırasına alındı."


def delete_item_file(item_id, item_type):
    """Bir film veya bölüm dosyasını diskten siler."""
    db = get_db()
    table = "movies" if item_type == "movie" else "episodes"

    item = db.execute(f"SELECT * FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if not item:
        return False, "Kayıt bulunamadı."

    filepath = item["filepath"]
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
            db.execute(f"UPDATE {table} SET filepath = NULL WHERE id = ?", (item_id,))
            db.commit()
            logger.info(f"Dosya diskten silindi: {filepath}")
            return True, f'"{os.path.basename(filepath)}" diskten başarıyla silindi.'
        except OSError:
            logger.error(f"Dosya silinemedi: {filepath}", exc_info=True)
            return False, "Dosya silinirken bir hata oluştu."
    else:
        db.execute(f"UPDATE {table} SET filepath = NULL WHERE id = ?", (item_id,))
        db.commit()
        return False, "Silinecek dosya bulunamadı veya zaten silinmiş."


# --- OTOMATİK İNDİRME YÖNETİCİSİ ---
def run_auto_download_cycle(active_processes):
    """Sıradaki filmleri ve bölümleri otomatik olarak indirmeye başlar."""
    db = get_db()
    try:
        concurrent_limit = int(get_setting("CONCURRENT_DOWNLOADS", db))
    except (ValueError, TypeError):
        concurrent_limit = 1

    # Ölü prosesleri temizle
    for pid, process in list(active_processes.items()):
        if not process.is_alive():
            del active_processes[pid]
            logger.info(
                f"Otomatik yönetici: Tamamlanmış proses (PID: {pid}) temizlendi."
            )

    # Mevcut indirme slotlarını doldurana kadar döngüye gir
    while len(active_processes) < concurrent_limit:
        next_item = db.execute("""
            SELECT id, 'movie' as type, created_at FROM movies WHERE status = 'Sırada'
            UNION ALL
            SELECT id, 'episode' as type, created_at FROM episodes WHERE status = 'Sırada'
            ORDER BY created_at ASC
            LIMIT 1
        """).fetchone()

        if not next_item:
            break  # Sırada başka öğe yoksa döngüden çık

        item_id = next_item["id"]
        item_type = next_item["type"]
        logger.info(
            f"[Auto-Download] Sırada bekleyen bulundu ({item_type} ID: {item_id}). İndirme başlatılıyor."
        )
        start_download(item_id, item_type, active_processes)


# --- API İÇİN VERİ ÇEKME FONKSİYONLARI ---


def get_all_movies_status():
    db = get_db()
    movies = db.execute("SELECT * FROM movies ORDER BY created_at DESC").fetchall()
    return {m["id"]: dict(m) for m in movies}


def get_all_series_status():
    db = get_db()
    series_list = db.execute("SELECT * FROM series ORDER BY title ASC").fetchall()

    series_data = []
    for s in series_list:
        series_dict = dict(s)
        seasons = db.execute(
            "SELECT * FROM seasons WHERE series_id = ? ORDER BY season_number ASC",
            (s["id"],),
        ).fetchall()

        series_dict["seasons"] = []
        for season in seasons:
            season_dict = dict(season)
            episodes = db.execute(
                "SELECT * FROM episodes WHERE season_id = ? ORDER BY episode_number ASC",
                (season["id"],),
            ).fetchall()
            season_dict["episodes"] = [dict(ep) for ep in episodes]
            series_dict["seasons"].append(season_dict)
        series_data.append(series_dict)

    return series_data
