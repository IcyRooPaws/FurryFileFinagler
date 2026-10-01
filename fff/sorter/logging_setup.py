# fff: default log folder is logs/sorter (was ./Logs).
"""
Logging configuration for FFF.

Provides centralized logging setup with consistent formatting and file management.
"""
import os
import logging
import datetime
from typing import Tuple

from fff.sorter import paths as sorter_paths


def setup_logging(log_directory: str = str(sorter_paths.LOG_DIR), 
                  console: bool = True, 
                  level: int = logging.WARNING) -> Tuple[logging.Logger, str]:
    """
    Setup logging configuration.
    
    Args:
        log_directory: Directory to store log files
        console: Whether to also log to console
        level: Logging level (default: WARNING for less verbose logs)
        
    Returns:
        Tuple of (logger, log_filename)
    """
    # Ensure log directory exists
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    # Create timestamped log filename
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = os.path.join(log_directory, f"log_{timestamp}.log")
    
    # Setup handlers
    handlers = [logging.FileHandler(log_filename, encoding='utf-8')]
    if console:
        handlers.append(logging.StreamHandler())

    # Configure logging
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True  # Override any existing configuration
    )
    
    logger = logging.getLogger('fff.sorter')
    logger.info(f"Logging initialized. Log file: {log_filename}")
    
    return logger, log_filename


def get_logger(name: str = 'fff.sorter') -> logging.Logger:
    """Get a logger instance with the specified name."""
    return logging.getLogger(name)


def cleanup_old_logs(log_directory: str = str(sorter_paths.LOG_DIR), days: int = 30) -> int:
    """
    Clean up log files older than specified days.
    
    Args:
        log_directory: Directory containing log files
        days: Remove logs older than this many days
        
    Returns:
        Number of files deleted
    """
    if not os.path.exists(log_directory):
        return 0
    
    logger = get_logger()
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=days)
    deleted_count = 0

    try:
        for file in os.listdir(log_directory):
            if file.startswith('log_') and file.endswith('.log'):
                file_path = os.path.join(log_directory, file)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))

                if mtime < cutoff_date:
                    os.remove(file_path)
                    deleted_count += 1
                    logger.info(f"Deleted old log file: {file}")

        logger.info(f"Log cleanup completed. Deleted {deleted_count} old log files.")
        return deleted_count

    except Exception as e:
        logger.error(f"Error during log cleanup: {e}")
        return 0
