# @author: MembaCo.

import logging
from logging.handlers import RotatingFileHandler
import sys
import os

# config.py'dan DATA_DIR'ı almak için import ediyoruz.
import config

# Log dosyasının tam yolunu belirliyoruz.
LOG_FILE = os.path.join(config.DATA_DIR, "app.log")

 
def setup_logging():
    """Uygulama genelinde kullanılacak merkezi loglama yapılandırmasını kurar."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(process)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    # Log dosyasının yazılacağı klasörün var olduğundan emin ol
    log_dir = os.path.dirname(LOG_FILE)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1024 * 1024 * 5, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(stdout_handler)
    logger.addHandler(file_handler)
    return logger
