"""``fff e621 ...``, ``fff fa ...``, ``fff gui ...``, ``fff web`` and archive ``fff db``/``fff legacy`` commands.

Each command calls a ported e621 archive entry point (project notes §7 command map).

* Scripts with their own argparse get the rest of the command line as-is, so their flags
  and ``--help`` are exactly the existing code's.
* Scripts without argparse get their options declared here instead. Unknown options are
  rejected, so ``--help`` can never start a real run.
"""
from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass, field

FORWARD = None  # the script parses its own arguments


@dataclass
class Command:
    path: tuple[str, ...]            # e.g. ("e621", "backfill", "pools")
    module: str                      # e.g. "fff.archive.scripts.database.backfill_pools"
    help: str
    func: str = "main"
    options: list | None = FORWARD   # [(flags, kwargs), ...] for scripts without argparse
    original: str = ""               # original script, shown in --help
    call: str = "argv"               # "argv": func(argv) | "none": func()
    aliases: tuple = ()              # other command implementations this command replaces
    extra: dict = field(default_factory=dict)


S = "fff.archive.scripts"
A = "e621 archive/"
FL = "reverse-image search/"
IS = "media sorter/"

COMMANDS = [
    # --- sync ------------------------------------------------------------------
    Command(("e621", "sync"), "fff.archive.sync", "Sync e621 favorites into e621_posts_db (full pipeline)",
            original=A + "e621_main.py"),
    # --- backfill / verify -----------------------------------------------------------
    Command(("e621", "backfill", "deletion-reasons"), f"{S}.database.backfill_deletion_reasons",
            "Scrape deletion reasons and takedown details for deleted posts",
            original=A + "scripts/database/backfill_deletion_reasons.py"),
    Command(("e621", "backfill", "file-paths"), f"{S}.database.backfill_file_paths_from_image_sorting",
            "Fill missing posts.file_path from the image_sorting_hashes database",
            original=A + "scripts/database/backfill_file_paths_from_image_sorting.py"),
    Command(("e621", "backfill", "file-paths-fast"), f"{S}.database.fast_backfill_file_paths",
            "Bulk file_path backfill/verify via the all_files view",
            original=A + "scripts/database/fast_backfill_file_paths.py"),
    Command(("e621", "backfill", "pools"), f"{S}.database.backfill_pools",
            "Pool links from cached pages, plus pool metadata from the API", original=A + "scripts/database/backfill_pools.py"),
    Command(("e621", "backfill", "relationships"), f"{S}.database.backfill_post_relationships",
            "Parent/child relationships from cached pages", original=A + "scripts/database/backfill_post_relationships.py"),
    Command(("e621", "backfill", "phash-cache"), f"{S}.database.build_perceptual_hash_cache",
            "Hash local files into e621_perceptual_hashes", original=A + "scripts/database/build_perceptual_hash_cache.py"),
    Command(("e621", "verify", "post-ids"), f"{S}.database.validate_post_ids_in_filenames",
            "Check post ids in filenames against the DB (with a review GUI)",
            original=A + "scripts/database/validate_post_ids_in_filenames.py"),
    Command(("e621", "rename-artists"), f"{S}.database.update_artist_filenames",
            "Rename files whose artist tags changed", original=A + "scripts/database/update_artist_filenames.py"),
    Command(("e621", "report", "deletion-timestamps"), f"{S}.database.check_deletion_timestamp_accuracy",
            "Report how accurate deleted_at timestamps are", original=A + "scripts/database/check_deletion_timestamp_accuracy.py"),
    Command(("e621", "report", "takedowns"), f"{S}.database.view_takedown_details",
            "Interactive takedown viewer", original=A + "scripts/database/view_takedown_details.py"),
    Command(("e621", "ai", "cleanup-deleted"), f"{S}.database.cleanup_ai_generated_posts",
            "Posts e621 deleted as AI: snapshot, unfavorite, optionally trash files, delete from DB (dry run by default)",
            original=A + "scripts/database/cleanup_ai_generated_posts.py"),
    Command(("legacy", "add-deletion-reason-column"), f"{S}.database.add_deletion_reason_column",
            "One-off migration: add posts.deletion_reason", original=A + "scripts/database/add_deletion_reason_column.py"),
    Command(("legacy", "migrate-ai-archive-text"), f"{S}.database.migrate_ai_archive_text_to_db",
            "One-off: import data/ai_generated_posts_archive.txt into the DB",
            original=A + "scripts/database/migrate_ai_archive_text_to_db.py"),
    Command(("legacy", "migrate-ai-artists"), f"{S}.database.migrate_ai_artists_to_db",
            "One-off: import data/ai_artist.md into ai_flagged_artists", original=A + "scripts/database/migrate_ai_artists_to_db.py"),
    Command(("e621", "verify", "file-paths"), f"{S}.database.verify_and_repair_file_paths",
            "Verify posts.file_path and repair broken paths", original=A + "scripts/database/verify_and_repair_file_paths.py"),
    Command(("e621", "verify", "file-names"), f"{S}.database.verify_and_fix_file_names",
            "Make posts.file_name agree with the files on disk", original=A + "scripts/database/verify_and_fix_file_names.py"),
    Command(("e621", "match-downloads"), f"{S}.database.create_download_tables_and_match_files",
            "Build dl_fav/dl_nsfw/dl_sfw tables and match downloaded files to posts",
            options=[(("--dry-run",), {"action": "store_true"}), (("--skip-to-id-matching",), {"action": "store_true"})],
            original=A + "scripts/database/create_download_tables_and_match_files.py"),
    # --- AI cleanup ------------------------------------------------------------------
    Command(("e621", "ai", "remove-tagged"), f"{S}.favorites.remove_ai_tagged_posts",
            "Remove posts with AI tags (move file, unfavorite, downvote, delete from DB)",
            original=A + "scripts/favorites/remove_ai_tagged_posts.py"),
    Command(("e621", "ai", "remove-artists"), f"{S}.favorites.remove_ai_artist_posts",
            "Remove posts by artists listed in data/ai_artist.md", original=A + "scripts/favorites/remove_ai_artist_posts.py"),
    # --- favorites ---------------------------------------------------------------------
    Command(("e621", "favorites", "add-missing"), f"{S}.favorites.favorite_missing_posts",
            "Favorite e621 posts the sorter knows about but that aren't favorited, then sync",
            original=A + "scripts/favorites/favorite_missing_posts.py"),
    Command(("e621", "favorites", "swap-duplicates"), f"{S}.favorites.swap_duplicate_favorites",
            "Move favorites from 'inferior/duplicate of' posts to their replacements",
            original=A + "scripts/favorites/swap_duplicate_favorites.py"),
    Command(("e621", "favorites", "from-md5"), f"{S}.favorites.md5_favorite_checker",
            "Look up md5s from data/md5_search/md5.txt and favorite the matches",
            original=A + "search_for_posts/md5_favorite_checker.py"),
    # --- files --------------------------------------------------------------------------
    Command(("e621", "files", "find-deleted"), f"{S}.file_management.check_deleted_posts_files_enhanced",
            "Look for local files of deleted posts (checks 10 posts, as the original did)", options=[],
            original=A + "scripts/file_management/check_deleted_posts_files_enhanced.py"),
    Command(("e621", "files", "unfavorited"), f"{S}.file_management.manage_unfavorited_posts",
            "Interactive menu for unfavorited posts (archive / delete)", options=[],
            original=A + "scripts/file_management/manage_unfavorited_posts.py"),
    Command(("e621", "files", "restore"), f"{S}.file_management.restore_deleted_files",
            "Restore files listed in a deleted-files CSV (one-off tool)",
            original=A + "scripts/file_management/restore_deleted_files.py"),
    # --- reports ------------------------------------------------------------------------
    Command(("e621", "report", "favorites-diff"), f"{S}.favorites.analyze_favorites_difference",
            "Compare database post ids with live favorites", options=[],
            original=A + "scripts/favorites/analyze_favorites_difference.py"),
    Command(("e621", "report", "relationships"), f"{S}.tools.check_relationship_stats",
            "Print parent/child relationship statistics", options=[],
            original=A + "scripts/tools/check_relationship_stats.py"),
    Command(("e621", "db-export"), f"{S}.tools.download_and_process_e621_db_exports",
            "Download e621 DB exports and load them into e621_db_dump_* tables", options=[],
            original=A + "scripts/tools/download_and_process_e621_db_exports.py"),
    # --- db / tools ------------------------------------------------------------------
    Command(("e621", "reset-db"), f"{S}.database.reset_database", "Drop all e621_posts_db tables (asks to confirm)",
            original=A + "scripts/database/reset_database.py"),
    Command(("db", "inspect-hashes"), f"{S}.tools.inspect_image_sorting_hashes_db",
            "Print the image_sorting_hashes schema and hash columns", options=[], call="none",
            original=A + "scripts/tools/inspect_image_sorting_hashes_db.py"),
    # --- reverse search (implemented in reverse-image search) -------------------------------------
    Command(("fluffle", "search"), "fff.fluffle.search",
            "Reverse-search images with reverse search (+FuzzySearch), process e621/FA matches, sort the rest",
            options=[], original=FL + "fluffle.py"),
    Command(("fluffle", "process-e621"), "fff.fluffle.e621_matches",
            "Process stored exact e621 matches: favorite, download, move existing code (applies the blacklist)",
            options=[], original=FL + "scripts/process_e621_matches.py"),
    Command(("fluffle", "process-fa"), "fff.fluffle.fa_matches",
            "Process stored exact FA matches (no e621 match): favorite, download, move existing code",
            options=[], original=FL + "scripts/process_furaffinity_matches.py"),
    Command(("fluffle", "sync-fa-favorites"), "fff.fluffle.sync_fa_favorites",
            "Favorite on FA the images that matched both e621 and FA", options=[],
            original=FL + "scripts/sync_fa_favorites.py"),
    Command(("fluffle", "verify-db"), "fff.fluffle.verify_database", "Print recent rows and counts from image_search",
            func="verify_database", options=[], original=FL + "scripts/verify_database.py"),
    Command(("fa", "rename-authors"), "fff.fluffle.rename_fa_authors",
            "Rename *-FA-* files from author name to author id (mappings from the reverse search DB; interactive)",
            options=[], original=FL + "scripts/rename_fa_author_files.py"),
    Command(("fa", "rename-authors-manual"), "fff.fluffle.rename_fa_authors_manual",
            "Same, with mappings from config/fa_author_mappings.yaml (interactive)", options=[],
            original=FL + "scripts/rename_fa_author_manual.py"),
    # --- media sorter -----------------------------------------------------------------------
    Command(("sort",), "fff.sorter.entry",
            "media sorter: sort, archive, tag/pool/artist sort, maintenance, duplicates, workflow (was main.py/run.py)",
            original=IS + "main.py", aliases=(IS + "run.py", IS + "fa_backlog_sort.py", IS + "fff.sorter/__main__.py")),
    Command(("e621", "fix-no-source-names"), "fff.sorter.scripts.fix_no_source_filenames",
            "Rename files whose path name differs from posts.file_name (excludes Fav/Artists, Tag Sorted, ...)",
            options=[(("--dry-run",), {"action": "store_true"})], original=IS + "fix_no_source_filenames.py"),
    Command(("dupes", "advanced"), "fff.sorter.scripts.find_duplicates_advanced",
            "Multi-stage duplicate finder (KNOWN BROKEN, project notes section 13)", original=IS + "scripts/find_duplicates_advanced.py"),
    Command(("db", "report", "space"), "fff.sorter.scripts.table_space_report",
            "Sum file_size per image_sorting_hashes table", options=[], original=IS + "table_space_report.py"),
    Command(("db", "report", "archive-tables"), "fff.sorter.scripts.check_archive_tables",
            "List the archive_* tables", options=[], original=IS + "check_archive_tables.py"),
    Command(("db", "diagnose", "all-files-view"), "fff.sorter.scripts.diagnose_all_files_view",
            "Diagnose the all_files view (artist-scan filters)", options=[], original=IS + "test_all_files_view.py"),
    Command(("db", "diagnose", "op-logging"), "fff.sorter.scripts.diagnose_file_operations_logging",
            "Exercise file_operations logging (writes and deletes test rows)", options=[],
            original=IS + "test_file_operations_logging.py"),
    Command(("legacy", "migrate-artist-columns"), "fff.legacy.sorter.migrate_artist_columns",
            "One-off: add last_artist_check / artist_matched to every table", options=[],
            original=IS + "migrate_artist_columns.py"),
    Command(("legacy", "migrate-paths-forward-slashes"), "fff.legacy.sorter.migrate_paths_to_forward_slashes",
            "One-off: rewrite stored file_path values with forward slashes", original=IS + "migrate_paths_to_forward_slashes.py"),
    Command(("legacy", "fix-file-hash-column"), "fff.legacy.sorter.fix_file_hash_column",
            "One-off: widen file_operations.file_hash (obsolete)", options=[], original=IS + "fix_file_hash_column.py"),
    Command(("legacy", "remove-file-hash-column"), "fff.legacy.sorter.remove_file_hash_column",
            "One-off: drop file_operations.file_hash", options=[], original=IS + "remove_file_hash_column.py"),
    Command(("hash",), "fff.sorter.hash_cli", "Hash files / show info / find duplicates (v1 media_hasher CLI)",
            original=IS + "_handlers/media_hasher/cli.py"),
    Command(("gui", "duplicates"), "fff.gui.duplicate_manager_ui", "Duplicate manager GUI", options=[],
            original=IS + "scripts/duplicate_manager_ui.py"),
    Command(("gui", "tag-sort-config"), "fff.gui.tag_sort_config_ui_v2", "Tag sorting rule editor GUI",
            original=IS + "scripts/tag_sort_config_ui_v2.py", aliases=(IS + "scripts/tag_sort_config_ui.py",)),
    Command(("gui", "artist-config"), "fff.gui.artist_config_ui", "Artist config editor GUI (sort_artists.yaml)",
            original=IS + "scripts/artist_config_ui.py"),
    # --- FA --------------------------------------------------------------------------
    Command(("fa", "sync-external"), "fff.fluffle.sync_external_fa_sources",
            "Track FA source links from e621 posts and favorite them on FA", options=[], call="none",
            original="e621 archive/FA Fav Sync/sync_external_fa_sources.py"),
    # --- GUIs --------------------------------------------------------------------------
    Command(("gui", "blacklist-review"), "fff.gui.blacklist_review_ui",
            "Review blacklisted posts (auto-deletes critical/AI-tagged posts first)", options=[],
            original=A + "scripts/ui/blacklist_review_ui.py"),
    Command(("gui", "artist-review"), "fff.gui.artists_review_ui", "Review posts by artists from review_artists.yaml",
            options=[], original=A + "scripts/ui/artists_review_ui.py"),
    Command(("web",), "fff.gui.archive_browser", "Local web gallery for the archive (Flask)",
            original=A + "scripts/ui/archive_browser.py"),
]


