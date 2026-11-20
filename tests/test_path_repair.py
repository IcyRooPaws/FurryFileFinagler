"""The posts.file_path repair never mistakes a missing library (drive offline, folder renamed,
sorter database down) for missing files, which would clear every path and queue the whole
library for download."""
import pytest

from fff.archive import db


class Conn:
    def __init__(self, rows):
        self.rows = rows                  # [(id, file_path, md5)]
        self.paths = {i: p for i, p, _ in rows}
        self.commits = self.rollbacks = 0

    def cursor(self):
        conn = self

        class Cursor:
            def execute(self, sql, params=()):
                assert sql.startswith("SELECT id, file_path, md5 FROM posts"), sql
                self.result = list(conn.rows)

            def executemany(self, sql, rows):
                for row in rows:
                    if sql == "UPDATE posts SET file_path = %s WHERE id = %s":
                        conn.paths[row[1]] = row[0]
                    elif sql == "UPDATE posts SET file_path = NULL WHERE id = %s":
                        conn.paths[row[0]] = None
                    else:
                        raise AssertionError(sql)

            def fetchall(self):
                return self.result

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def sorter(monkeypatch):
    """The sorter database: reachable, and knows the files in ``known`` (post id -> path)."""
    state = {"up": [True], "known": {}, "lookups": 0}

    class TestConn:
        def close(self):
            pass

    def connect(cfg):
        up = state["up"].pop(0) if len(state["up"]) > 1 else state["up"][0]
        return TestConn() if up else None

    def by_id(cfg, post_id):
        state["lookups"] += 1
        return state["known"].get(post_id)

    monkeypatch.setattr(db, "connect_to_image_sorting_hashes_db", connect)
    monkeypatch.setattr(db, "get_file_path_from_image_sorting_hashes_by_post_id", by_id)
    monkeypatch.setattr(db, "get_file_path_from_image_sorting_hashes", lambda cfg, md5: None)
    return state


def library(tmp_path, present, gone):
    """Posts 1..present have their file; the next ``gone`` posts' files are missing."""
    folder = tmp_path / "Library" / "Pictures"
    folder.mkdir(parents=True)
    rows = []
    for i in range(1, present + gone + 1):
        path = folder / f"a-safe-{i}.png"
        if i <= present:
            path.write_bytes(b"x")
        rows.append((i, str(path), f"md5-{i}"))
    return rows


def run(conn, **kw):
    return db.verify_and_repair_file_paths(conn, {"host": "x"}, **kw)


def test_files_that_really_went_away_are_repaired_or_cleared(tmp_path, sorter):
    conn = Conn(library(tmp_path, present=500, gone=3))
    moved = tmp_path / "Library" / "Pictures" / "Tag Sorted"
    moved.mkdir()
    (moved / "a-safe-501.png").write_bytes(b"x")
    sorter["known"][501] = str(moved / "a-safe-501.png")
    stats = run(conn)
    assert (stats["checked"], stats["missing"], stats["repaired"], stats["not_found"]) == (503, 3, 1, 2)
    assert conn.paths[501].endswith("a-safe-501.png") and "Tag Sorted" in conn.paths[501]
    assert conn.paths[502] is None and conn.paths[503] is None
    assert not stats["aborted"]


def test_an_unplugged_drive_or_renamed_top_folder_changes_nothing(tmp_path, sorter, caplog):
    import os
    rows = library(tmp_path, present=10, gone=0)
    # A path whose top folder does not exist, like P:\IPL\... with P: unplugged.
    top = os.path.join(os.path.splitdrive(str(tmp_path))[0] + os.sep, f"fff-test-unplugged-{tmp_path.name}")
    assert not os.path.exists(top)
    elsewhere = os.path.join(top, "Pictures", "b-safe-99.png")
    rows.append((99, str(elsewhere), "md5"))
    conn = Conn(rows)
    stats = run(conn)
    assert stats["skipped"] == 1 and stats["not_found"] == 0 and conn.paths[99] == elsewhere
    assert sorter["lookups"] == 0
    assert "is not reachable; leaving the paths under it as they are" in caplog.text


def test_too_many_missing_files_stop_the_repair_before_it_changes_anything(tmp_path, sorter, caplog):
    """A library folder emptied or renamed under a top folder that still exists."""
    conn = Conn(library(tmp_path, present=100, gone=db.MAX_MISSING_PATHS + 1))
    before = dict(conn.paths)
    stats = run(conn)
    assert stats["aborted"] and conn.paths == before and conn.commits == 0 and sorter["lookups"] == 0
    assert "more than the 200 a run will repair" in caplog.text


def test_the_limit_grows_with_the_library(tmp_path, sorter):
    # 2% of 20,000 checked is 400: 300 missing is believed.
    rows = library(tmp_path, present=1, gone=300)
    rows += [(10_000 + i, rows[0][1], "m") for i in range(19_699)]      # 19,699 more posts sharing the file that exists
    stats = run(Conn(rows))
    assert not stats["aborted"] and stats["not_found"] == 300


def test_a_sorter_database_that_stops_answering_clears_nothing(tmp_path, sorter, caplog):
    sorter["up"] = [True, False]          # up at the start, down by the end
    conn = Conn(library(tmp_path, present=5, gone=2))
    stats = run(conn)
    assert stats["not_found"] == 0 and stats["skipped"] == 2
    assert all(path for path in conn.paths.values())
    assert "stopped answering" in caplog.text


def test_keep_missing_and_dry_run_never_clear(tmp_path, sorter):
    conn = Conn(library(tmp_path, present=5, gone=2))
    assert run(conn, clear_missing=False)["not_found"] == 2
    assert all(conn.paths.values())
    assert run(conn, dry_run=True)["not_found"] == 2
    assert all(conn.paths.values()) and conn.commits == 1       # only the clear_missing=False run wrote (nothing)


def test_a_file_with_another_extension_is_adopted(tmp_path, sorter):
    rows = library(tmp_path, present=0, gone=1)
    actual = tmp_path / "Library" / "Pictures" / "a-safe-1.webm"
    actual.write_bytes(b"x")
    conn = Conn(rows)
    assert run(conn)["repaired"] == 1 and conn.paths[1].endswith("a-safe-1.webm")


def test_library_root():
    import os
    assert db._library_root(os.path.join(os.sep, "IPL", "Pictures", "a.png")).rstrip(os.sep).endswith("IPL")


def test_the_command_uses_the_shared_repair(monkeypatch):
    from fff.archive.scripts.database import verify_and_repair_file_paths as command

    seen = {}

    class C:
        def close(self):
            pass

    monkeypatch.setattr(command, "load_config", lambda: {"mysql": {"host": "x"}, "log": {"directory": "logs/archive"}})
    monkeypatch.setattr(command, "setup_logging", lambda d: None)
    monkeypatch.setattr(command, "connect_to_mysql", lambda cfg: C())
    monkeypatch.setattr(command, "verify_and_repair_file_paths",
                        lambda conn, cfg, **kw: seen.update(kw) or {"aborted": True})
    with pytest.raises(SystemExit) as stopped:
        command.main(["--keep-missing", "--dry-run"])
    assert stopped.value.code == 1
    assert seen == {"batch_limit": None, "clear_missing": False, "dry_run": True}
