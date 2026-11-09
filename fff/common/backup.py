"""Database backups: ``fff db backup`` and ``fff db restore``, with no mysqldump needed.

A backup is a folder ``<backup.directory>/<YYYYmmdd_HHMMSS>_<env>/`` with one gzipped SQL file
per database and a ``manifest.json`` (row counts per table, file checksums). All three databases
are read inside one consistent snapshot, so the backup is a single point in time even while a
sync is writing. The folder is written as ``*.partial`` and renamed when complete; only complete
backups count, and the newest ``backup.keep`` are kept.

File format: SQL, one statement per block, each block ending with a line that is just ``;``.
Row data is written one INSERT per line (newlines inside values are escaped), so that line can't
occur inside data. Restore with ``fff db restore`` (the mysql client would need DELIMITER handling
for the stored procedures). Database names are written as their production names and mapped to
the restore target, so a backup can be restored into test_* or production.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import re
import shutil
import time
from decimal import Decimal
from pathlib import Path

from fff.common import config as fff_config
from fff.common import db, paths

logger = logging.getLogger(__name__)

MODULES = ("fluffle", "archive", "sorter")
DEFAULTS = {"directory": None, "keep": 7, "max_age_days": 2, "in_daily": True}
INSERT_BATCH_BYTES = 1_000_000          # well under MySQL's default max_allowed_packet (64 MB)
FETCH_ROWS = 5000
SESSION = ("SET NAMES utf8mb4", "SET time_zone = '+00:00'")
RESTORE_SESSION = (*SESSION, "SET FOREIGN_KEY_CHECKS = 0", "SET UNIQUE_CHECKS = 0",
                   "SET sql_mode = 'NO_AUTO_VALUE_ON_ZERO'")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def settings() -> dict:
    try:
        configured = fff_config.load().get("backup") or {}
    except fff_config.ConfigError:
        configured = {}
    return DEFAULTS | configured


def backup_root() -> Path:
    """Where backups go: backup.directory, or data/backups (data/test/backups in test)."""
    configured = settings()["directory"]
    if not configured:
        return paths.for_env(paths.DATA_DIR / "backups")
    path = Path(configured)
    if not path.is_absolute():
        path = paths.ROOT / path
    try:
        cfg = fff_config.load()
        return Path(fff_config.test_path(str(path), cfg)) if fff_config.is_test(cfg) else path
    except fff_config.ConfigError:
        return path


# --- SQL literals ----------------------------------------------------------------------

_ESCAPES = str.maketrans({"\\": "\\\\", "'": "\\'", "\0": "\\0", "\n": "\\n", "\r": "\\r", "\x1a": "\\Z"})


def literal(value) -> str:
    """A MySQL literal for a value read by mysql-connector (round-trips through restore)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (bytes, bytearray)):
        return f"X'{bytes(value).hex()}'" if value else "''"
    if isinstance(value, dt.datetime):
        return f"'{value.isoformat(sep=' ')}'"
    if isinstance(value, dt.date):
        return f"'{value.isoformat()}'"
    if isinstance(value, dt.timedelta):
        total = value.total_seconds()
        sign, total = ("-" if total < 0 else ""), abs(total)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        return f"'{sign}{int(hours):02d}:{int(minutes):02d}:{seconds:09.6f}'"
    if isinstance(value, (set, frozenset)):
        value = ",".join(sorted(value))
    return "'" + str(value).translate(_ESCAPES) + "'"


