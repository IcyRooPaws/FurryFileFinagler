#!/usr/bin/env python3
# fff: imports from fff.sorter.handlers; main(argv=None). --config's default still means the
# fff: sorter view of the unified config.
"""
FurAffinity Backlog Sorter

Finds FurAffinity files (identified by '-FA-' in the filename) that are not yet
organized into "Furaffinity - {Rating}" subfolders and moves them into the
correct location:

  - Files already sitting inside a configured archive directory are moved into a
    "Furaffinity - {Rating}" subfolder within that SAME archive location
    (preserving a year subfolder, e.g. ".../2023/...", if one is already present).
  - All other files are moved into a "Furaffinity - {Rating}" subfolder under the
    correct live nsfw_*/sfw_* destination directory, based on media type
    (image/video/gif) and the rating found in the filename
    (general -> SFW, mature/adult -> NSFW).

Files under fav_directory, artist_directory, or archived_favorites_directory are
left untouched, respecting the existing favorites hierarchy.

Usage:
    python fa_backlog_sort.py [--dry-run] [--verbose] [--config configs/config.yaml]
"""
import os
import re
import sys
import shutil
import logging
import argparse
from pathlib import Path

from fff.sorter.handlers import Config, DatabaseManager
from fff.sorter.handlers.utils import (
    is_furaffinity_file, get_furaffinity_rating,
    is_image, is_video, is_gif, is_configured_file,
    ensure_directory_exists, log_file_move
)

YEAR_DIR_RE = re.compile(r'^(19|20)\d{2}$')


def _norm(path):
    """Normalize a path for reliable comparison, or None."""
    return os.path.normpath(os.path.abspath(path)) if path else None


def _is_under(path, root):
    """Return True if path is root itself or nested under it."""
    if not root:
        return False
    path_n = _norm(path)
    root_n = _norm(root)
    return path_n == root_n or path_n.startswith(root_n + os.sep)


def collect_archive_leaf_dirs(archive_directories):
    """Flatten archive_directories config into a list of existing leaf directory paths."""
    leaves = []
    for _key, value in (archive_directories or {}).items():
        if not value:
            continue
        if isinstance(value, str):
            leaves.append(value)
        elif isinstance(value, dict):
            for _media_type, path in value.items():
                if path:
                    leaves.append(path)
    return [d for d in leaves if os.path.exists(d)]


def build_scan_roots(config, archive_leaf_dirs):
    """Build the de-duplicated list of top-level directories to scan for backlog files."""
    roots = []
    roots.extend(config.source_directories or [])
    roots.extend((config.destination_directories or {}).values())
    roots.extend(archive_leaf_dirs)
    for attr in ('manual_sort_directory', 'manual_sort_video_directory', 'manual_sort_gif_directory',
                 'possible_missing_files_directory'):
        d = getattr(config, attr, None)
        if d:
            roots.append(d)
    roots.extend(getattr(config, 'other_directories_to_hash', None) or [])

    existing = sorted({_norm(d) for d in roots if d and os.path.exists(d)})

    # Drop any root that is already nested inside another root in the list
    deduped = []
    for r in existing:
        if not any(r != other and _is_under(r, other) for other in existing):
            deduped.append(r)
    return deduped


def build_exclude_roots(config):
    """Directories that make up the favorites hierarchy - never touched by this script."""
    excludes = []
    for attr in ('fav_directory', 'artist_directory', 'archived_favorites_directory'):
        d = getattr(config, attr, None)
        if d:
            excludes.append(d)
    return [_norm(d) for d in excludes if d]


def classify_media_type(filename):
    """Return 'image', 'video', 'gif', or None."""
    if is_video(filename):
        return 'video'
    if is_gif(filename):
        return 'gif'
    if is_image(filename):
        return 'image'
    return None


