#!/usr/bin/env python3
# fff: 'fff sort workflow': main(argv=None) -> int with the same flags and steps.
# Configure console logging explicitly.
# fff: the SIGINT/SIGTERM handlers and stdout reconfigure run in main(), not at import.
"""
FFF - Unified-CLI Orchestrator

Runs the full workflow in the same order as the legacy run.py but delegates
    each step to the command classes defined in fff.sorter.cli.commands so
that every step uses exactly the same logic as `python main.py <command>`.

Execution order:
  0. Deleted-e621-post processing   (feature module, no standalone CLI command)
  1. Tag-based sorting              (main.py tag-sort)
  2. Main file sorting + hashing    (main.py sort)
  3. Artist processing              (main.py artist-sort)
  4. Archive old files              (main.py archive)
  5. Database verify & fix          (main.py maintenance --verify-database --fix-database)
    6. Tag Sorted empty-folder cleanup (feature module)

Usage:
    fff sort workflow [--dry-run] [--skip-hash] [--config PATH]
"""

import sys
import os
import time
import signal
import argparse
import logging
from argparse import Namespace

# fff: the stdout/stderr UTF-8 reconfigure moved into main() (no import-time side effects)
from datetime import datetime
from pathlib import Path

from fff.sorter.config import Config, DEFAULT_CONFIG_FILE
from fff.sorter.logging_setup import setup_logging
from fff.sorter.cli.commands import (
    TagSortCommand,
    SortCommand,
    ArtistSortCommand,
    ArchiveCommand,
    MaintenanceCommand,
)

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

shutdown_requested = False


def _signal_handler(signum, frame):
    global shutdown_requested
    print(f"\n[STOP] Signal {signum} received — finishing current step then stopping...")
    shutdown_requested = True


def _install_signal_handlers():
    # fff: was run at import time; now called from main() so importing doesn't hijack Ctrl+C
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _header():
    print("[TARGET] FFF v2.0 — Full Workflow (run.py)")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


def _section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _step(n: int, total: int, label: str):
    print(f"\n[Clipboard] Step {n}/{total}: {label}")
    print("-" * (len(label) + 12))


def _elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


# ---------------------------------------------------------------------------
# Individual step functions (each mirrors a main.py sub-command)
# ---------------------------------------------------------------------------

def step_deleted_posts(config, logger, dry_run: bool) -> int:
    """Step 0: Move files whose e621 posts have been deleted."""
    target_dir = getattr(config, 'deleted_from_e621_directory', None)
    move_enabled = getattr(config, 'move_deleted_posts', True)

    if not target_dir:
        logger.debug("deleted_from_e621_directory not set — skipping deleted-post step")
        return 0

    if not move_enabled:
        logger.info("move_deleted_posts is disabled — deleted posts will remain in their current locations")
        print("   [INFO] Deleted post moving is disabled (move_deleted_posts: false)")
        return 0

    print(f"   Target: {target_dir}")
    try:
        from fff.sorter.features.deleted_sorter import DeletedSorter
        from fff.sorter.handlers import DatabaseManager

        db_manager = DatabaseManager(config)
        deleted_sorter = DeletedSorter(config, config.get_e621_database_config(), db_manager)
        results = deleted_sorter.move_deleted_posts(target_dir, dry_run=dry_run)

        moved = results.get('moved', 0)
        missing = results.get('missing', 0)
        skipped = results.get('skipped', 0)
        errors = results.get('move_errors', 0) + results.get('db_errors', 0)

        if moved:
            print(f"   [OK] Moved {moved} files linked to deleted posts")
        else:
            print("   No files linked to deleted posts found")
        if missing:
            print(f"   [INFO] {missing} files already missing from disk")
        if skipped:
            print(f"   [INFO] {skipped} files skipped (favorite/protected)")
        if errors:
            print(f"   [WARNING] {errors} errors occurred")

        logger.info(f"Deleted-post step: moved={moved} missing={missing} skipped={skipped} errors={errors}")
        return moved
    except ImportError:
        logger.debug("DeletedSorter not available — skipping step")
        return 0
    except Exception as e:
        logger.error(f"Error in deleted-post step: {e}")
        print(f"   [ERROR] {e}")
        return 0


