import os
from pathlib import Path

from fff.common import fileops, timestamps


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


def test_set_file_timestamps(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"x")
    timestamps.set_file_timestamps(str(f), mtime=1_600_000_000, ctime=1_500_000_000)
    assert f.stat().st_mtime == 1_600_000_000
    if os.name == "nt":
        st = f.stat()
        # st_birthtime exists on Windows from Python 3.12; before that, st_ctime is the creation time.
        assert int(getattr(st, "st_birthtime", st.st_ctime)) == 1_500_000_000


def test_path_normalizers():
    assert fileops.normalize_path("P:\\IPL\\a\\..\\b.png") == "P:/IPL/b.png"
    expected = "P:\\IPL\\b.png" if os.name == "nt" else "P:/IPL/b.png"
    assert fileops.normalize_file_path("P:/IPL/b.png") == expected


def test_restrict_to_owner_limits_the_file_to_this_account(tmp_path):
    secret = tmp_path / "secrets.yaml"
    secret.write_text("x: 1\n", encoding="utf-8")
    assert fileops.restrict_to_owner(secret)
    assert secret.read_text(encoding="utf-8") == "x: 1\n"  # still readable by us
    if os.name == "nt":
        import subprocess
        acl = subprocess.run(["icacls", str(secret)], capture_output=True, text=True).stdout
        acl = acl.replace(str(secret), "")  # the temp path itself contains C:\Users
        assert "(I)" not in acl  # nothing inherited from the folder
        assert "Users" not in acl and "Everyone" not in acl
        assert "NT AUTHORITY\\SYSTEM:(F)" in acl
    else:
        assert secret.stat().st_mode & 0o077 == 0


def test_restrict_to_owner_skips_missing_files(tmp_path):
    assert fileops.restrict_to_owner(tmp_path / "missing.yaml") is False