GROUP_HELP = {
    ("e621",): "e621 archive: sync favorites, backfills, verification, AI cleanup, reports (was e621 archive)",
    ("e621", "backfill"): "Fill in missing data (deletion reasons, file paths, pools, relationships, hashes)",
    ("e621", "verify"): "Check and repair file paths, file names and post ids",
    ("e621", "report"): "Read-only reports",
    ("e621", "ai"): "Remove AI-generated content",
    ("e621", "favorites"): "Add, swap or look up favorites",
    ("e621", "files"): "Local files of deleted/unfavorited posts",
    ("fa",): "FurAffinity tools",
    ("fluffle",): "reverse search reverse image search and match processing (was reverse-image search)",
    ("gui",): "Desktop GUIs",
    ("legacy",): "One-off migrations kept from the current features",
    ("dupes",): "Duplicate finders",
    ("db", "report"): "Read-only database reports",
    ("db", "diagnose"): "Database diagnostics",
}


def _run(command: Command, args) -> int:
    module = importlib.import_module(command.module)
    func = getattr(module, command.func)
    if command.options is FORWARD:
        argv = list(args.rest)
    else:
        argv = []
        for flags, kwargs in command.options:
            dest = flags[0].lstrip("-").replace("-", "_")
            value = getattr(args, dest)
            if kwargs.get("action") == "store_true":
                if value:
                    argv.append(flags[0])
            elif value is not None:
                argv += [flags[0], str(value)]
    import sys

    saved_argv0 = sys.argv[0] if sys.argv else ""
    if sys.argv:
        sys.argv[0] = "fff " + " ".join(command.path)  # argparse's prog in the script's --help
    try:
        result = func(argv) if command.call == "argv" else func()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        if sys.argv:
            sys.argv[0] = saved_argv0
    return result if isinstance(result, int) else 0


