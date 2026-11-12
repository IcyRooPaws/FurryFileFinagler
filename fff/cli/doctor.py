"""``fff doctor``: check databases, configured folders and dependencies for all modules.

Replaces reverse search's scripts/test_directories.py (and grows to cover the sorter's
diagnostic scripts in later phases).
"""
from __future__ import annotations

import importlib
from pathlib import Path

from fff.common import config as fff_config
from fff.common import paths
from fff.common.fileops import restrict_to_owner

OK, MISSING, FAIL = "OK", "MISSING", "FAIL"

REQUIRED_MODULES = ["yaml", "requests", "mysql.connector", "bs4", "send2trash", "flask", "PIL",
                    "imagehash", "numpy", "scipy", "pywt", "cv2", "xxhash", "colorama"]
OPTIONAL_MODULES = {"pygame": "audio in the blacklist review GUI"}


def configured_directories(cfg: dict) -> list[tuple[str, str]]:
    """(label, path) for every folder the three modules are configured to use."""
    dirs: list[tuple[str, str]] = []

    fl = fff_config.legacy_view("fluffle", cfg)
    search = fl.get("search", {})
    for d in search.get("image_directories") or ([search["image_directory"]] if search.get("image_directory") else []):
        dirs.append(("fluffle.search.image_directories", d))
    for key in ("not_found_directory", "deleted_source_directory", "other_sourced_directory",
                "other_sourced_backup_directory", "source_found_directory"):
        if search.get(key):
            dirs.append((f"fluffle.search.{key}", search[key]))
    for section in ("e621", "furaffinity"):
        for key in ("download_directory", "source_found_directory", "blacklisted_directory", "rename_search_directory"):
            if fl.get(section, {}).get(key):
                dirs.append((f"fluffle.{section}.{key}", fl[section][key]))

    ar = fff_config.legacy_view("archive", cfg)
    download = ar.get("download", {})
    for key in ("missing_files_directory", "all_star_favs", "default"):
        if download.get(key):
            dirs.append((f"archive.download.{key}", download[key]))
    for media in ("images", "videos", "gifs"):
        for rating, d in (download.get(media) or {}).items():
            dirs.append((f"archive.download.{media}.{rating}", d))
    for d in download.get("extra_search_dirs") or []:
        dirs.append(("archive.download.extra_search_dirs", d))

    so = fff_config.legacy_view("sorter", cfg)
    raw = (so.get("SortMobileDownloadFolder") or {}).get("source_directories") or ""
    for d in (raw.splitlines() if isinstance(raw, str) else raw):
        if d.strip():
            dirs.append(("sorter.source_directories", d.strip()))
    for key, d in (so.get("destination_directories") or {}).items():
        dirs.append((f"sorter.destination_directories.{key}", d))
    for kind, value in (so.get("archive_directories") or {}).items():
        if isinstance(value, dict):
            for media, d in value.items():
                if d:
                    dirs.append((f"sorter.archive_directories.{kind}.{media}", d))
        elif value:
            dirs.append((f"sorter.archive_directories.{kind}", value))
    other = so.get("other") or {}
    for key, value in other.items():
        if key.endswith("_directory") and isinstance(value, str) and key != "log_directory":
            dirs.append((f"sorter.other.{key}", value))
    for d in other.get("other_directories_to_hash") or []:
        dirs.append(("sorter.other.other_directories_to_hash", d))
    return dirs


def check_databases(cfg: dict) -> list[tuple[str, str, str]]:
    from fff.common import db

    results = []
    for module in fff_config.MODULES:
        name = fff_config.database_name(cfg, module)
        try:
            conn = db.connect(module, cfg, pooled=False, connection_timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s", (name,))
            (count,) = cur.fetchone()
            conn.close()
            results.append((module, name, f"{OK} ({count} tables)"))
        except Exception as exc:  # report every failure, keep checking the others
            results.append((module, name, f"{FAIL}: {exc}"))
    return results


def check_schema() -> list[str]:
    """Missing tables, and hash tables the all_files view doesn't cover (fff.common.schema)."""
    from fff.common import schema

    try:
        return schema.check()
    except Exception as exc:
        return [f"{FAIL}: could not check the schema: {exc}"]


def check_backups() -> tuple[bool, str]:
    """(ok, message) for the newest database backup (fff.common.backup)."""
    from fff.common import backup

    root, max_age = backup.backup_root(), float(backup.settings()["max_age_days"])
    age = backup.newest_age_days(root)
    if age is None:
        return False, f"{MISSING} no backups in {root} (run 'fff db backup')"
    when = f"newest is {age:.1f} days old, {len(backup.backups(root))} kept in {root}"
    return (True, f"{OK:<8} {when}") if age <= max_age else (False, f"{MISSING} {when} (over {max_age:g} days)")


def check_modules() -> list[tuple[str, str]]:
    results = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
            results.append((name, OK))
        except Exception as exc:
            results.append((name, f"{MISSING}: {exc}"))
    for name, why in OPTIONAL_MODULES.items():
        try:
            importlib.import_module(name)
            results.append((name, f"{OK} (optional)"))
        except Exception:
            results.append((name, f"not installed (optional: {why})"))
    return results


def check_secrets(secrets_path: Path | None = None) -> tuple[bool, str]:
    """Make secrets.yaml readable by the current user only (files saved before 1.32.0 weren't)."""
    secrets_path = secrets_path or paths.CONFIG_DIR / "secrets.yaml"
    if not secrets_path.is_file():
        return True, f"{OK:<8} no {secrets_path.name} (credentials not set up yet)"
    if restrict_to_owner(secrets_path):
        return True, f"{OK:<8} {secrets_path.name}: only your account can read it"
    return False, f"{FAIL:<8} {secrets_path.name}: could not limit it to your account; other users may read it"


def run(args) -> int:
    cfg = fff_config.load()
    problems = 0

    if not args.skip_deps:
        print("== Python packages")
        for name, status in check_modules():
            print(f"  {status:<10} {name}" if status == OK else f"  {name}: {status}")
            problems += status.startswith(MISSING)

    print("\n== Configured folders")
    seen = set()
    for label, path in configured_directories(cfg):
        if (label, path) in seen:
            continue
        seen.add((label, path))
        exists = Path(path).exists()
        problems += not exists
        print(f"  {OK if exists else MISSING:<8} {path}   [{label}]")

    if not args.skip_db:
        print(f"\n== Databases ({fff_config.environment(cfg)} environment)")
        for module, name, status in check_databases(cfg):
            print(f"  {module:<8} {name:<22} {status}")
            problems += not status.startswith(OK)
        schema_problems = check_schema()
        for line in schema_problems:
            print(f"  {line}")
        if not schema_problems:
            print(f"  {OK:<8} schema: every table FFF uses exists; all_files covers every hash table")
        problems += sum(1 for line in schema_problems if not line.startswith("run "))

    print("\n== Secrets")
    ok, message = check_secrets()
    print(f"  {message}")
    problems += not ok

    print("\n== Database backups")
    ok, message = check_backups()
    print(f"  {message}")
    problems += not ok

    print(f"\n{problems} problem(s) found." if problems else "\nAll checks passed.")
    return 1 if problems else 0
