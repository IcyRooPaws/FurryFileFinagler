"""Send files from folders that no tag rule uses any more back to the sort queue.

When a tag rule is deleted (or its folder changes), the files it sorted stay in its old folder
and nothing sorts them again. The tag-sort GUI calls this module on save: it compares the
folders the rules used before and after, and moves the files in the folders that are no longer
used into the first source directory ("Need to be Sorted"), where the next sort picks them up.
"""
import logging
import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from fff.common import protected
from fff.common.fileops import move_file_with_logging, unique_destination

logger = logging.getLogger('fff.sorter.removed_rule_dirs')

MOVE_REASON = 'tag_rule_removed'


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _inside(path_key: str, root_key: str) -> bool:
    """``path_key`` is ``root_key`` or somewhere below it (both from ``_key``)."""
    return path_key == root_key or path_key.startswith(root_key.rstrip(os.sep) + os.sep)


def find_removed_directories(old_dirs: Iterable[str], new_dirs: Iterable[str],
                             never_empty: Iterable[str] = ()) -> List[str]:
    """Folders in ``old_dirs`` that no folder in ``new_dirs`` matches.

    A folder that is, or contains, one of ``never_empty`` (the source, destination, favourites
    and other core folders) is never returned, so a rule that pointed straight at one of them
    can't empty it. A folder nested inside another returned folder is left out, because
    emptying the outer folder covers it.
    """
    new_keys = {_key(d) for d in new_dirs if d}
    guards = [_key(d) for d in never_empty if d]
    found = {}
    for d in old_dirs:
        if not d:
            continue
        k = _key(d)
        if k in new_keys or k in found:
            continue
        if any(_inside(g, k) for g in guards):
            logger.info("Not emptying %s: it is or contains a core folder", d)
            continue
        found[k] = d
    return [d for k, d in found.items()
            if not any(other != k and _inside(k, other) for other in found)]


def list_files(directories: Iterable[str], keep_dirs: Iterable[str] = ()) -> List[str]:
    """Every file under ``directories``, skipping protected files and any subfolder that is
    one of ``keep_dirs`` (a folder another rule still uses)."""
    keep_keys = {_key(d) for d in keep_dirs if d}
    files = []
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for root, subdirs, names in os.walk(directory):
            subdirs[:] = [s for s in subdirs if _key(os.path.join(root, s)) not in keep_keys]
            for name in names:
                path = os.path.join(root, name)
                if not protected.is_protected(path):
                    files.append(path)
    return files


def remove_empty_folders(directories: Iterable[str]) -> int:
    """Remove each directory and its subfolders once they are empty. Returns how many went."""
    removed = 0
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for dirpath, _, _ in os.walk(directory, topdown=False):
            try:
                if not any(Path(dirpath).iterdir()):
                    os.rmdir(dirpath)
                    removed += 1
            except OSError as error:
                logger.warning("Could not remove empty folder %s: %s", dirpath, error)
    return removed


def move_files_to_queue(files: Iterable[str], destination: str, db_manager=None,
                        logger_obj: Optional[logging.Logger] = None) -> Tuple[int, List[str]]:
    """Move ``files`` into ``destination`` (renaming on a name clash).

    Returns the number moved and the files that could not be moved.
    """
    os.makedirs(destination, exist_ok=True)
    moved, failed = 0, []
    for path in files:
        target = unique_destination(Path(destination) / os.path.basename(path))
        try:
            move_file_with_logging(path, str(target), db_manager=db_manager,
                                   logger_obj=logger_obj or logger, reason=MOVE_REASON)
            moved += 1
        except Exception as error:
            logger.warning("Could not move %s: %s", path, error)
            failed.append(path)
    return moved, failed
