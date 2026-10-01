"""File moves, copies, deletes and renames, logged to the log file and ``file_operations``.

The ``*_with_logging`` helpers are copied verbatim from FFF/_handlers/utils.py.
``db_manager`` may be the sorter's DatabaseManager or an ``OperationLogger`` (below),
which writes ``file_operations`` rows without the rest of the sorter's DB layer. Search
workflows use the latter.
"""
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# --- e621 archive/modules/config.py: backslash paths for e621_posts_db ---

def normalize_file_path(file_path):
    """
    Normalize file path to use Windows backslashes consistently.
    
    Args:
        file_path: Path string to normalize
        
    Returns:
        Normalized path with backslashes on Windows, or original if empty/None
    """
    if not file_path:
        return file_path
    if not isinstance(file_path, str):
        return file_path
    # Replace forward slashes with backslashes on Windows
    if os.name == 'nt':  # Windows
        return file_path.replace('/', '\\')
    return file_path


# --- FFF/_handlers/utils.py: forward-slash paths for image_sorting_hashes, logged file ops ---

def ensure_directory_exists(directory):
    """Create directory if it doesn't exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")


def normalize_path(path):
    """
    Normalize file path to use forward slashes consistently.

    This ensures all file paths in the database use forward slashes (/)
    for cross-platform compatibility and easier SQL queries.

    Forward slashes work correctly on both Windows and Unix-like systems
    when accessing files through Python's os module.

    Args:
        path (str): File path to normalize

    Returns:
        str: Normalized path with forward slashes
    """
    if path is None:
        return None

    # Use os.path.normpath to handle path normalization
    normalized = os.path.normpath(path)

    # Convert backslashes to forward slashes for cross-platform consistency
    normalized = normalized.replace('\\', '/')

    return normalized


def log_file_move(logger, source_path, destination_path, move_type="file"):
    """Log a single file move with exact source and destination paths."""
    logger.info(
        "Moved file (%s): '%s' -> '%s'",
        move_type,
        normalize_path(source_path),
        normalize_path(destination_path),
    )


def move_file_with_logging(source_path, destination_path, db_manager=None, logger_obj=None,
                           source_directory=None, destination_directory=None, reason=None):
    """
    Move a file and log the operation to both log file and database.

    Args:
        source_path: Full path to source file
        destination_path: Full path to destination
        db_manager: DatabaseManager instance for logging (optional)
        logger_obj: Logger instance for file logging (optional)
        source_directory: Source directory name/type (optional)
        destination_directory: Destination directory name/type (optional)
        reason: Reason for the move (optional)

    Returns:
        bool: True if successful, False otherwise

    Raises:
        Exception: If the move operation fails
    """
    import shutil

    file_name = os.path.basename(source_path)
    file_size = None

    try:
        # Get file size before moving
        if os.path.exists(source_path):
            file_size = os.path.getsize(source_path)

        # Perform the move
        shutil.move(source_path, destination_path)

        # Log to file logger
        if logger_obj:
            log_file_move(logger_obj, source_path, destination_path)

        # Update database entries when moving files between destinations
        if db_manager:
            source_table = _find_table_for_path(source_path, db_manager)
            dest_table = _find_table_for_path(destination_path, db_manager)

            if source_table and dest_table:
                if source_table == dest_table:
                    # Moving within same destination (e.g., to subdirectory) - just update path
                    try:
                        db_manager.update_file_path(source_table, file_name, destination_path)
                        if logger_obj:
                            logger_obj.debug(f"Updated database path for {file_name} in table {source_table}")
                    except Exception as update_err:
                        if logger_obj:
                            logger_obj.warning(f"Failed to update database path for {file_name}: {update_err}")
                else:
                    # Moving to different destination - copy entry to new table and delete from old
                    try:
                        # Copy the database entry to the new table
                        success = db_manager.copy_file_entry_between_tables(
                            source_table, dest_table, file_name, destination_path
                        )
                        if success:
                            if logger_obj:
                                logger_obj.info(f"Copied database entry for {file_name} from {source_table} to {dest_table}")
                        else:
                            if logger_obj:
                                logger_obj.warning(f"No entry found to copy for {file_name} in {source_table}")
                    except Exception as copy_err:
                        if logger_obj:
                            logger_obj.error(f"Failed to copy database entry for {file_name}: {copy_err}")
            elif source_table:
                # Destination doesn't map to a table - just update path
                try:
                    db_manager.update_file_path(source_table, file_name, destination_path)
                    if logger_obj:
                        logger_obj.debug(f"Updated database path for {file_name} in table {source_table}")
                except Exception as update_err:
                    if logger_obj:
                        logger_obj.warning(f"Failed to update database path for {file_name}: {update_err}")

            # Log the move operation to file_operations table
            db_manager.log_move_operation(
                file_name=file_name,
                source_path=source_path,
                destination_path=destination_path,
                source_directory=source_directory,
                destination_directory=destination_directory,
                file_size=file_size,
                reason=reason
            )

        return True

    except Exception as e:
        # Log the failure
        if logger_obj:
            logger_obj.error(f"Failed to move file from '{source_path}' to '{destination_path}': {e}")

        if db_manager:
            db_manager.log_failed_operation(
                operation_type='move',
                file_name=file_name,
                source_path=source_path,
                destination_path=destination_path,
                error_message=str(e),
                reason=reason
            )

        raise


def _find_table_for_path(file_path, db_manager):
    """
    Find which destination table contains a record for the given file path.

    This is used when moving files within destination directories (e.g., to subdirectories)
    to determine which table needs its file_path updated.

    Args:
        file_path: Full path to file
        db_manager: DatabaseManager instance

    Returns:
        str: Table name if found, None otherwise
    """
    if not db_manager or not hasattr(db_manager, 'destination_directories'):
        return None

    # Normalize the file path for comparison
    normalized_file_path = os.path.abspath(file_path).lower()

    # Check each destination directory to see if file is within it
    for dest_key, dest_dir in db_manager.destination_directories.items():
        normalized_dest = os.path.abspath(dest_dir).lower()
        if normalized_file_path.startswith(normalized_dest):
            # File is within this destination directory
            table = db_manager.table_name_for_dest(dest_dir)
            if table.startswith("questionable_"):
                table = table.replace("questionable_", "nsfw_")
            return table

    # Check favorites directory
    if hasattr(db_manager, 'config') and hasattr(db_manager.config, 'fav_directory'):
        fav_dir = db_manager.config.fav_directory
        if fav_dir:
            normalized_fav = os.path.abspath(fav_dir).lower()
            if normalized_file_path.startswith(normalized_fav):
                return 'favorites'

    # Check artist directory
    if hasattr(db_manager, 'config') and hasattr(db_manager.config, 'artist_directory'):
        artist_dir = db_manager.config.artist_directory
        if artist_dir:
            normalized_artist = os.path.abspath(artist_dir).lower()
            if normalized_file_path.startswith(normalized_artist):
                return 'favorites'  # Artist files typically use favorites table

    # Check archive directories
    if hasattr(db_manager, 'config') and hasattr(db_manager.config, 'archive_directories'):
        archive_dirs = db_manager.config.archive_directories
        for archive_key, archive_path in archive_dirs.items():
            if isinstance(archive_path, dict):
                # Expanded format (images/videos/gifs)
                for media_type, media_path in archive_path.items():
                    if media_path:
                        normalized_archive = os.path.abspath(media_path).lower()
                        if normalized_file_path.startswith(normalized_archive):
                            # Construct archive table name
                            return f"archive_{archive_key}_{media_type}"
            elif archive_path:
                # Simple format
                normalized_archive = os.path.abspath(archive_path).lower()
                if normalized_file_path.startswith(normalized_archive):
                    return f"archive_{archive_key}"

    return None


def copy_file_with_logging(source_path, destination_path, db_manager=None, logger_obj=None,
                           source_directory=None, destination_directory=None, reason=None):
    """
    Copy a file and log the operation to both log file and database.

    Args:
        source_path: Full path to source file
        destination_path: Full path to destination
        db_manager: DatabaseManager instance for logging (optional)
        logger_obj: Logger instance for file logging (optional)
        source_directory: Source directory name/type (optional)
        destination_directory: Destination directory name/type (optional)
        reason: Reason for the copy (optional)

    Returns:
        bool: True if successful, False otherwise

    Raises:
        Exception: If the copy operation fails
    """
    import shutil

    file_name = os.path.basename(source_path)
    file_size = None

    try:
        # Get file size before copying
        if os.path.exists(source_path):
            file_size = os.path.getsize(source_path)

        # Perform the copy
        shutil.copy2(source_path, destination_path)

        # Log to file logger
        if logger_obj:
            logger_obj.info(f"Copied file: '{source_path}' -> '{destination_path}'")

        # Log to database
        if db_manager:
            db_manager.log_copy_operation(
                file_name=file_name,
                source_path=source_path,
                destination_path=destination_path,
                source_directory=source_directory,
                destination_directory=destination_directory,
                file_size=file_size,
                reason=reason
            )

        return True

    except Exception as e:
        # Log the failure
        if logger_obj:
            logger_obj.error(f"Failed to copy file from '{source_path}' to '{destination_path}': {e}")

        if db_manager:
            db_manager.log_failed_operation(
                operation_type='copy',
                file_name=file_name,
                source_path=source_path,
                destination_path=destination_path,
                error_message=str(e),
                reason=reason
            )

        raise


def delete_file_with_logging(file_path, db_manager=None, logger_obj=None,
                             source_directory=None, file_hash=None, reason=None):
    """
    Delete a file and log the operation to both log file and database.

    Args:
        file_path: Full path to file to delete
        db_manager: DatabaseManager instance for logging (optional)
        logger_obj: Logger instance for file logging (optional)
        source_directory: Source directory name/type (optional)
        file_hash: Hash of the file (optional)
        reason: Reason for the deletion (optional)

    Returns:
        bool: True if successful, False otherwise

    Raises:
        Exception: If the delete operation fails
    """
    file_name = os.path.basename(file_path)
    file_size = None

    try:
        # Get file size before deleting
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)

        # Perform the delete
        os.remove(file_path)

        # Log to file logger
        if logger_obj:
            logger_obj.info(f"Deleted file: '{file_path}'")

        # Log to database
        if db_manager:
            db_manager.log_delete_operation(
                file_name=file_name,
                source_path=file_path,
                source_directory=source_directory,
                file_hash=file_hash,
                file_size=file_size,
                reason=reason
            )

        return True

    except Exception as e:
        # Log the failure
        if logger_obj:
            logger_obj.error(f"Failed to delete file '{file_path}': {e}")

        if db_manager:
            db_manager.log_failed_operation(
                operation_type='delete',
                file_name=file_name,
                source_path=file_path,
                error_message=str(e),
                reason=reason
            )

        raise


# --- fff additions -----------------------------------------------------------

class OperationLogger:
    """Writes ``file_operations`` rows in the sorter DB, matching DatabaseManager.log_*.

    Same SQL, retry and "never raise" behaviour as FFF's
    DatabaseManager.log_file_operation. The original log_delete_operation and
    log_rename_operation passed ``file_hash=`` to a method that no longer accepted it
    (a TypeError); here ``file_hash`` is accepted and ignored, since the column was dropped.
    """

    def __init__(self, connect=None, reason_prefix=None):
        if connect is None:
            from fff.common import db

            def connect():
                return db.connect("sorter")
        self._connect = connect
        self.reason_prefix = reason_prefix

    def _reason(self, reason):
        if self.reason_prefix and reason:
            return f"{self.reason_prefix}:{reason}"
        return reason or self.reason_prefix

    def log_file_operation(self, operation_type, file_name, source_path=None, destination_path=None,
                           source_directory=None, destination_directory=None,
                           file_size=None, operation_status='success', error_message=None, reason=None,
                           file_hash=None):
        if source_path:
            source_path = normalize_path(str(source_path))
        if destination_path:
            destination_path = normalize_path(str(destination_path))
        values = (operation_type, file_name, source_path, destination_path,
                  source_directory, destination_directory, file_size,
                  operation_status, error_message, self._reason(reason))
        for attempt in range(3):
            conn = None
            try:
                conn = self._connect()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO file_operations (
                        operation_type, file_name, source_path, destination_path,
                        source_directory, destination_directory, file_size,
                        operation_status, error_message, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, values)
                conn.commit()
                cursor.close()
                return
            except Exception as e:
                if "lock" in str(e).lower() and attempt < 2:
                    time.sleep(0.5)
                else:
                    # Don't raise - logging failure shouldn't stop file operations
                    logger.warning(f"Failed to log file operation: {e}")
                    return
            finally:
                if conn:
                    conn.close()

    def log_move_operation(self, file_name, source_path, destination_path, source_directory=None,
                           destination_directory=None, file_size=None, reason=None):
        self.log_file_operation('move', file_name, source_path, destination_path, source_directory,
                                destination_directory, file_size, reason=reason)

    def log_copy_operation(self, file_name, source_path, destination_path, source_directory=None,
                           destination_directory=None, file_size=None, reason=None):
        self.log_file_operation('copy', file_name, source_path, destination_path, source_directory,
                                destination_directory, file_size, reason=reason)

    def log_delete_operation(self, file_name, source_path, source_directory=None, file_hash=None,
                             file_size=None, reason=None):
        self.log_file_operation('delete', file_name, source_path, source_directory=source_directory,
                                file_size=file_size, reason=reason)

    def log_rename_operation(self, old_name, new_name, directory_path, file_hash=None, file_size=None,
                             reason=None):
        self.log_file_operation('rename', new_name, os.path.join(directory_path, old_name),
                                os.path.join(directory_path, new_name), directory_path, directory_path,
                                file_size, reason=reason)

    def log_failed_operation(self, operation_type, file_name, source_path=None, destination_path=None,
                             error_message=None, reason=None):
        self.log_file_operation(operation_type, file_name, source_path, destination_path,
                                operation_status='failed', error_message=error_message, reason=reason)


def rename_file_with_logging(source_path, destination_path, db_manager=None, logger_obj=None, reason=None):
    """Rename a file in place (logged as 'rename'); a different folder is logged as a 'move'."""
    source_path, destination_path = str(source_path), str(destination_path)
    if os.path.dirname(os.path.abspath(source_path)) != os.path.dirname(os.path.abspath(destination_path)):
        return move_file_with_logging(source_path, destination_path, db_manager, logger_obj, reason=reason)
    file_size = os.path.getsize(source_path) if os.path.exists(source_path) else None
    try:
        os.rename(source_path, destination_path)
    except Exception as e:
        if logger_obj:
            logger_obj.error(f"Failed to rename '{source_path}' to '{destination_path}': {e}")
        if db_manager:
            db_manager.log_failed_operation('rename', os.path.basename(source_path), source_path,
                                            destination_path, str(e), reason)
        raise
    if logger_obj:
        logger_obj.info(f"Renamed file: '{source_path}' -> '{destination_path}'")
    if db_manager:
        db_manager.log_rename_operation(os.path.basename(source_path), os.path.basename(destination_path),
                                        os.path.dirname(source_path), file_size=file_size, reason=reason)
    return True


def unique_destination(destination: Path) -> Path:
    """reverse search's collision rule: name.ext, name_1.ext, name_2.ext, ..."""
    counter = 1
    original_destination = destination
    while destination.exists():
        stem = original_destination.stem
        suffix = original_destination.suffix
        destination = original_destination.parent / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination


def move_to_directory(image_path: Path, target_dir: Path, base_search_dir: Path = None,
                      preserve_structure: bool = False, *, db_manager=None, reason=None,
                      error_label: str = "Error moving file") -> bool:
    """reverse search's move_to_{source_found,not_found,blacklisted} (5 identical copies), now logged.

    Keeps subfolders relative to ``base_search_dir`` when ``preserve_structure`` is set,
    avoids name collisions with ``_N`` suffixes, and prints and returns False on error.
    """
    try:
        if preserve_structure and base_search_dir:
            try:
                relative_path = image_path.relative_to(base_search_dir)
                destination = target_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
            except ValueError:
                target_dir.mkdir(exist_ok=True)
                destination = target_dir / image_path.name
        else:
            target_dir.mkdir(exist_ok=True)
            destination = target_dir / image_path.name

        destination = unique_destination(destination)
        move_file_with_logging(str(image_path), str(destination), db_manager=db_manager, logger_obj=logger,
                               source_directory=str(image_path.parent), destination_directory=str(target_dir),
                               reason=reason)
        return True
    except Exception as e:
        print(f"    {error_label}: {e}")
        return False