def step_tag_sort(config, logger, dry_run: bool) -> int:
    """Step 1: Tag-based pre-sorting via TagSortCommand."""
    if not config.is_tag_sorting_enabled():
        logger.info("Tag sorting disabled in config — skipping")
        return 0

    tag_rules = config.get_tag_sorting_rules()
    if not tag_rules:
        logger.info("Tag sorting enabled but no rules found — skipping")
        return 0

    print(f"   {len(tag_rules)} rule(s) to process")
    total_moved = 0

    try:
        from fff.sorter.features.tag_sorter import TagSorter
        from fff.sorter.handlers import DatabaseManager

        # Create database manager for operation logging
        db_manager = DatabaseManager(config)
        e621_config = config.get_e621_database_config()
        tag_global_base_dirs = getattr(config, "get_tag_sorting_base_dirs", lambda: {})() or {}
        tag_sorter = TagSorter(config, e621_config, db_manager=db_manager)

        def _rule_destinations(rule):
            """Return all effective destination paths for a rule (for hierarchical exclusion)."""
            td = rule.get('target_directory') or ''
            if td:
                return [td]
            rule_bd = rule.get('base_directories') or {}
            eff_bd = rule_bd if any(rule_bd.values()) else tag_global_base_dirs
            fn = rule.get('folder_name') or ''
            if not fn:
                return []
            if rule.get('append_media_type_label', False):
                return [os.path.join(b, f"{fn} - {s}") for b in eff_bd.values() if b
                        for s in ('Pics', 'Vids', 'Gifs')]
            return [os.path.join(b, fn) for b in eff_bd.values() if b]

        # Build full destination list for hierarchical exclusion
        all_destinations = [d for r in tag_rules for d in _rule_destinations(r)]

        for i, rule in enumerate(tag_rules, 1):
            if shutdown_requested:
                logger.warning(f"Shutdown during tag sort after rule {i - 1}/{len(tag_rules)}")
                break

            if not rule.get('enabled', True):
                continue

            tags = rule.get('tags') or []
            needed_tags = rule.get('needed_tags') or None
            can_have_tags = rule.get('can_have_tags') or None
            pool_ids = rule.get('pool_ids') or None
            if not tags and not needed_tags and not can_have_tags and not pool_ids:
                logger.warning(f"Tag rule {i} has no tags or pool IDs — skipping")
                continue

            target_dir = rule.get('target_directory')
            rule_base_dirs = rule.get('base_directories') or {}
            effective_base_dirs = rule_base_dirs if any(rule_base_dirs.values()) else tag_global_base_dirs
            folder_name = rule.get('folder_name') or (os.path.basename(target_dir) if target_dir else None)
            use_split_mode = bool(not target_dir and any(effective_base_dirs.values()) and folder_name)

            if not target_dir and not use_split_mode:
                logger.warning(f"Tag rule {i} missing destination — skipping")
                continue

            exclude_tags = rule.get('exclude_tags') or []
            if isinstance(exclude_tags, str):
                exclude_tags = [exclude_tags] if exclude_tags else []

            higher_priority_dirs = [d for r in tag_rules[:i - 1] for d in _rule_destinations(r)]

            try:
                result = tag_sorter.sort_by_tags(
                    tags=tags if tags and not needed_tags and not can_have_tags else None,
                    needed_tags=needed_tags,
                    can_have_tags=can_have_tags,
                    pool_ids=pool_ids,
                    destination_dir=target_dir if not use_split_mode else None,
                    base_directories=effective_base_dirs if use_split_mode else {},
                    folder_name=folder_name if use_split_mode else None,
                    match_all=rule.get('match_all', False),
                    exclude_favorite_artists=rule.get('exclude_favorite_artists', False),
                    exclude_tags=exclude_tags,
                    exclude_higher_priority_dirs=higher_priority_dirs,
                    only_fav_directory=rule.get('only_fav_directory', False),
                    sub_rules=rule.get('sub_rules') or [],
                    dry_run=dry_run,
                    append_media_type_label=rule.get('append_media_type_label', False),
                    set_post_date=rule.get('match_post_date', False),
                )
                moved = result.get('moved', 0)
                dated = result.get('dates_set', 0)
                total_moved += moved
                if moved:
                    print(f"   Rule {i}: moved {moved} file(s)" + (f", dated {dated}" if rule.get('match_post_date', False) else ""))
                logger.info(f"Tag rule {i}: moved {moved}" + (f", dated {dated}" if rule.get('match_post_date', False) else ""))
            except Exception as e:
                logger.error(f"Error in tag rule {i}: {e}")

        if total_moved:
            print(f"   [OK] Tag sorting total: {total_moved} file(s) moved")
        return total_moved

    except Exception as e:
        logger.error(f"Tag sort step failed: {e}")
        print(f"   [ERROR] {e}")
        return 0


