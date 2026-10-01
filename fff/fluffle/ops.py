"""reverse search's file operations, logged to the log file and ``file_operations``.

Every move, copy, rename and delete reverse search does on a media file goes through here, recorded
in the sorter database's file_operations table with reason ``fluffle:<step>``. Each helper
behaves like the shutil/os call it replaces (and raises the same way on failure), so the
ported code's own try/except blocks still decide what happens next.
"""
import logging
from pathlib import Path

from fff.common import fileops

logger = logging.getLogger("fff.fluffle")
_ops = None


def operation_logger():
    global _ops
    if _ops is None:
        _ops = fileops.OperationLogger(reason_prefix="fluffle")
    return _ops


def move(src, dest, reason):
    return fileops.move_file_with_logging(str(src), str(dest), db_manager=operation_logger(), logger_obj=logger,
                                          source_directory=str(Path(src).parent),
                                          destination_directory=str(Path(dest).parent), reason=reason)


def copy(src, dest, reason):
    return fileops.copy_file_with_logging(str(src), str(dest), db_manager=operation_logger(), logger_obj=logger,
                                          source_directory=str(Path(src).parent),
                                          destination_directory=str(Path(dest).parent), reason=reason)


def rename(src, dest, reason):
    return fileops.rename_file_with_logging(src, dest, db_manager=operation_logger(), logger_obj=logger, reason=reason)


def delete(path, reason):
    return fileops.delete_file_with_logging(str(path), db_manager=operation_logger(), logger_obj=logger,
                                            source_directory=str(Path(path).parent), reason=reason)


def move_to_directory(image_path, target_dir, base_search_dir=None, preserve_structure=False, *,
                      reason, error_label="Error moving file"):
    return fileops.move_to_directory(Path(image_path), Path(target_dir),
                                     Path(base_search_dir) if base_search_dir else None, preserve_structure,
                                     db_manager=operation_logger(), reason=reason, error_label=error_label)
