# fff: implemented in media sorter/tests/test_move_logging.py.
# fff: the test was stale against the current implementation (its dummy lacked the logging methods and it
# fff: expected an old log format); both fixed, see the comments. It now passes on the current implementation too.
import logging
from pathlib import Path

from fff.sorter.handlers.file_operations import FileOperations


class DummyConfig:
    def __init__(self, base_dir: Path):
        self.scan_subdirectories = False
        self.duplicate_directory = str(base_dir / "duplicates")
        self.manual_sort_directory = str(base_dir / "manual_sort")
        self.manual_sort_video_directory = str(base_dir / "manual_sort_videos")
        self.manual_sort_gif_directory = str(base_dir / "manual_sort_gifs")
        self.destination_directories = {
            "sfw_image": str(base_dir / "sfw_images"),
        }


class DummyDbManager:
    # fff: the original dummy predates file_operations logging, so this test failed against the
    # current implementation too ('DummyDbManager' object has no attribute 'log_move_operation').
    def log_move_operation(self, *args, **kwargs):
        pass

    def log_failed_operation(self, *args, **kwargs):
        pass


def test_non_recursive_sfw_moves_are_counted_once_and_logged(tmp_path, caplog):
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    moved_file = source_dir / "sample-safe.png"
    moved_file.write_bytes(b"image")

    skipped_file = source_dir / "notes.txt"
    skipped_file.write_text("ignore me", encoding="utf-8")

    nested_dir = source_dir / "nested"
    nested_dir.mkdir()

    config = DummyConfig(tmp_path)
    operations = FileOperations(config, DummyDbManager())

    with caplog.at_level(logging.INFO):
        moved_count = operations.move_sfw_images_to_sfw_dir([str(source_dir)])

    destination_path = tmp_path / "sfw_images" / moved_file.name

    assert moved_count == 1
    assert destination_path.exists()
    assert not moved_file.exists()
    assert skipped_file.exists()

    # fff: updated to the current log format ("Moved file (file): 'src' -> 'dst'", forward slashes);
    # the original expected "Moved file (sfw_image):" with backslashes and failed against its own code.
    move_logs = [record.getMessage() for record in caplog.records if "Moved file (file):" in record.getMessage()]
    assert len(move_logs) == 1
    assert str(moved_file).replace("\\", "/") in move_logs[0]
    assert str(destination_path).replace("\\", "/") in move_logs[0]