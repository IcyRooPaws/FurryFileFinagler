# fff: implemented in e621 archive/modules/logging_setup.py.
import os
import logging
from datetime import datetime

def ensure_download_folder(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)

def setup_logging(log_dir):
    ensure_download_folder(log_dir)
    log_file_name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    log_file = os.path.join(log_dir, log_file_name)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )
    logging.info("Logging initialized. Log file: %s", log_file)
