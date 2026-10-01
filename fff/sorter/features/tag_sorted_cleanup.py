# fff: implemented in media sorter/fff.sorter/features/tag_sorted_cleanup.py.
"""Remove empty folders from configured tag-sorted directory trees."""

import logging
import os
from pathlib import Path
from typing import Iterable, List, Tuple


logger = logging.getLogger('fff.sorter.tag_sorted_cleanup')


def _tag_sorted_root(path: str) -> str:
    """Return the path through the first ``Tag Sorted`` component."""
    path_obj = Path(path)
    parts = path_obj.parts
    for index, part in enumerate(parts):
        if part.casefold().replace('_', ' ') == 'tag sorted':
            return str(Path(*parts[:index + 1]))
    return ''


def get_tag_sorted_directories(config) -> List[str]:
    """Find unique configured roots whose path includes ``Tag Sorted``."""
    candidates = []

    base_directories = getattr(config, 'get_tag_sorting_base_dirs', lambda: {})() or {}
    candidates.extend(base_directories.values())

    tag_config = getattr(config, 'get_tag_sorting_config', lambda: {})() or {}
    for rule in tag_config.get('rules', []) or []:
        candidates.append(rule.get('target_directory'))
        candidates.extend((rule.get('base_directories') or {}).values())

    roots = {}
    for candidate in candidates:
        if not candidate:
            continue
        root = _tag_sorted_root(str(candidate))
        if root:
            key = os.path.normcase(os.path.normpath(os.path.abspath(root)))
            roots[key] = root

    return list(roots.values())


def remove_empty_folders(directories: Iterable[str], dry_run: bool = False) -> Tuple[int, int]:
    """Remove nested empty folders below each directory.

    Returns:
        A tuple containing deleted/found folders and folders skipped due to errors.
    """
    deleted = 0
    skipped = 0

    for directory in directories:
        root = Path(directory)
        if not root.is_dir():
            logger.debug("Skipping missing tag-sorted directory: %s", directory)
            continue

        for dirpath, _, _ in os.walk(root, topdown=False):
            current = Path(dirpath)
            if current == root or current.name.startswith('.'):
                continue

            try:
                if not any(current.iterdir()):
                    if dry_run:
                        logger.info("[DRY RUN] Would remove empty tag-sorted folder: %s", current)
                    else:
                        current.rmdir()
                        logger.info("Removed empty tag-sorted folder: %s", current)
                    deleted += 1
            except (PermissionError, OSError) as error:
                logger.warning("Could not remove empty folder %s: %s", current, error)
                skipped += 1

    return deleted, skipped


def cleanup_tag_sorted_directories(config, dry_run: bool = False) -> Tuple[int, int]:
    """Clean empty folders from all configured tag-sorted roots."""
    directories = get_tag_sorted_directories(config)
    if not directories:
        logger.info("No Tag Sorted directories configured; skipping empty-folder cleanup")
        return 0, 0

    deleted, skipped = remove_empty_folders(directories, dry_run=dry_run)
    logger.info(
        "Tag Sorted empty-folder cleanup complete: %s %s, %s skipped",
        'found' if dry_run else 'removed',
        deleted,
        skipped,
    )
    return deleted, skipped