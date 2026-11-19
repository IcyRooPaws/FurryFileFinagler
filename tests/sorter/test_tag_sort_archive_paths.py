"""Tag sorting writes a moved file's new location through to e621_posts_db (posts.file_path).

Before this, only the sorter's own tables were updated: a post ingested by ``fff e621 sync`` and
then tag-sorted by the next ``fff sort workflow`` kept its pre-sort path until a later sync.
"""
import os
import re
from types import SimpleNamespace

import pytest

from fff.common.fileops import normalize_file_path
from fff.sorter.features.tag_sorter import TagSorter


def _stored(path) -> str:
    """``path`` as posts.file_path holds it."""
    return normalize_file_path(os.path.normpath(str(path)))


class _Posts:
    """Stand-in for an e621_posts_db connection: just the three statements the sync runs."""

    def __init__(self, rows):
        self.rows = dict(rows)      # post id -> file_path
        self.commits = self.closed = 0
        self.fail_updates = False

    def cursor(self):
        posts = self

        class Cursor:
            def execute(self, sql, params=()):
                params = list(params)
                if sql.startswith("SELECT id, file_path FROM posts WHERE file_path IN"):
                    # MySQL's default collation compares case-insensitively.
                    wanted = {p.lower() for p in params}
                    self.result = [(i, p) for i, p in posts.rows.items() if p and p.lower() in wanted]
                elif sql.startswith("SELECT id, file_path FROM posts WHERE id IN"):
                    self.result = [(i, posts.rows[i]) for i in params if i in posts.rows]
                elif sql.startswith("UPDATE posts SET file_path"):
                    if posts.fail_updates:
                        from mysql.connector import Error
                        raise Error("Data too long for column 'file_path'")
                    posts.rows[params[1]] = params[0]
                else:
                    raise AssertionError(f"unexpected statement: {sql}")

            def fetchall(self):
                return self.result

            def close(self):
                pass

        return Cursor()

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed += 1


@pytest.fixture
def sorter(tmp_path):
    ts = object.__new__(TagSorter)
    ts.config = SimpleNamespace(duplicate_directory=str(tmp_path / "dups"))
    ts.db_manager = None
    ts.filename_pattern = re.compile(r'^(.+)-(explicit|questionable|safe)-(\d+)(\.\w+)$', re.IGNORECASE)
    ts._resolve_destination_for_file = lambda *a: str(tmp_path / "Tag Sorted" / "Vore")
    ts._apply_post_date = lambda *a: False
    ts.posts = _Posts({})
    ts.connect_to_e621_db = lambda: ts.posts
    return ts


def _file(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _sort(ts, *paths, **kwargs):
    return ts.move_files_to_destination({p.name: str(p) for p in paths}, None, {}, "Vore", **kwargs)


def test_a_tag_sorted_post_gets_its_new_path(sorter, tmp_path):
    src = _file(tmp_path / "NSFW" / "awoogasm-questionable-6857241.jpg")
    # Recorded the way the archive sync writes it, whichever slashes the sorter then uses.
    sorter.posts.rows[6857241] = _stored(src)
    moved, _ = _sort(sorter, src)
    dst = tmp_path / "Tag Sorted" / "Vore" / src.name
    assert moved == 1 and dst.exists()
    assert sorter.posts.rows[6857241] == _stored(dst)
    assert sorter.posts.commits == 1 and sorter.posts.closed == 1


def test_forward_slash_and_differently_cased_paths_still_match(sorter, tmp_path):
    src = _file(tmp_path / "NSFW" / "artist-explicit-11.png")
    sorter.posts.rows[11] = str(src).replace("\\", "/").upper()
    _sort(sorter, src)
    # On Windows the old path matches whatever its case; elsewhere the upper-cased path is a
    # missing file, so the post id in the file name finds the post.
    assert sorter.posts.rows[11] == _stored(tmp_path / "Tag Sorted" / "Vore" / src.name)


def test_a_post_whose_recorded_file_is_missing_is_found_by_the_id_in_the_file_name(sorter, tmp_path):
    src = _file(tmp_path / "NSFW" / "artist-safe-22.png")
    sorter.posts.rows[22] = _stored(tmp_path / "long gone" / "artist-safe-22.png")
    sorter.posts.rows[23] = None
    other = _file(tmp_path / "NSFW" / "artist-safe-23.png")
    _sort(sorter, src, other)
    assert sorter.posts.rows[22] == _stored(tmp_path / "Tag Sorted" / "Vore" / src.name)
    assert sorter.posts.rows[23] == _stored(tmp_path / "Tag Sorted" / "Vore" / other.name)


def test_a_post_pointing_at_another_copy_that_exists_is_left_alone(sorter, tmp_path):
    kept = _file(tmp_path / "Fav" / "artist-safe-33.png")
    sorter.posts.rows[33] = _stored(kept)
    _sort(sorter, _file(tmp_path / "NSFW" / "artist-safe-33.png"))
    assert sorter.posts.rows[33] == _stored(kept)


def test_a_file_without_a_post_id_is_matched_by_its_old_path_only(sorter, tmp_path):
    src = _file(tmp_path / "NSFW" / "Zuruzuking - SmergZuruPads.png")
    sorter.posts.rows[6163718] = _stored(src)
    sorter.posts.rows[44] = _stored(tmp_path / "missing" / "unrelated.png")
    _sort(sorter, src)
    assert sorter.posts.rows[6163718] == _stored(tmp_path / "Tag Sorted" / "Vore" / src.name)
    assert sorter.posts.rows[44] == _stored(tmp_path / "missing" / "unrelated.png")


def test_a_newer_source_sent_to_duplicates_repoints_the_post_at_the_kept_copy(sorter, tmp_path):
    dst = _file(tmp_path / "Tag Sorted" / "Vore" / "artist-safe-55.png", b"old")
    src = _file(tmp_path / "NSFW" / "artist-safe-55.png", b"new")
    os.utime(dst, (1_000_000, 1_000_000))
    sorter.posts.rows[55] = _stored(src)
    _sort(sorter, src)
    assert dst.read_bytes() == b"old" and (tmp_path / "dups" / src.name).exists()
    assert sorter.posts.rows[55] == _stored(dst)


def test_dry_run_does_not_touch_the_archive_database(sorter, tmp_path):
    src = _file(tmp_path / "NSFW" / "artist-safe-66.png")
    sorter.posts.rows[66] = _stored(src)
    sorter.connect_to_e621_db = lambda: pytest.fail("dry run connected to e621_posts_db")
    _sort(sorter, src, dry_run=True)
    assert src.exists() and sorter.posts.rows[66] == _stored(src)


def test_nothing_moved_means_no_connection(sorter, tmp_path):
    already = _file(tmp_path / "Tag Sorted" / "Vore" / "artist-safe-77.png")
    sorter.connect_to_e621_db = lambda: pytest.fail("connected with nothing to update")
    assert _sort(sorter, already) == (0, 0)


def test_a_database_failure_never_fails_the_sort(sorter, tmp_path, caplog):
    src = _file(tmp_path / "NSFW" / "artist-safe-88.png")
    sorter.posts.rows[88] = _stored(src)
    sorter.posts.fail_updates = True
    assert _sort(sorter, src)[0] == 1
    assert "Could not update posts.file_path for post 88" in caplog.text

    def unreachable():
        raise RuntimeError("server gone")

    sorter.connect_to_e621_db = unreachable
    other = _file(tmp_path / "NSFW" / "artist-safe-89.png")
    assert _sort(sorter, other)[0] == 1
    assert "Could not update posts.file_path for 1 moved file(s)" in caplog.text
