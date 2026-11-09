"""Database backups: literals, the file format, rotation, and (integration) a byte-exact round
trip of awkward values through a real database."""
import datetime as dt
import gzip
import json
import os
from decimal import Decimal

import pytest

from fff.common import backup


@pytest.mark.parametrize("value, sql", [
    (None, "NULL"), (True, "1"), (7, "7"), (Decimal("12.50"), "12.50"), (0.1, "0.1"),
    (b"\x00\xff", "X'00ff'"), (b"", "''"),
    (dt.datetime(2026, 1, 2, 3, 4, 5, 600), "'2026-01-02 03:04:05.000600'"),
    (dt.date(2026, 1, 2), "'2026-01-02'"),
    (dt.timedelta(hours=-1, minutes=30), "'-00:30:00.000000'"),
    ("it's a\\b\n;\n\x00\x1a", "'it\\'s a\\\\b\\n;\\n\\0\\Z'"),
    ({"b", "a"}, "'a,b'"),
])
def test_literal(value, sql):
    assert backup.literal(value) == sql


def test_literal_never_puts_a_raw_newline_in_data():
    assert "\n" not in backup.literal("line1\nline2\r\n;\n")


def _write_backup_file(path, blocks):
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as out:
        out.write("-- header comment\n")
        for block in blocks:
            backup._write(out, block)


def test_statements_split_on_lone_semicolon_lines(tmp_path):
    path = tmp_path / "x.sql.gz"
    ddl = "CREATE TABLE `t` (\n  `a` int\n)"
    _write_backup_file(path, [ddl, "INSERT INTO `t` VALUES ('a;b'),('-- not a comment')"])
    assert list(backup.statements(path)) == [ddl, "INSERT INTO `t` VALUES ('a;b'),('-- not a comment')"]


def test_truncated_file_is_reported(tmp_path):
    path = tmp_path / "x.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as out:
        out.write("INSERT INTO `t` VALUES (1)\n;\nINSERT INTO `t` VALUES (2)\n")
    with pytest.raises(ValueError, match="truncated"):
        list(backup.statements(path))


def _fake_backup(root, name, created):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"created": created, "databases": {}}))
    return folder


def test_prune_keeps_the_newest_and_clears_old_partials(tmp_path):
    for day in range(1, 6):
        _fake_backup(tmp_path, f"2026110{day}_000000_prod", f"2026-11-0{day}T00:00:00")
    (tmp_path / "20261102_000000_prod.partial").mkdir()      # an old run that never finished
    (tmp_path / "20261109_000000_prod.partial").mkdir()      # one that may still be running
    removed = backup.prune(keep=2, root=tmp_path)
    assert [p.name for p in backup.backups(tmp_path)] == ["20261104_000000_prod", "20261105_000000_prod"]
    assert len(removed) == 4
    assert (tmp_path / "20261109_000000_prod.partial").exists()


def test_newest_age(tmp_path):
    assert backup.newest_age_days(tmp_path) is None
    _fake_backup(tmp_path, "20261101_000000_prod", (dt.datetime.now() - dt.timedelta(days=3)).isoformat())
    assert 2.9 < backup.newest_age_days(tmp_path) < 3.1


def test_verify_detects_damage(tmp_path):
    folder = tmp_path / "b"
    folder.mkdir()
    data = folder / "e621_posts_db.sql.gz"
    _write_backup_file(data, ["SELECT 1"])
    (folder / "manifest.json").write_text(json.dumps({"databases": {"archive": {
        "file": data.name, "sha256": backup._sha256(data), "rows": {}}}}))
    assert backup.verify(folder) == []
    data.write_bytes(data.read_bytes()[:-3])
    assert "checksum" in backup.verify(folder)[0]
    os.remove(data)
    assert "missing" in backup.verify(folder)[0]


@pytest.mark.integration
def test_round_trip_is_exact_for_awkward_values(tmp_path):
    """Dump a scratch database with every column type FFF uses and tricky values, restore it
    into another scratch database, and compare the rows and CHECKSUM TABLE."""
    from fff.common import db

    src, dst = "test_fffbackup_src", "test_fffbackup_dst"
    conn = db.connect_server()
    cur = conn.cursor()
    try:
        for statement in backup.SESSION:
            cur.execute(statement)
        for name in (src, dst):
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        cur.execute(f"CREATE DATABASE `{src}` DEFAULT CHARACTER SET utf8mb4")
        cur.execute(f"""CREATE TABLE `{src}`.`t` (
            id INT AUTO_INCREMENT PRIMARY KEY, s VARCHAR(200), txt LONGTEXT, j JSON,
            d DECIMAL(10,2), f FLOAT, dbl DOUBLE, big BIGINT, tiny TINYINT(1), dt DATETIME(6),
            ts TIMESTAMP NULL, day DATE, c CHAR(3),
            g VARCHAR(220) GENERATED ALWAYS AS (CONCAT('x', s)) STORED)""")
        cur.execute(f"CREATE VIEW `{src}`.`v` AS SELECT id, s FROM `{src}`.`t`")
        rows = [
            ("it's \\ a \"test\"", "line1\n;\nline3\r\n\x1a", '{"a": [1, "x;y"]}', Decimal("-12.34"),
             0.123456789,  # a FLOAT MySQL sends rounded to 0.123457 (30 FLOAT steps off)
             1e300, 2**62, 1, dt.datetime(2026, 1, 2, 3, 4, 5, 123456), dt.datetime(2026, 6, 1, 12, 0, 0),
             dt.date(1999, 12, 31), "abc"),
            ("emoji 🐺 ünïcödé", "-- not a comment", None, None, None, None, None, 0, None, None, None, None),
        ]
        cur.executemany(f"INSERT INTO `{src}`.`t` (s, txt, j, d, f, dbl, big, tiny, dt, ts, day, c) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
        conn.commit()

        path = tmp_path / "src.sql.gz"
        counts = backup._dump_database(conn, src, src, {}, path)
        assert counts == {"t": 2}
        for statement in backup.RESTORE_SESSION:
            cur.execute(statement)
        cur.execute(f"CREATE DATABASE `{dst}` DEFAULT CHARACTER SET utf8mb4")
        cur.execute(f"USE `{dst}`")
        for statement in backup.statements(path):
            cur.execute(backup._rename(statement, {src: dst}))
        conn.commit()

        def snapshot(name):
            cur.execute(f"SELECT * FROM `{name}`.`t` ORDER BY id")
            data = cur.fetchall()
            cur.execute(f"CHECKSUM TABLE `{name}`.`t`")
            return data, cur.fetchone()[1]

        assert snapshot(dst) == snapshot(src)
        cur.execute(f"SELECT COUNT(*) FROM `{dst}`.`v`")
        assert cur.fetchone()[0] == 2
    finally:
        for name in (src, dst):
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        conn.close()
