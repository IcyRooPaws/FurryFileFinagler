# fff: implemented in media sorter/_handlers/utils.py.
# fff: Config is fff.sorter.config.Config (the two near-identical classes are merged).
# fff: the file-type/rating helpers, path normalization, timestamps and the *_with_logging
# fff: file operations were copied verbatim to fff.common in Phase 1 and are imimplemented in there.
# fff: hash_file uses fff.hashing (media sorter's media_hasher.py, sorter profile) instead of
# fff: loading media_hasher.py by file path.
"""
Utility functions and configuration management for media sorter.

This module provides core utilities including configuration management,
logging setup, file type detection, and hashing functions.
"""
import os
import yaml  # noqa: F401
import datetime
import logging
import hashlib
import time

from fff.hashing.media_hasher import hash_file as media_hash_file
from fff.sorter.config import Config  # noqa: F401
from fff.sorter import paths as sorter_paths
from fff.common.fileops import (  # noqa: F401
    ensure_directory_exists,
    normalize_path,
    log_file_move,
    move_file_with_logging,
    _find_table_for_path,
    copy_file_with_logging,
    delete_file_with_logging,
)
from fff.common.naming import (  # noqa: F401
    is_configured_file,
    is_video,
    is_gif,
    is_image,
    is_explicit,
    is_adult,
    is_questionable,
    is_mature,
    is_safe,
    is_general,
    is_explicit_or_adult,
    is_questionable_or_mature,
    is_sfw_or_general,
    is_furaffinity_file,
    get_furaffinity_rating,
    extract_e621_post_id,
)
from fff.common.timestamps import set_file_timestamps  # noqa: F401


def setup_logging(log_directory=str(sorter_paths.LOG_DIR), config=None):
    """Setup logging configuration with UTF-8 encoding support."""
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_directory, f"log_{timestamp}.log")

    # Get log level from config or default to WARNING
    log_level = logging.WARNING
    if config and hasattr(config, 'log_level'):
        log_level = config.log_level

    # Create file handler with UTF-8 encoding to handle Unicode characters
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    # Create console handler with UTF-8 encoding
    import sys
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[file_handler, console_handler]
    )

    logger = logging.getLogger(__name__)
    logger.info("Logging setup complete with UTF-8 encoding.")
    return logger, log_filename


def hash_file(file_path, chunk_size=8192, logger=None):
    """
    Return hash for file_path using the new MediaHasher library.

    Uses type-specific hashing:
    - Perceptual hashing for images (visual similarity detection)
    - Content-based hashing for audio files
    - Smart sampling for other file types

    Logs progress for large files.
    """
    try:
        # Use the new MediaHasher library
        file_hash = media_hash_file(file_path)

        if file_hash is None:
            # Fallback to MD5 if MediaHasher fails
            if logger:
                logger.warning(f"MediaHasher failed for {file_path}, falling back to MD5")
            return _fallback_md5_hash(file_path, chunk_size, logger)

        return file_hash

    except Exception as e:
        if logger:
            logger.warning(f"MediaHasher error for {file_path}: {e}, falling back to MD5")
        # Fallback to MD5 if MediaHasher fails
        return _fallback_md5_hash(file_path, chunk_size, logger)


def _fallback_md5_hash(file_path, chunk_size=8192, logger=None):
    """Fallback MD5 hash function for when MediaHasher fails."""
    hash_md5 = hashlib.md5()
    try:
        file_size = os.path.getsize(file_path)
        LARGE_FILE_THRESHOLD = 100 * 1024 * 1024  # 100 MB
        is_large = file_size > LARGE_FILE_THRESHOLD
        start_time = time.time()
        last_log_time = start_time
        processed_bytes = 0

        with open(file_path, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                hash_md5.update(data)
                processed_bytes += len(data)
                if is_large and logger:
                    now = time.time()
                    if now - last_log_time > 5:
                        elapsed = now - start_time
                        percent = processed_bytes / file_size * 100 if file_size else 0
                        speed = processed_bytes / elapsed if elapsed > 0 else 0
                        remaining = (file_size - processed_bytes) / speed if speed > 0 else 0
                        logger.info(
                            f"Hashing large file '{file_path}': {percent:.1f}% done, "
                            f"{processed_bytes // (1024*1024)}MB/{file_size // (1024*1024)}MB, "
                            f"elapsed {elapsed:.1f}s, est. {remaining:.1f}s left"
                        )
                        last_log_time = now
        if is_large and logger:
            total_time = time.time() - start_time
            logger.info(f"Finished hashing large file '{file_path}' in {total_time:.1f}s")
        return hash_md5.hexdigest()
    except Exception as e:
        if logger:
            logger.error("Error hashing file %s: %s", file_path, e)
        return None


def compute_md5_hash(file_path, chunk_size=8192, logger=None):
    """
    Compute pure MD5 hash of a file.

    This function computes a traditional MD5 hash of the entire file content,
    separate from MediaHasher's content-based hashing. Use this for:
    - Traditional file deduplication
    - Exact file matching
    - File integrity verification

    Args:
        file_path: Path to the file to hash
        chunk_size: Size of chunks to read (default 8KB)
        logger: Optional logger for progress tracking on large files

    Returns:
        str: 32-character hexadecimal MD5 hash, or None on error
    """
    return _fallback_md5_hash(file_path, chunk_size, logger)
