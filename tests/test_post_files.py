"""Removing a post moves every local copy of its file, not just posts.file_path."""
import pytest

from fff.archive import post_files

SORTER = "image_sorting_hashes"


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows = db, []

    def execute(self, sql, params=()):
        params = list(params)
        if sql.startswith("SELECT id, file_path, md5 FROM posts"):
            self.rows = [(i, *self.db.posts[i]) for i in params if i in self.db.posts]
        elif "WHERE e621_post_id IN" in sql:
            self.rows = [(pid, path, t) for (pid, md5, path, t) in self.db.files if pid in params]
        elif "WHERE md5_hash IN" in sql:
            self.rows = [(md5, path, t) for (pid, md5, path, t) in self.db.files if md5 in params]
        elif sql.startswith("DELETE FROM"):
            self.db.deleted_rows.append((sql.split("`")[3], params[0]))
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, posts, files):
        self.posts = posts            # id -> (file_path, md5)
        self.files = files            # [(e621_post_id, md5, file_path, table)]
        self.deleted_rows = []

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    class Ops:
        def log_move_operation(self, **kw): pass
        def log_failed_operation(self, **kw): pass
        def log_delete_operation(self, **kw): pass

    monkeypatch.setattr(post_files, "_operation_logger", lambda: Ops())
    monkeypatch.setattr(post_files.archive_paths, "db_names", lambda: {"sorter": SORTER, "archive": "e621_posts_db"})


def make(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_stale_file_path_still_moves_the_real_file(tmp_path):
    # Downloaded to the inbox, then the sorter moved it; posts.file_path still points at the inbox.
    real = make(tmp_path / "Fav" / "Artists" / "a-explicit-1.jpg")
    conn = FakeConn({1: (str(tmp_path / "inbox" / "a-explicit-1.jpg"), "abc")},
                    [(1, "abc", real.as_posix(), "favorites")])
    out = tmp_path / "Unfavorited"
    result = post_files.move_post_files(conn, [1], out, reason="unfavorited")
    assert result == {"moved": 1, "failed": 0, "cleared": {1}}
    assert not real.exists() and (out / "a-explicit-1.jpg").exists()
    assert conn.deleted_rows == [("favorites", real.as_posix())]


def test_every_copy_moves_once(tmp_path):
    a = make(tmp_path / "Tag Sorted" / "b-explicit-2.png", "1")
    b = make(tmp_path / "Fav" / "b-explicit-2.png", "2")
    renamed = make(tmp_path / "Other" / "renamed.png", "3")   # found only by md5
    conn = FakeConn({2: (str(a).replace("/", "\\"), "m2")},   # same file as a, other spelling
                    [(2, "m2", a.as_posix(), "nsfw_image"), (2, "m2", b.as_posix(), "favorites"),
                     (None, "m2", renamed.as_posix(), "other_directories")])
    out = tmp_path / "Unfavorited"
    result = post_files.move_post_files(conn, [2], out, reason="unfavorited")
    assert result["moved"] == 3 and result["cleared"] == {2}
    assert sorted(p.name for p in out.iterdir()) == ["b-explicit-2.png", "b-explicit-2_1.png", "renamed.png"]


def test_extension_change_is_found(tmp_path):
    real = make(tmp_path / "lib" / "c-safe-3.webm")
    conn = FakeConn({3: (str(tmp_path / "lib" / "c-safe-3.mp4"), None)}, [])
    result = post_files.move_post_files(conn, [3], tmp_path / "out", reason="unfavorited")
    assert result["moved"] == 1 and not real.exists()


def test_post_without_files_is_cleared(tmp_path):
    conn = FakeConn({4: (None, None)}, [])
    assert post_files.move_post_files(conn, [4], tmp_path / "out", reason="x")["cleared"] == {4}


def test_failed_move_keeps_the_post(tmp_path, monkeypatch):
    make(tmp_path / "lib" / "d-safe-5.png")
    conn = FakeConn({5: (str(tmp_path / "lib" / "d-safe-5.png"), None)}, [])

    def boom(*a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(post_files.fileops, "move_file_with_logging", boom)
    result = post_files.move_post_files(conn, [5], tmp_path / "out", reason="x")
    assert result["failed"] == 1 and result["cleared"] == set()


def test_dry_run_moves_nothing(tmp_path):
    real = make(tmp_path / "lib" / "e-safe-6.png")
    conn = FakeConn({6: (str(real), None)}, [(6, None, real.as_posix(), "sfw_image")])
    result = post_files.move_post_files(conn, [6], tmp_path / "out", reason="x", dry_run=True)
    assert result["moved"] == 1 and real.exists() and not (tmp_path / "out").exists()
    assert conn.deleted_rows == []


def test_orphan_without_posts_row_found_through_sorter(tmp_path):
    real = make(tmp_path / "Fav" / "f-explicit-7.mp4")
    conn = FakeConn({}, [(7, None, real.as_posix(), "favorites")])
    assert post_files.find_post_files(conn, [7]) == {7: [(real.as_posix(), "favorites")]}


def test_trash_uses_recycle_bin(tmp_path, monkeypatch):
    real = make(tmp_path / "lib" / "g-safe-8.png")
    trashed = []
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda p: trashed.append(p))
    conn = FakeConn({8: (str(real), None)}, [])
    assert post_files.trash_post_files(conn, [8], reason="x")["trashed"] == 1
    assert trashed == [str(real)]