def forwarded_argv(argv: list[str], command_path: tuple[str, ...]) -> list[str]:
    """Everything after the command's own words, e.g. ``fff --env test e621 sync -x`` -> ``["-x"]``.

    argparse's REMAINDER won't capture a leading ``--option`` after a subcommand, so forwarded
    commands take their arguments straight from argv instead.
    """
    n = len(command_path)
    for i in range(len(argv) - n + 1):
        if tuple(argv[i:i + n]) == command_path:
            return list(argv[i + n:])
    return []


def register(sub) -> None:
    """Add every command to the top-level subparsers ``sub``."""
    groups: dict[tuple, argparse._SubParsersAction] = {(): sub}
    parsers: dict[tuple, argparse.ArgumentParser] = {}
    existing = {name: p for name, p in sub.choices.items()}
    for command in COMMANDS:
        for depth in range(1, len(command.path)):
            prefix = command.path[:depth]
            if prefix not in groups:
                parent = groups[prefix[:-1]]
                parser = existing.get(prefix[0]) if depth == 1 else None
                if parser is None:
                    parser = parent.add_parser(prefix[-1], help=GROUP_HELP.get(prefix, f"{' '.join(prefix)} commands"))
                    parsers[prefix] = parser
                action = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
                if action is None:
                    action = parser.add_subparsers(dest="_".join(prefix) + "_command", metavar="<command>", required=True)
                groups[prefix] = action
        parent = groups[command.path[:-1]]
        description = command.help + (f"\n\nOriginal: {command.original}" if command.original else "")
        if command.options is FORWARD:
            p = parent.add_parser(command.path[-1], help=command.help, add_help=False, description=description,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
            p.add_argument("rest", nargs=argparse.REMAINDER, help="arguments for the original script (try --help)")
            p.set_defaults(forward_path=command.path)
        else:
            p = parent.add_parser(command.path[-1], help=command.help, description=description,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
            for flags, kwargs in command.options:
                p.add_argument(*flags, **kwargs)
        p.set_defaults(func=lambda args, c=command: _run(c, args))