def _name(name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unexpected table or database name {name!r}")
    return f"`{name}`"


# --- backup ----------------------------------------------------------------------------

def _objects(cur, schema: str):
    cur.execute("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
                "ORDER BY TABLE_NAME", (schema,))
    rows = cur.fetchall()
    return [t for t, kind in rows if kind == "BASE TABLE"], [t for t, kind in rows if kind == "VIEW"]


def _insert_columns(cur, schema: str, table: str) -> list[tuple[str, str]]:
    """(column, data type) to write: every column except generated ones (MySQL computes those)."""
    cur.execute("SELECT COLUMN_NAME, DATA_TYPE, EXTRA FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION", (schema, table))
    return [(col, kind.lower()) for col, kind, extra in cur.fetchall()
            if "VIRTUAL GENERATED" not in (extra or "").upper() and "STORED GENERATED" not in (extra or "").upper()]


def _select_expr(column: str, kind: str) -> str:
    # MySQL sends a FLOAT to clients rounded to ~6 significant digits (0.95532697 -> 0.955327), so
    # writing that back changes the stored value. As a DOUBLE it arrives exact and converts back
    # to the identical FLOAT on restore.
    return f"CAST({_name(column)} AS DOUBLE)" if kind == "float" else _name(column)


def _rename(sql: str, renames: dict) -> str:
    for old, new in renames.items():
        if old != new:
            sql = sql.replace(f"`{old}`", f"`{new}`")
    return sql


def _strip_definer(sql: str) -> str:
    return re.sub(r"DEFINER\s*=\s*`[^`]+`@`[^`]+`\s*", "", sql, flags=re.I)


def _write(out, statement: str) -> None:
    out.write(statement.rstrip().rstrip(";") + "\n;\n")


def _dump_database(conn, live: str, prod: str, renames: dict, path: Path) -> dict:
    """Write one database to a gzipped SQL file; returns {table: rows}."""
    from fff.common.schema_copy import get_database_schema

    cur = conn.cursor()
    tables, views = _objects(cur, live)
    cur.close()
    ddl = get_database_schema(conn, live)
    counts = {}
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as out:
        out.write(f"-- FurryFileFinagler backup of {prod}. Restore with 'fff db restore'.\n")
        for table in tables:
            out.write(f"\n-- table {table}\n")
            _write(out, f"DROP TABLE IF EXISTS {_name(table)}")
            _write(out, _rename(re.sub(r"\s+AUTO_INCREMENT=\d+", "", ddl["tables"][table]), renames))
            meta = conn.cursor()
            columns = _insert_columns(meta, live, table)
            meta.close()
            head = f"INSERT INTO {_name(table)} ({', '.join(_name(c) for c, _ in columns)}) VALUES "
            data = conn.cursor(buffered=False)
            data.execute(f"SELECT {', '.join(_select_expr(c, k) for c, k in columns)} "
                         f"FROM {_name(live)}.{_name(table)}")
            n, batch, size = 0, [], 0
            while True:
                rows = data.fetchmany(FETCH_ROWS)
                if not rows:
                    break
                for row in rows:
                    values = "(" + ",".join(literal(v) for v in row) + ")"
                    batch.append(values)
                    size += len(values)
                    if size >= INSERT_BATCH_BYTES:
                        _write(out, head + ",".join(batch))
                        batch, size = [], 0
                n += len(rows)
            if batch:
                _write(out, head + ",".join(batch))
            data.close()
            counts[table] = n
        for view in views:
            out.write(f"\n-- view {view}\n")
            _write(out, f"DROP VIEW IF EXISTS {_name(view)}")
            _write(out, _rename(_strip_definer(ddl["views"][view]), renames))
        for kind in ("procedures", "functions"):
            for name, body in sorted(ddl[kind].items()):
                out.write(f"\n-- {kind[:-1]} {name}\n")
                _write(out, f"DROP {kind[:-1].upper()} IF EXISTS {_name(name)}")
                _write(out, _rename(_strip_definer(body), renames))
    return counts


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create(modules=MODULES, root: Path | None = None, progress=print) -> Path:
    """Back up the active environment's databases; returns the finished backup folder."""
    cfg = fff_config.load()
    env = fff_config.environment(cfg)
    root = Path(root or backup_root())
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    final = root / f"{stamp}_{env}"
    partial = root / f"{stamp}_{env}.partial"
    partial.mkdir(parents=True)
    renames = {fff_config.database_name(cfg, m): fff_config.prod_database_name(cfg, m) for m in MODULES}
    manifest = {"format": 1, "created": dt.datetime.now().isoformat(timespec="seconds"),
                "environment": env, "databases": {}}
    started = time.monotonic()
    conn = db.connect_server(cfg)
    try:
        cur = conn.cursor()
        for statement in SESSION:
            cur.execute(statement)
        cur.execute("SELECT @@version")
        manifest["server_version"] = cur.fetchone()[0]
        cur.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        cur.close()
        for module in modules:
            live = fff_config.database_name(cfg, module)
            prod = renames[live]
            path = partial / f"{prod}.sql.gz"
            progress(f"{module}: {live} -> {path.name}")
            counts = _dump_database(conn, live, prod, renames, path)
            manifest["databases"][module] = {"name": prod, "file": path.name, "sha256": _sha256(path),
                                             "bytes": path.stat().st_size, "rows": counts}
        conn.rollback()
    except BaseException:
        conn.close()
        shutil.rmtree(partial, ignore_errors=True)
        raise
    conn.close()
    manifest["seconds"] = round(time.monotonic() - started, 1)
    (partial / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    partial.rename(final)
    return final


def backups(root: Path | None = None) -> list[Path]:
    """Complete backups, oldest first."""
    root = Path(root or backup_root())
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.endswith(".partial")
                  and (p / "manifest.json").is_file())


def prune(keep: int | None = None, root: Path | None = None) -> list[Path]:
    """Delete all but the newest ``keep`` complete backups, and leftover partial folders."""
    keep = int(settings()["keep"] if keep is None else keep)
    root = Path(root or backup_root())
    removed = []
    complete = backups(root)
    for path in complete[:-keep] if keep > 0 else []:
        shutil.rmtree(path)
        removed.append(path)
    if root.is_dir():
        newest = complete[-1].name if complete else ""
        for path in root.glob("*.partial"):
            if path.name < newest:  # an older run that never finished
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
    return removed


def newest_age_days(root: Path | None = None) -> float | None:
    found = backups(root)
    if not found:
        return None
    created = json.loads((found[-1] / "manifest.json").read_text(encoding="utf-8"))["created"]
    return (dt.datetime.now() - dt.datetime.fromisoformat(created)).total_seconds() / 86400


# --- restore ---------------------------------------------------------------------------

def read_manifest(backup: Path) -> dict:
    return json.loads((Path(backup) / "manifest.json").read_text(encoding="utf-8"))


def statements(path: Path):
    """Statements of a backup file (each block ends with a line that is just ';')."""
    lines = []
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as f:
        for line in f:
            if line == ";\n":
                if lines:
                    yield "".join(lines).rstrip("\n")
                lines = []
            elif lines or not line.startswith("--") and line.strip():
                lines.append(line)
    if "".join(lines).strip():
        raise ValueError(f"{path.name} ends in the middle of a statement (truncated file?)")


def verify(backup: Path) -> list[str]:
    """Problems with a backup's files (missing, checksum mismatch); empty when it is intact."""
    backup = Path(backup)
    problems = []
    for module, info in read_manifest(backup)["databases"].items():
        path = backup / info["file"]
        if not path.is_file():
            problems.append(f"{module}: {info['file']} is missing")
        elif _sha256(path) != info["sha256"]:
            problems.append(f"{module}: {info['file']} does not match its checksum")
    return problems


def restore(backup: Path, target: str = "test", modules=None, progress=print) -> dict:
    """Restore a backup into the test_* databases (``target='test'``) or production ('prod').

    Every table, view and routine in the backup is dropped and recreated in the target, then
    row counts are checked against the manifest. Returns {module: {table: rows}}.
    """
    if target not in ("test", "prod"):
        raise ValueError("target must be 'test' or 'prod'")
    backup = Path(backup)
    problems = verify(backup)
    if problems:
        raise ValueError("backup is damaged: " + "; ".join(problems))
    manifest = read_manifest(backup)
    cfg = fff_config.load()
    names = {m: (fff_config.prod_database_name(cfg, m) if target == "prod"
                 else fff_config.TEST_PREFIX + fff_config.prod_database_name(cfg, m)) for m in MODULES}
    renames = {fff_config.prod_database_name(cfg, m): names[m] for m in MODULES}
    selected = [m for m in manifest["databases"] if not modules or m in modules]
    restored = {}
    conn = db.connect_server(cfg)
    try:
        cur = conn.cursor()
        for statement in RESTORE_SESSION:
            cur.execute(statement)
        for module in selected:
            info = manifest["databases"][module]
            target_db = names[module]
            if target == "test" and not target_db.startswith(fff_config.TEST_PREFIX):
                raise ValueError(f"refusing to restore into {target_db}: not a test database")
            progress(f"{module}: {info['file']} -> {target_db}")
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {_name(target_db)} DEFAULT CHARACTER SET utf8mb4")
            cur.execute(f"USE {_name(target_db)}")
            for statement in statements(backup / info["file"]):
                cur.execute(_rename(statement, renames))
            conn.commit()
            counts = {}
            for table, expected in info["rows"].items():
                cur.execute(f"SELECT COUNT(*) FROM {_name(target_db)}.{_name(table)}")
                counts[table] = cur.fetchone()[0]
                if counts[table] != expected:
                    raise RuntimeError(f"{target_db}.{table}: restored {counts[table]} rows, "
                                       f"backup has {expected}")
            restored[module] = counts
        cur.close()
    finally:
        conn.close()
    return restored
