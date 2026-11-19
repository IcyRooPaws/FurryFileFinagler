"""Find and move every local copy of an e621 post's file before the post leaves the database.

``posts.file_path`` is often stale: the sorter moves files (tag sort, artist folders, archive)
without updating e621_posts_db, and a post can have more than one copy in the library. Code
that removed a post used to act on ``posts.file_path`` only, then delete the row, which left the
real file behind with nothing pointing at it. Every removal path goes through
``move_post_files`` instead, which also finds the post's files in the sorter database (by
``e621_post_id`` and by MD5), moves them, and drops their sorter rows.

Call it while the posts are still in the ``posts`` table, and delete only the post ids it
returns as ``cleared``; a post whose file could not be moved stays, so the next run retries it.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fff.archive import paths as archive_paths
from fff.common import fileops

logger = logging.getLogger(__name__)
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_CHUNK = 500
_ops = None


def _operation_logger():
    global _ops
    if _ops is None:
        _ops = fileops.OperationLogger(reason_prefix="archive")
    return _ops


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _chunks(items):
    items = list(items)
    for i in range(0, len(items), _CHUNK):
        yield items[i:i + _CHUNK]


def _resolve_existing(file_path):
    # Local import: fff.archive.db is heavy and imports this module's neighbours.
    from fff.archive.db import resolve_existing_file_path

    return resolve_existing_file_path(file_path)


def find_post_files(conn, post_ids) -> dict[int, list[tuple[str, str | None]]]:
    """``{post_id: [(path, sorter_table or None), ...]}`` for every copy that exists on disk.

    ``conn`` is an e621_posts_db connection; the posts must still be in ``posts``.
    """
    post_ids = sorted({int(p) for p in post_ids if p})
    found: dict[int, dict[str, tuple[str, str | None]]] = {pid: {} for pid in post_ids}
    if not post_ids:
        return {}

    def add(pid, path, table=None):
        if not path:
            return
        existing = _resolve_existing(path)
        if not existing:
            return
        key = _key(existing)
        # Prefer the entry that knows its sorter table.
        if key not in found[pid] or (table and not found[pid][key][1]):
            found[pid][key] = (existing, table if existing == path else None)

    md5_to_post = {}
    cursor = conn.cursor()
    try:
        for chunk in _chunks(post_ids):
            cursor.execute(
                f"SELECT id, file_path, md5 FROM posts WHERE id IN ({','.join(['%s'] * len(chunk))})", chunk)
            for pid, file_path, md5 in cursor.fetchall():
                add(pid, file_path)
                if md5:
                    md5_to_post[md5.lower()] = pid

        sorter = archive_paths.db_names()["sorter"]
        try:
            for chunk in _chunks(post_ids):
                cursor.execute(
                    f"SELECT e621_post_id, file_path, table_name FROM `{sorter}`.all_files "
                    f"WHERE e621_post_id IN ({','.join(['%s'] * len(chunk))})", chunk)
                for pid, file_path, table in cursor.fetchall():
                    add(int(pid), file_path, table)
            for chunk in _chunks(md5_to_post):
                cursor.execute(
                    f"SELECT md5_hash, file_path, table_name FROM `{sorter}`.all_files "
                    f"WHERE md5_hash IN ({','.join(['%s'] * len(chunk))})", chunk)
                for md5, file_path, table in cursor.fetchall():
                    pid = md5_to_post.get((md5 or "").lower())
                    if pid:
                        add(pid, file_path, table)
        except Exception as e:  # sorter database missing or unreachable: posts.file_path still counts
            logger.warning("Could not look up post files in the sorter database: %s", e)
    finally:
        cursor.close()
    return {pid: list(paths.values()) for pid, paths in found.items() if paths}


def _drop_sorter_row(conn, table, file_path):
    if not table or not _TABLE_NAME.match(table):
        return
    sorter = archive_paths.db_names()["sorter"]
    cursor = conn.cursor()
    try:
        cursor.execute(f"DELETE FROM `{sorter}`.`{table}` WHERE file_path = %s", (file_path,))
        conn.commit()
    except Exception as e:
        logger.warning("Moved %s but could not remove its %s row: %s", file_path, table, e)
    finally:
        cursor.close()


def record_rename(conn, old_path, new_path, reason) -> int:
    """After a library file was renamed in place: log it and re-point its sorter row.

    The archive renames files when a post's artist or rating changes. Without this the sorter
    row kept the old name until the next hash pass threw it away and rehashed the file, and the
    rename never reached ``file_operations``. ``conn`` is an e621_posts_db connection with nothing
    uncommitted. Never raises. Returns the number of sorter rows updated.
    """
    old_path, new_path = str(old_path), str(new_path)
    try:
        size = os.path.getsize(new_path) if os.path.exists(new_path) else None
        _operation_logger().log_rename_operation(
            os.path.basename(old_path), os.path.basename(new_path), os.path.dirname(old_path),
            file_size=size, reason=reason)
    except Exception as e:
        logger.warning("Could not log the rename of %s: %s", old_path, e)

    updated = 0
    cursor = None
    try:
        cursor = conn.cursor()
        sorter = archive_paths.db_names()["sorter"]
        # Sorter rows hold forward-slash paths, with a few backslash ones among them.
        spellings = sorted({old_path, fileops.normalize_path(old_path),
                            fileops.normalize_file_path(os.path.normpath(old_path))})
        cursor.execute(
            f"SELECT table_name, file_path FROM `{sorter}`.all_files "
            f"WHERE file_path IN ({','.join(['%s'] * len(spellings))})", spellings)
        for table, stored in cursor.fetchall():
            if not table or not _TABLE_NAME.match(table):
                continue
            cursor.execute(
                # The artist scan caches its verdict per row; a new name needs a new look.
                f"UPDATE `{sorter}`.`{table}` SET file_name = %s, file_path = %s, "
                "last_artist_check = NULL, artist_matched = NULL WHERE file_path = %s",
                (os.path.basename(new_path), fileops.normalize_path(new_path), stored))
            updated += 1
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning("Renamed %s to %s but could not update its sorter row: %s",
                       old_path, os.path.basename(new_path), e)
    finally:
        if cursor is not None:
            cursor.close()
    return updated


def _apply(conn, post_ids, action, describe, dry_run, keep=()) -> dict:
    post_ids = {int(p) for p in post_ids if p}
    stats = {"done": 0, "failed": 0, "cleared": set(post_ids)}
    kept = {_key(path) for path in keep}
    for pid, copies in find_post_files(conn, post_ids).items():
        for path, table in copies:
            if _key(path) in kept:
                continue
            if dry_run:
                logger.info("[DRY RUN] Would %s file of post %s: %s", describe, pid, path)
                stats["done"] += 1
                continue
            try:
                action(path)
            except Exception as e:
                stats["failed"] += 1
                stats["cleared"].discard(pid)
                logger.error("Failed to %s file of post %s (%s): %s", describe, pid, path, e)
                continue
            stats["done"] += 1
            _drop_sorter_row(conn, table, path)
    return stats


def move_post_files(conn, post_ids, destination_dir, reason, dry_run=False, keep=()) -> dict:
    """Move every local copy of these posts' files into ``destination_dir``.

    ``keep`` lists paths to leave where they are (a copy that is still wanted).

    Returns ``{"moved": n, "failed": n, "cleared": set(post ids safe to delete)}``. A post with
    no file on disk is cleared; a post with any copy that failed to move is not.
    """
    destination = Path(destination_dir)

    def move(path):
        destination.mkdir(parents=True, exist_ok=True)
        target = fileops.unique_destination(destination / os.path.basename(path))
        try:
            fileops.move_file_with_logging(
                path, str(target), db_manager=_operation_logger(), logger_obj=logger,
                source_directory=os.path.dirname(path), destination_directory=str(destination),
                reason=reason)
        except Exception:
            # Across drives a move is a copy, then a delete. A file held open by another program
            # copies fine and then refuses the delete: without this every retry would leave one
            # more full copy in the destination.
            if os.path.exists(path) and target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            raise
        logger.info("Moved post file %s -> %s", path, target)

    stats = _apply(conn, post_ids, move, f"move to {destination}", dry_run, keep)
    stats["moved"] = stats.pop("done")
    return stats


def trash_post_files(conn, post_ids, reason, dry_run=False) -> dict:
    """Send every local copy of these posts' files to the recycle bin.

    Returns ``{"trashed": n, "failed": n, "cleared": set(post ids safe to delete)}``.
    """
    from send2trash import send2trash

    def trash(path):
        size = os.path.getsize(path)
        send2trash(path)
        _operation_logger().log_delete_operation(
            file_name=os.path.basename(path), source_path=path,
            source_directory=os.path.dirname(path), file_size=size, reason=reason)
        logger.info("Sent post file to the recycle bin: %s", path)

    stats = _apply(conn, post_ids, trash, "trash", dry_run)
    stats["trashed"] = stats.pop("done")
    return stats
