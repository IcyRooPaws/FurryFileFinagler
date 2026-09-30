import os
from pathlib import Path

import pytest

from fff.common import fileops, timestamps
from tests import existing code
from tests.existing code import requires_existing code


def _fluffle_movers():
    return [
        existing code.fluffle_e621().move_to_blacklisted,
        existing code.fluffle_e621().move_to_source_found,
        existing code.fluffle_fa().move_to_source_found,
        existing code.fluffle_fa().move_to_not_found,
        existing code.fluffle_main().move_to_not_found,
    ]


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _scenario(root: Path, mover, preserve, collide, outside):
    base = root / "in"
    src = (root / "elsewhere" / "pic.png") if outside else (base / "a" / "b" / "pic.png")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"x")
    target = root / "out"
    if collide:
        existing = target / ("a/b" if preserve and not outside else "") / "pic.png"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"old")
        existing.with_name("pic_1.png").write_bytes(b"old")
    ok = mover(src, target, base, preserve)
    return ok, _tree(target), src.exists()


@requires_existing code
@pytest.mark.parametrize("preserve", [False, True])
@pytest.mark.parametrize("collide", [False, True])
@pytest.mark.parametrize("outside", [False, True])
def test_move_to_directory_matches_all_five_fluffle_copies(tmp_path, preserve, collide, outside):
    expected = _scenario(tmp_path / "new", fileops.move_to_directory, preserve, collide, outside)
    for i, mover in enumerate(_fluffle_movers()):
        assert _scenario(tmp_path / f"orig{i}", mover, preserve, collide, outside) == expected, mover


@requires_existing code
def test_move_to_directory_missing_source_returns_false_like_original(tmp_path):
    for i, mover in enumerate(_fluffle_movers() + [fileops.move_to_directory]):
        assert mover(tmp_path / "nope.png", tmp_path / f"out{i}") is False


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def close(self):
        pass


class FakeConn:
    def __init__(self, log, fail=None):
        self.log, self.fail = log, fail

    def cursor(self):
        if self.fail:
            raise self.fail
        return FakeCursor(self.log)

    def commit(self):
        pass

    def close(self):
        pass


def test_operation_logger_rows(tmp_path):
    rows = []
    op = fileops.OperationLogger(connect=lambda: FakeConn(rows), reason_prefix="fluffle")
    src = tmp_path / "in" / "pic.png"
    src.parent.mkdir()
    src.write_bytes(b"12345")
    assert fileops.move_to_directory(src, tmp_path / "out", db_manager=op, reason="not_found")
    (sql, params), = rows
    assert sql.startswith("INSERT INTO file_operations")
    assert params[0] == "move" and params[1] == "pic.png"
    assert params[2] == fileops.normalize_path(str(src)) and "\\" not in params[3]
    assert params[6] == 5 and params[7] == "success" and params[9] == "fluffle:not_found"


def test_delete_and_rename_logging_no_longer_crash(tmp_path):
    """The original DatabaseManager.log_delete/rename_operation raised TypeError (stale file_hash kwarg)."""
    rows = []
    op = fileops.OperationLogger(connect=lambda: FakeConn(rows))
    f = tmp_path / "a.png"
    f.write_bytes(b"1")
    assert fileops.rename_file_with_logging(f, tmp_path / "b.png", db_manager=op, reason="rename")
    assert fileops.delete_file_with_logging(str(tmp_path / "b.png"), db_manager=op, file_hash="ignored")
    assert [r[1][0] for r in rows] == ["rename", "delete"]
    assert not (tmp_path / "b.png").exists()


def test_operation_logger_never_raises(tmp_path):
    op = fileops.OperationLogger(connect=lambda: FakeConn([], fail=RuntimeError("db down")))
    op.log_move_operation("x", "a", "b")  # logged as a warning, not raised


def test_rename_across_folders_is_logged_as_move(tmp_path):
    rows = []
    op = fileops.OperationLogger(connect=lambda: FakeConn(rows))
    f = tmp_path / "a.png"
    f.write_bytes(b"1")
    (tmp_path / "sub").mkdir()
    fileops.rename_file_with_logging(f, tmp_path / "sub" / "a.png", db_manager=op)
    assert rows[0][1][0] == "move"


@requires_existing code
@pytest.mark.parametrize("keep_atime,original", [(False, "fluffle_e621"), (True, "fluffle_fa")])
def test_copy_file_times_matches_original_variants(tmp_path, keep_atime, original):
    src = tmp_path / "src"
    src.write_bytes(b"s")
    os.utime(src, (1_000_000_000, 1_500_000_000))
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    getattr(existing code, original)().copy_file_times(src, a)
    timestamps.copy_file_times(src, b, keep_source_atime=keep_atime)
    assert (a.stat().st_atime, a.stat().st_mtime) == (b.stat().st_atime, b.stat().st_mtime)


def test_set_file_timestamps(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"x")
    timestamps.set_file_timestamps(str(f), mtime=1_600_000_000, ctime=1_500_000_000)
    assert f.stat().st_mtime == 1_600_000_000
    if os.name == "nt":
        assert int(f.stat().st_birthtime) == 1_500_000_000


def test_path_normalizers():
    assert fileops.normalize_path("P:\\IPL\\a\\..\\b.png") == "P:/IPL/b.png"
    expected = "P:\\IPL\\b.png" if os.name == "nt" else "P:/IPL/b.png"
    assert fileops.normalize_file_path("P:/IPL/b.png") == expected