def step_sort(config, logger, dry_run: bool, skip_hash: bool, verbose_hash: bool = False) -> int:
    """Step 2: Main file sorting via SortCommand."""
    args = Namespace(
        dry_run=dry_run,
        skip_hash=skip_hash,
        rehash_only=False,
        force_rehash=False,
        verbose_hash=verbose_hash,
    )
    rc = SortCommand.execute(args, config)
    if rc != 0:
        logger.error("SortCommand returned non-zero exit code")
    return rc


def step_artist_sort(config, logger, dry_run: bool = False, debug: bool = True) -> int:
    """Step 3: Artist-based sorting + artists.yaml sort via ArtistSortCommand."""
    args = Namespace(
        search_all_subdirs=True,
        debug=debug,
        dry_run=dry_run,
    )
    rc = ArtistSortCommand.execute(args, config)
    if rc != 0:
        logger.error("ArtistSortCommand returned non-zero exit code")

    # Sort artists.yaml (legacy run.py also did this after artist processing)
    try:
        from fff.sorter.handlers import ArtistManager
        ArtistManager(config).sort_artists_yaml()
        logger.info("artists.yaml sorted")
    except Exception as e:
        logger.warning(f"Could not sort artists.yaml: {e}")

    return rc


def step_archive(config, logger, dry_run: bool) -> int:
    """Step 4: Archive old files via ArchiveCommand (honours config settings)."""
    archive_options = config.config.get('archive_options', {})
    if not archive_options.get('enabled', True):
        logger.info("Archive disabled in config — skipping")
        print("   [SKIP] Archive disabled in config")
        return 0

    if not (hasattr(config, 'archive_directories') and config.config.get('archive_directories')):
        logger.info("No archive directories configured — skipping")
        return 0

    args = Namespace(
        age_days=None,            # Let command read from config
        threads=archive_options.get('threads', 4),
        dry_run=dry_run,
        include_favorites=not archive_options.get('exclude_favorites', True),
        include_tag_sorted=not archive_options.get('exclude_tag_sorted', True),
    )
    rc = ArchiveCommand.execute(args, config)
    if rc != 0:
        logger.error("ArchiveCommand returned non-zero exit code")
    return rc


def step_maintenance(config, logger) -> int:
    """Step 5: Database verify + fix via MaintenanceCommand."""
    args = Namespace(
        cleanup_logs=False,
        log_age_days=30,
        verify_database=True,
        fix_database=True,
        verify_and_fix=False,
        reset_database=False,
        yes=False,
        all=False,
    )
    rc = MaintenanceCommand.execute(args, config)
    if rc != 0:
        logger.error("MaintenanceCommand returned non-zero exit code")
    return rc


def step_tag_sorted_cleanup(config, logger, dry_run: bool) -> int:
    """Step 6: Remove empty folders left in Tag Sorted directories."""
    try:
        from fff.sorter.features.tag_sorted_cleanup import cleanup_tag_sorted_directories

        deleted, skipped = cleanup_tag_sorted_directories(config, dry_run=dry_run)
        action = 'found' if dry_run else 'removed'
        print(f"   [OK] Tag Sorted cleanup: {deleted} empty folder(s) {action}")
        if skipped:
            print(f"   [WARNING] Tag Sorted cleanup skipped {skipped} folder(s)")
        return 0
    except Exception as e:
        logger.error(f"Tag Sorted cleanup failed: {e}")
        print(f"   [ERROR] Tag Sorted cleanup failed: {e}")
        return 1


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='fff sort workflow',  # fff: was run.py
        description='FFF full workflow orchestrator (uses unified CLI commands)')
    p.add_argument('--config', default=DEFAULT_CONFIG_FILE,  # fff: default = unified config's sorter view
                   help='Path to an FFF-format config.yaml (default: the sorter view of config/config.yaml)')
    p.add_argument('--dry-run', action='store_true',
                   help='Pass --dry-run to every step (no files are moved)')
    p.add_argument('--skip-hash', action='store_true',
                   help='Skip post-sort hashing step')
    p.add_argument('--verbose-hash', action='store_true',
                   help='Show detailed output for each file being hashed')
    p.add_argument('--skip-artist', action='store_true',
                   help='Skip artist processing step')
    p.add_argument('--skip-archive', action='store_true',
                   help='Skip archive step regardless of config')
    p.add_argument('--skip-maintenance', action='store_true',
                   help='Skip database verify/fix step')
    p.add_argument('--log-level',
                   choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                   default='INFO')
    return p