def find_archive_leaf(file_path, archive_leaf_dirs):
    """Return the archive leaf directory containing file_path, if any (longest match wins)."""
    best = None
    for leaf in archive_leaf_dirs:
        if _is_under(file_path, leaf):
            if best is None or len(_norm(leaf)) > len(_norm(best)):
                best = leaf
    return best


def resolve_destination(file_path, filename, rating, config, archive_leaf_dirs):
    """Determine the 'Furaffinity - {rating}' destination directory for a file, or None.

    Returns a (dest_dir, base_root) tuple, where base_root is the exact configured
    directory (destination_directories/archive_directories entry) the file belongs
    under - needed for DatabaseManager.table_name_for_dest(), which only matches
    exact configured paths, not arbitrary subfolders.
    """
    archive_leaf = find_archive_leaf(file_path, archive_leaf_dirs)
    if archive_leaf:
        rel = os.path.relpath(file_path, archive_leaf)
        parts = rel.split(os.sep)
        year = parts[0] if len(parts) > 1 and YEAR_DIR_RE.match(parts[0]) else None
        if year:
            return os.path.join(archive_leaf, year, f"Furaffinity - {rating}"), archive_leaf
        return os.path.join(archive_leaf, f"Furaffinity - {rating}"), archive_leaf

    media_type = classify_media_type(filename)
    if not media_type:
        return None, None
    base_key = ('sfw_' if rating == 'General' else 'nsfw_') + media_type
    base_dir = (config.destination_directories or {}).get(base_key)
    if not base_dir:
        return None, None
    return os.path.join(base_dir, f"Furaffinity - {rating}"), base_dir


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Find backlog FurAffinity files ('-FA-' in filename) and sort them into "
                    "'Furaffinity - {Rating}' folders"
    )
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be moved without making changes')
    parser.add_argument('--verbose', action='store_true',
                       help='Print every already-sorted/skipped file, not just moves')
    parser.add_argument('--config', default='configs/config.yaml',
                       help='Path to config file (default: configs/config.yaml)')
    args = parser.parse_args(argv)

    print("=" * 80)
    print("FURAFFINITY BACKLOG SORT")
    print("=" * 80)
    if args.dry_run:
        print("[DRY RUN MODE] No files will be moved\n")

    try:
        config = Config(args.config)
        print(f"Config loaded: {args.config}")
    except Exception as e:
        print(f"Failed to load config: {e}")
        return 1

    db_manager = None
    if not args.dry_run:
        try:
            db_manager = DatabaseManager(config)
            print(f"Connected to database: {config.MYSQL_HOST}")
        except Exception as e:
            print(f"Failed to connect to database: {e}")
            return 1

    logger = logging.getLogger('fa_backlog_sort')

    archive_leaf_dirs = collect_archive_leaf_dirs(config.archive_directories)
    exclude_roots = build_exclude_roots(config)
    scan_roots = build_scan_roots(config, archive_leaf_dirs)

    print(f"\nScanning {len(scan_roots)} root director{'y' if len(scan_roots) == 1 else 'ies'}:")
    for r in scan_roots:
        print(f"   - {r}")
    if exclude_roots:
        print("\nExcluding (favorites hierarchy - left untouched):")
        for r in exclude_roots:
            print(f"   - {r}")
    print()

    stats = {
        'found': 0,
        'moved': 0,
        'already_sorted': 0,
        'skipped_no_rating': 0,
        'skipped_unresolved': 0,
        'errors': 0,
    }
    by_rating = {'General': 0, 'Mature': 0, 'Adult': 0}

    for root_dir in scan_roots:
        for dirpath, dirnames, filenames in os.walk(root_dir):
            if any(_is_under(dirpath, ex) for ex in exclude_roots):
                dirnames.clear()
                continue

            dirnames[:] = [
                d for d in dirnames
                if not any(_is_under(os.path.join(dirpath, d), ex) for ex in exclude_roots)
            ]

            for filename in filenames:
                if not is_furaffinity_file(filename):
                    continue

                file_path = os.path.join(dirpath, filename)
                stats['found'] += 1

                if not is_configured_file(filename):
                    stats['skipped_unresolved'] += 1
                    if args.verbose:
                        print(f"  [SKIP] Unsupported file type: {file_path}")
                    continue

                rating = get_furaffinity_rating(filename)
                if not rating:
                    stats['skipped_no_rating'] += 1
                    print(f"  [WARN] No rating found in FA filename, skipping: {file_path}")
                    continue

                dest_dir, base_root = resolve_destination(file_path, filename, rating, config, archive_leaf_dirs)
                if not dest_dir:
                    stats['skipped_unresolved'] += 1
                    print(f"  [WARN] Could not resolve destination for: {file_path}")
                    continue

                dest_path = os.path.join(dest_dir, filename)

                if _norm(dest_path) == _norm(file_path):
                    stats['already_sorted'] += 1
                    if args.verbose:
                        print(f"  [OK] Already sorted: {file_path}")
                    continue

                final_dest_path = dest_path
                if os.path.exists(dest_path):
                    base, ext = os.path.splitext(dest_path)
                    counter = 1
                    while os.path.exists(f"{base}_{counter}{ext}"):
                        counter += 1
                    final_dest_path = f"{base}_{counter}{ext}"
                    print(f"  [WARN] Destination filename collision for '{filename}', "
                          f"using '{os.path.basename(final_dest_path)}'")

                if args.dry_run:
                    print(f"  [DRY RUN] Would move: {file_path}")
                    print(f"                    ->  {final_dest_path}")
                    stats['moved'] += 1
                    by_rating[rating] = by_rating.get(rating, 0) + 1
                    continue

                try:
                    ensure_directory_exists(dest_dir)
                    shutil.move(file_path, final_dest_path)
                    log_file_move(logger, file_path, final_dest_path)
                except Exception as e:
                    stats['errors'] += 1
                    print(f"  [ERROR] Failed to move {file_path}: {e}")
                    logger.error(f"Failed to move {file_path} -> {final_dest_path}: {e}")
                    continue

                # The move itself succeeded - DB sync failures are reported separately
                # so they're never mistaken for a failed file move.
                if db_manager:
                    try:
                        table = db_manager.table_name_for_dest(base_root)
                        if table:
                            db_manager.update_file_path(table, filename, final_dest_path)
                        db_manager.log_move_operation(
                            file_name=filename,
                            source_path=file_path,
                            destination_path=final_dest_path,
                            source_directory=os.path.basename(dirpath),
                            destination_directory=os.path.basename(dest_dir),
                            reason='fa_backlog_sort'
                        )
                    except Exception as e:
                        print(f"  [WARN] Moved but failed to sync database record for {filename}: {e}")
                        logger.warning(f"Failed to sync DB for {filename} after move to {final_dest_path}: {e}")

                print(f"  [OK] Moved: {file_path}")
                print(f"          ->  {final_dest_path}")
                stats['moved'] += 1
                by_rating[rating] = by_rating.get(rating, 0) + 1

    print("\n" + "=" * 80)
    print("DRY RUN COMPLETE" if args.dry_run else "BACKLOG SORT COMPLETE")
    print("=" * 80)
    print(f"FurAffinity files found:  {stats['found']}")
    print(f"  Already sorted:         {stats['already_sorted']}")
    print(f"  {'Would move' if args.dry_run else 'Moved'}:{'  ' if args.dry_run else '            '}{stats['moved']}")
    for rating, count in by_rating.items():
        if count:
            print(f"      - {rating}: {count}")
    print(f"  Skipped (no rating):    {stats['skipped_no_rating']}")
    print(f"  Skipped (unresolved):   {stats['skipped_unresolved']}")
    if stats['errors']:
        print(f"  Errors:                 {stats['errors']}")
    print("=" * 80)

    if args.dry_run and stats['moved']:
        print("\nRun without --dry-run to apply changes")

    return 0


if __name__ == '__main__':
    sys.exit(main())
