import os
import logging
from datetime import datetime

FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_file_handler = None


def ensure_download_folder(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)

def setup_logging(log_dir):
    """Log INFO and above to a new ``log_dir/<stamp>.log`` (and the console).

    When logging is already configured (a tool opened inside the Manager), basicConfig would
    do nothing and leave the new log file empty, so the file handler is attached directly.
    It replaces the handler from an earlier call, so tools opened one after another each log
    to their own file only.
    """
    global _file_handler
    ensure_download_folder(log_dir)
    log_file_name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    log_file = os.path.join(log_dir, log_file_name)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format=FORMAT, handlers=[logging.StreamHandler()])
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        _file_handler.close()
    _file_handler = logging.FileHandler(log_file, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter(FORMAT))
    _file_handler.setLevel(logging.INFO)
    root.addHandler(_file_handler)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
    logging.info("Logging initialized. Log file: %s", log_file)