TOTAL_STEPS = 7


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # fff: moved here from module level (import-time side effects)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    _install_signal_handlers()

    _header()

    try:
        config = Config(args.config)
    except Exception as e:
        print(f"[ERROR] Failed to load config '{args.config}': {e}")
        return 1  # fff: was sys.exit(1)

    logger, log_filename = setup_logging(
        config.log_directory,
        console=True,
        level=getattr(logging, args.log_level),
    )
    logger.info("run2.py started")
    print(f"[OK] Config loaded | Log: {log_filename}")

    if args.dry_run:
        print("[ANALYZE] DRY-RUN MODE — no files will actually be moved")

    timings: dict[str, float] = {}

    # Clear possible_missing_files for a clean run
    try:
        from fff.sorter.handlers import DatabaseManager
        db = DatabaseManager(config)
        if getattr(config, 'possible_missing_files_directory', None):
            db.clear_table('possible_missing_files')
            logger.info("possible_missing_files table cleared")
    except Exception as e:
        logger.warning(f"Could not clear possible_missing_files: {e}")

    # -----------------------------------------------------------------
    # Step 0: Deleted posts
    # -----------------------------------------------------------------
    _step(0, TOTAL_STEPS, "Move files for deleted e621 posts")
    t = time.time()
    step_deleted_posts(config, logger, args.dry_run)
    timings['deleted'] = time.time() - t

    if shutdown_requested:
        _abort("after deleted-post step")
        return 0

    # -----------------------------------------------------------------
    # Step 1: Tag sort
    # -----------------------------------------------------------------
    _step(1, TOTAL_STEPS, "Tag-based sorting")
    t = time.time()
    tag_moved = step_tag_sort(config, logger, args.dry_run)
    timings['tag_sort'] = time.time() - t

    if shutdown_requested:
        _abort("after tag sort")
        return 0

    # -----------------------------------------------------------------
    # Step 2: Main sort
    # -----------------------------------------------------------------
    _step(2, TOTAL_STEPS, "Main file sorting")
    t = time.time()
    step_sort(config, logger, args.dry_run, args.skip_hash, getattr(args, 'verbose_hash', False))
    timings['sort'] = time.time() - t

    if shutdown_requested:
        _abort("after main sort")
        return 0

    # -----------------------------------------------------------------
    # Step 3: Artist sort
    # -----------------------------------------------------------------
    _step(3, TOTAL_STEPS, "Artist processing")
    t = time.time()
    if not args.skip_artist:
        step_artist_sort(config, logger, dry_run=args.dry_run, debug=True)
    else:
        print("   [SKIP] --skip-artist flag set")
    timings['artist'] = time.time() - t

    if shutdown_requested:
        _abort("after artist sort")
        return 0

    # -----------------------------------------------------------------
    # Step 4: Archive
    # -----------------------------------------------------------------
    _step(4, TOTAL_STEPS, "Archive old files")
    t = time.time()
    if not args.skip_archive:
        step_archive(config, logger, args.dry_run)
    else:
        print("   [SKIP] --skip-archive flag set")
    timings['archive'] = time.time() - t

    if shutdown_requested:
        _abort("after archive")
        return 0

    # -----------------------------------------------------------------
    # Step 5: Maintenance (verify + fix)
    # -----------------------------------------------------------------
    _step(5, TOTAL_STEPS, "Database verification & cleanup")
    t = time.time()
    if not args.skip_maintenance:
        step_maintenance(config, logger)
    else:
        print("   [SKIP] --skip-maintenance flag set")
    timings['maintenance'] = time.time() - t

    # -----------------------------------------------------------------
    # Step 6: Tag Sorted empty-folder cleanup
    # -----------------------------------------------------------------
    _step(6, TOTAL_STEPS, "Remove empty Tag Sorted folders")
    t = time.time()
    step_tag_sorted_cleanup(config, logger, args.dry_run)
    timings['tag_sorted_cleanup'] = time.time() - t

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    total = sum(timings.values())
    _section("COMPLETED")
    for step_name, elapsed in timings.items():
        print(f"   {step_name:<14} {elapsed:>6.1f}s")
    print(f"   {'TOTAL':<14} {total:>6.1f}s")
    print()

    logger.info(f"run2.py finished — total {total:.1f}s")
    return 0


def _abort(where: str):
    print(f"\n[STOP] Shutdown requested {where}. Stopping.")
    logging.getLogger('fff.sorter.run2').warning(f"Shutdown {where}")


if __name__ == '__main__':
    sys.exit(main())
