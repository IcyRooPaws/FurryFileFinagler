"""An incoming file whose content is already anywhere in the library goes to the duplicates folder
(normal sort and tag sort); files already in the library are never treated as incoming."""
import sqlite3
from types import SimpleNamespace

import pytest

from fff.sorter.features.tag_sorter import TagSorter
from fff.sorter.handlers import file_operations
from fff.sorter.handlers.database_manager import DatabaseManager
from fff.sorter.handlers.file_operations import FileOperations

HASHES = ("AVG", "DH", "PH", "WH")
COMPOSITE = "|".join(HASHES)


class _Conn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        db = self.db

        class Cursor:
            def execute(self, sql, params=()):
                self._cur = db.execute(sql.replace("%s", "?"), params)

            def fetchall(self):
                return self._cur.fetchall()

            def fetchone(self):
                return self._cur.fetchone()

            def close(self):
                pass

        return Cursor()

    def close(self):
        pass


def _file(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def manager():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE all_files (table_name TEXT, file_name TEXT, file_path TEXT, md5_hash TEXT, "
               "perceptual_hash_average TEXT, perceptual_hash_dhash TEXT, perceptual_hash_phash TEXT, "
               "perceptual_hash_whash TEXT)")
    dm = object.__new__(DatabaseManager)
    dm.database = "main"          # sqlite's name for the in-memory database
    dm._pool = None
    dm.get_db_connection = lambda: _Conn(db)
    dm.add = lambda table, path, md5=None, hashes=(None,) * 4: db.execute(
        "INSERT INTO all_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (table, path.name, path.as_posix(), md5, *hashes))
    return dm


def test_md5_match_in_another_table(manager, tmp_path):
    fav = _file(tmp_path / "Fav" / "a.png")
    manager.add("favorites", fav, md5="m1")
    found = manager.find_existing_copy(str(tmp_path / "inbox" / "a.png"), "m1", None)
    assert found["table"] == "favorites" and found["match"] == "md5"


def test_own_row_and_vanished_files_are_ignored(manager, tmp_path):
    incoming = _file(tmp_path / "inbox" / "a.png")
    manager.add("manual_sort_images", incoming, md5="m1")           # its own row
    manager.add("archive_sfw_images", tmp_path / "gone" / "a.png", md5="m1")   # file no longer exists
    assert manager.find_existing_copy(str(incoming), "m1", None) is None


def test_perceptual_match_only_for_still_images(manager, tmp_path):
    existing = _file(tmp_path / "lib" / "b.png")
    manager.add("nsfw_image", existing, md5="other", hashes=HASHES)
    assert manager.find_existing_copy(str(tmp_path / "in" / "b.jpg"), "m2", COMPOSITE)["match"] == "perceptual"
    video = _file(tmp_path / "lib" / "v.webm")
    manager.add("nsfw_video", video, md5="other2", hashes=HASHES)
    # Videos/GIFs collide on blank frames: only an MD5 match counts for them.
    assert manager.find_existing_copy(str(tmp_path / "in" / "w.webm"), "m3", COMPOSITE) is None
    assert manager.find_existing_copy(str(tmp_path / "in" / "w.webm"), "other2", COMPOSITE)["table"] == "nsfw_video"


def test_is_tracked_accepts_either_slash(manager, tmp_path):
    lib = _file(tmp_path / "lib" / "c.png")
    manager.add("sfw_image", lib)
    assert manager.is_tracked(str(lib).replace("/", "\\"))
    assert not manager.is_tracked(str(tmp_path / "inbox" / "c.png"))


class _FakeDb:
    """DatabaseManager stand-in for FileOperations/TagSorter: the real lookups, no-op logging."""

    def __init__(self, manager, dup_dir):
        self.manager, self.destination_directories, self.duplicate_directory = manager, {}, str(dup_dir)
        self.saved = []

    def __getattr__(self, name):
        if name.startswith("log_"):
            return lambda *a, **k: None
        raise AttributeError(name)

    def is_tracked(self, path):
        return self.manager.is_tracked(path)

    def find_existing_copy(self, *args):
        return self.manager.find_existing_copy(*args)

    def is_duplicate_in_dest(self, *a, **k):
        return None

    def save_file_hash_to_dest(self, *args):
        self.saved.append(args)


@pytest.fixture
def ops(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(file_operations, "hash_file", lambda path, logger=None: COMPOSITE)
    monkeypatch.setattr(file_operations, "compute_md5_hash", lambda path, logger=None: "m1")
    fo = object.__new__(FileOperations)
    fo.duplicate_directory = str(tmp_path / "dups")
    fo.db_manager = _FakeDb(manager, tmp_path / "dups")
    fo.use_advanced_dedupe = False
    fo.manual_sort_directory = fo.manual_sort_video_directory = str(tmp_path / "manual")
    (tmp_path / "nsfw").mkdir()   # move_files_single creates the destination before process_file
    return fo


def test_normal_sort_moves_an_incoming_duplicate_to_duplicates(ops, manager, tmp_path):
    existing = _file(tmp_path / "Fav" / "a.png", b"same")
    manager.add("favorites", existing, md5="m1")
    _file(tmp_path / "inbox" / "a.png", b"same")
    result = ops.process_file("a.png", str(tmp_path / "inbox"), str(tmp_path / "nsfw"), lambda f: True)
    assert result == (0, 1)
    assert (tmp_path / "dups" / "a.png").exists() and existing.exists()
    assert not (tmp_path / "nsfw" / "a.png").exists()


def test_normal_sort_moves_a_new_file_normally(ops, tmp_path):
    _file(tmp_path / "inbox" / "new.png")
    assert ops.process_file("new.png", str(tmp_path / "inbox"), str(tmp_path / "nsfw"), lambda f: True) == (1, 0)
    assert (tmp_path / "nsfw" / "new.png").exists()


def test_library_files_are_not_treated_as_incoming(ops, manager, tmp_path):
    """A file already in a hash table is a library file: a copy elsewhere is not handled here."""
    lib = _file(tmp_path / "inbox" / "lib.png", b"same")
    manager.add("manual_sort_images", lib, md5="m1")
    manager.add("favorites", _file(tmp_path / "Fav" / "lib.png", b"same"), md5="m1")
    assert ops.process_file("lib.png", str(tmp_path / "inbox"), str(tmp_path / "nsfw"), lambda f: True) == (1, 0)


def test_tag_sort_sends_incoming_duplicates_to_duplicates(manager, tmp_path, monkeypatch):
    from fff.sorter.features import tag_sorter

    monkeypatch.setattr(tag_sorter, "compute_md5_hash", lambda path, logger=None: "m1")
    monkeypatch.setattr(tag_sorter, "hash_file", lambda path, logger=None: COMPOSITE)
    existing = _file(tmp_path / "Archive" / "t.png", b"same")
    manager.add("archive_sfw_images", existing, md5="m1")
    incoming = _file(tmp_path / "inbox" / "t.png", b"same")
    fresh = _file(tmp_path / "inbox" / "fresh.png", b"new")
    ts = object.__new__(TagSorter)
    ts.config = SimpleNamespace(duplicate_directory=str(tmp_path / "dups"))
    ts.db_manager = _FakeDb(manager, tmp_path / "dups")
    ts._resolve_destination_for_file = lambda *a: str(tmp_path / "Tag Sorted" / "MLP")
    ts._apply_post_date = lambda *a: False
    monkeypatch.setattr(tag_sorter, "compute_md5_hash",
                        lambda path, logger=None: "m1" if path.endswith("t.png") else "fresh")

    ts.move_files_to_destination({"t.png": str(incoming), "fresh.png": str(fresh)}, None, {}, "MLP", dry_run=True)
    assert incoming.exists()   # dry run changes nothing
    moved, _ = ts.move_files_to_destination({"t.png": str(incoming), "fresh.png": str(fresh)}, None, {}, "MLP")
    assert moved == 1
    assert (tmp_path / "dups" / "t.png").exists() and existing.exists()
    assert (tmp_path / "Tag Sorted" / "MLP" / "fresh.png").exists()


def test_tag_sort_without_a_database_skips_the_check(tmp_path):
    ts = object.__new__(TagSorter)
    ts.db_manager = None
    assert ts._existing_copy_for_incoming(str(tmp_path / "x.png")) is None
