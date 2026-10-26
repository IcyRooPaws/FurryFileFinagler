"""Moving a file whose name has a stale duplicate row must not hit the unique file_path key."""
import sqlite3

import pytest

from fff.sorter.handlers.database_manager import DatabaseManager
from fff.sorter.handlers.utils import normalize_path


class _Conn:
    """sqlite stand-in for a pooled MySQL connection (%s placeholders, close() keeps data)."""

    def __init__(self, db):
        self.db = db

    def cursor(self):
        db = self.db

        class Cursor:
            def execute(self, sql, params=()):
                self._cur = db.execute(sql.replace('%s', '?'), params)

            def fetchall(self):
                return self._cur.fetchall()

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.db.commit()

    def close(self):
        pass


@pytest.fixture
def manager():
    db = sqlite3.connect(':memory:')
    db.execute("CREATE TABLE nsfw_image (id INTEGER PRIMARY KEY, file_name TEXT, file_path TEXT UNIQUE, "
               "perceptual_hash_phash TEXT, perceptual_hash_dhash TEXT, perceptual_hash_average TEXT, "
               "perceptual_hash_whash TEXT, md5_hash TEXT, file_created_at TEXT)")
    dm = object.__new__(DatabaseManager)
    dm.get_db_connection = lambda: _Conn(db)
    dm.ensure_dest_table = lambda table: None
    dm.rows = lambda: sorted(db.execute("SELECT file_name, file_path FROM nsfw_image").fetchall())
    dm.add = lambda name, path: db.execute(
        "INSERT INTO nsfw_image (file_name, file_path) VALUES (?, ?)", (name, normalize_path(str(path))))
    return dm


def _file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')
    return path


def test_move_with_stale_duplicate_keeps_one_row(manager, tmp_path):
    # falcrus case: a stale row (file gone) and a row for the file that was just moved
    old, src, dst = tmp_path / 'Gryphon Feral/a.jpg', tmp_path / 'Ferals/a.jpg', _file(tmp_path / 'Gryphon/a.jpg')
    manager.add('a.jpg', old)
    manager.add('a.jpg', src)

    assert manager.update_file_path('nsfw_image', 'a.jpg', str(dst))
    assert manager.rows() == [('a.jpg', normalize_path(str(dst)))]


def test_move_keeps_rows_for_copies_still_on_disk(manager, tmp_path):
    copy, src, dst = _file(tmp_path / 'Ferals/a.jpg'), tmp_path / 'In/a.jpg', _file(tmp_path / 'Gryphon/a.jpg')
    manager.add('a.jpg', copy)
    manager.add('a.jpg', src)

    manager.update_file_path('nsfw_image', 'a.jpg', str(dst))
    assert manager.rows() == sorted([('a.jpg', normalize_path(str(copy))), ('a.jpg', normalize_path(str(dst)))])


def test_move_onto_existing_row_drops_the_stale_one(manager, tmp_path):
    src, dst = tmp_path / 'Old/a.jpg', _file(tmp_path / 'New/a.jpg')
    manager.add('a.jpg', src)
    manager.add('a.jpg', dst)

    assert manager.update_file_path('nsfw_image', 'a.jpg', str(dst))
    assert manager.rows() == [('a.jpg', normalize_path(str(dst)))]


def test_unknown_file_is_not_found(manager, tmp_path):
    assert not manager.update_file_path('nsfw_image', 'nope.jpg', str(tmp_path / 'nope.jpg'))


def test_cleanup_removes_stale_duplicates_only(manager, tmp_path):
    live = _file(tmp_path / 'Gryphon/a.jpg')
    manager.add('a.jpg', tmp_path / 'Gone/a.jpg')
    manager.add('a.jpg', live)
    manager.add('b.jpg', tmp_path / 'Unmounted/b.jpg')  # single row: name-based rule decides

    removed = manager.cleanup_missing_files('nsfw_image', {'a.jpg', 'b.jpg'})
    assert removed == 1
    assert manager.rows() == sorted([('a.jpg', normalize_path(str(live))),
                                     ('b.jpg', normalize_path(str(tmp_path / 'Unmounted/b.jpg')))])
