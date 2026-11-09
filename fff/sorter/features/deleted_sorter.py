# fff: schema names in the cross-database query come from the environment.
"""
Move files whose e621 post is marked deleted into a dedicated directory.

Queries image_sorting_hashes.all_files joined with e621_posts_db.posts and
moves any files where posts.deleted = 1 to the configured deleted_from_e621 directory.
"""
import logging
import os
import shutil
from typing import Dict, List, Tuple

from mysql.connector import Error

from fff.common import db
from fff.sorter import paths as sorter_paths

logger = logging.getLogger(__name__)


def ensure_directory_exists(directory: str) -> None:
    """Create directory if it doesn't exist."""
    if directory and not os.path.exists(directory):
        os.makedirs(directory)


class DeletedSorter:
    """Handles relocating files whose e621 posts are marked as deleted."""

    def __init__(self, config, e621_config: Dict, db_manager):
        self.config = config
        self.e621_config = e621_config
        self.db_manager = db_manager

    def _connect(self):
        """Create a MySQL connection to the e621 database host."""
        return db.connect_with(
            self.e621_config,
            database=self.e621_config.get("database") or sorter_paths.db_names()["archive"],
            autocommit=True,
            connection_timeout=30,
            charset="utf8mb4",
            use_unicode=True,
            buffered=True,
        )

    def _fetch_deleted_files(self) -> List[Dict]:
        """Return rows for files whose linked post is marked deleted."""
        DB = sorter_paths.db_names()  # fff: prod or test_ schema names
        query = (
            "SELECT af.file_name, af.file_path, af.e621_post_id, af.table_name "
            f"FROM {DB['sorter']}.all_files af "
            f"LEFT JOIN {DB['archive']}.posts p ON p.id = af.e621_post_id "
            "WHERE p.deleted = 1"
        )

        try:
            conn = self._connect()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return rows or []
        except Error as e:
            logger.error(f"Failed querying deleted posts: {e}")
            return []

    def _table_directory_map(self) -> Dict[str, str]:
        """Build mapping from table names to on-disk directories."""
        dest = self.config.destination_directories
        mapping = {
            "favorites": getattr(self.config, "fav_directory", None),
            "manual_sort_images": getattr(self.config, "manual_sort_directory", None),
            "manual_sort_videos": getattr(self.config, "manual_sort_video_directory", None),
            "sfw_image": dest.get("sfw_image"),
            "sfw_video": dest.get("sfw_video"),
            "sfw_gif": dest.get("sfw_gif"),
            "nsfw_image": dest.get("nsfw_image"),
            "nsfw_video": dest.get("nsfw_video"),
            "nsfw_gif": dest.get("nsfw_gif"),
        }
        # Include optional gif manual dir when present in config
        manual_gif = getattr(self.config, "manual_sort_gif_directory", None)
        if manual_gif:
            mapping["manual_sort_gifs"] = manual_gif
        return {k: v for k, v in mapping.items() if v}

    def _resolve_path(self, row: Dict, table_map: Dict[str, str]) -> Tuple[str, str]:
        """Determine the current file path and its source directory."""
        file_path = row.get("file_path") or ""
        if file_path and os.path.exists(file_path):
            return file_path, os.path.dirname(file_path)

        table = row.get("table_name")
        base_dir = table_map.get(table)
        if base_dir:
            candidate = os.path.join(base_dir, row.get("file_name"))
            return candidate, base_dir

        return "", ""

    def _unique_destination(self, dest_dir: str, file_name: str) -> str:
        """Return a destination path that will not overwrite an existing file."""
        base, ext = os.path.splitext(file_name)
        candidate = os.path.join(dest_dir, file_name)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(dest_dir, f"{base}_{counter}{ext}")
            counter += 1
        return candidate

    def _is_protected_path(self, source_dir: str) -> bool:
        """Return True when the source directory should never be moved (favorites/top priority)."""
        fav_dir = getattr(self.config, "fav_directory", None)
        if fav_dir:
            try:
                if os.path.commonpath([os.path.abspath(source_dir), os.path.abspath(fav_dir)]) == os.path.abspath(fav_dir):
                    return True
            except ValueError:
                pass  # different drives; ignore

        lowered = source_dir.lower()
        return "- fav" in lowered or "favorites" in lowered

    def move_deleted_posts(self, target_directory: str, dry_run: bool = False) -> Dict[str, int]:
        """Move all files whose posts are deleted to the target directory.
        
        Args:
            target_directory: Destination directory for deleted post files
            dry_run: If True, only report what would be moved without actually moving files
        """
        ensure_directory_exists(target_directory)
        table_map = self._table_directory_map()
        rows = self._fetch_deleted_files()

        moved = 0
        missing = 0
        move_errors = 0
        db_errors = 0
        skipped = 0

        if not rows:
            return {
                "moved": moved,
                "missing": missing,
                "move_errors": move_errors,
                "db_errors": db_errors,
            }

        for row in rows:
            file_name = row.get("file_name")
            file_path, source_dir = self._resolve_path(row, table_map)

            if not file_path or not os.path.exists(file_path):
                missing += 1
                continue

            if source_dir and self._is_protected_path(source_dir):
                skipped += 1
                continue

            # Skip if file is already in the target directory
            if source_dir and os.path.normpath(source_dir) == os.path.normpath(target_directory):
                skipped += 1
                continue

            try:
                dest_path = self._unique_destination(target_directory, file_name)
                
                if dry_run:
                    logger.info(f"[DRY RUN] Would move '{file_path}' to '{dest_path}'")
                    moved += 1
                else:
                    shutil.move(file_path, dest_path)
                    moved += 1
            except Exception as move_err:  # pragma: no cover - file system errors
                move_errors += 1
                logger.error(f"Failed to move '{file_path}' to deleted folder: {move_err}")
                continue

            # Update database entry (skip in dry-run mode)
            source_table = row.get("table_name")
            if not dry_run:
                try:
                    updated = self.db_manager.update_file_path(source_table, file_name, dest_path)
                    if not updated:
                        db_errors += 1
                        logger.error(f"DB entry not found for {file_name} in {source_table} when updating path")
                except Exception as db_err:
                    db_errors += 1
                    logger.error(f"Failed to update DB entry for {file_name} in {source_table}: {db_err}")

        return {
            "moved": moved,
            "missing": missing,
            "move_errors": move_errors,
            "db_errors": db_errors,
            "skipped": skipped,
        }
