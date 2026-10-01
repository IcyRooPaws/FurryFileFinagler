# fff command reference

Generated from the parsers by `python tools/gen_commands_doc.py`; do not edit by hand.
Global options: `fff [--config PATH] [--env prod|test] <command>`.

## Project commands

| Command | What it does |
|---|---|
| `fff setup` | Build config.yaml/secrets.yaml from the original three projects |
| `fff config make-example` | Write config/config.example.yaml from config.yaml, sanitized |
| `fff config test-dirs` | Show where each configured folder goes in the test environment |
| `fff config show` | Print the config (secrets redacted) |
| `fff db copy-schema` | Create/refresh the test_* databases as empty copies of production's structure |
| `fff db copy-data` | Replace the test_* databases with a full copy of production (schema + rows) |
| `fff db rewrite-test-paths` | Point file paths stored in the test_* databases at the test tree (E:/Testing/...) |
| `fff doctor` | Check databases, folders and dependencies |

## Ported commands

Each runs the command implementation listed; options are the existing code's unless noted.

| Command | What it does | Original |
|---|---|---|
| `fff e621 sync` | Sync e621 favorites into e621_posts_db (full pipeline) | `e621 archive/e621_main.py` |
| `fff e621 backfill deletion-reasons` | Scrape deletion reasons and takedown details for deleted posts | `e621 archive/scripts/database/backfill_deletion_reasons.py` |
| `fff e621 backfill file-paths` | Fill missing posts.file_path from the image_sorting_hashes database | `e621 archive/scripts/database/backfill_file_paths_from_image_sorting.py` |
| `fff e621 backfill file-paths-fast` | Bulk file_path backfill/verify via the all_files view | `e621 archive/scripts/database/fast_backfill_file_paths.py` |
| `fff e621 backfill pools` | Pool links from cached pages, plus pool metadata from the API | `e621 archive/scripts/database/backfill_pools.py` |
| `fff e621 backfill relationships` | Parent/child relationships from cached pages | `e621 archive/scripts/database/backfill_post_relationships.py` |
| `fff e621 backfill phash-cache` | Hash local files into e621_perceptual_hashes | `e621 archive/scripts/database/build_perceptual_hash_cache.py` |
| `fff e621 verify post-ids` | Check post ids in filenames against the DB (with a review GUI) | `e621 archive/scripts/database/validate_post_ids_in_filenames.py` |
| `fff e621 rename-artists` | Rename files whose artist tags changed | `e621 archive/scripts/database/update_artist_filenames.py` |
| `fff e621 report deletion-timestamps` | Report how accurate deleted_at timestamps are | `e621 archive/scripts/database/check_deletion_timestamp_accuracy.py` |
| `fff e621 report takedowns` | Interactive takedown viewer | `e621 archive/scripts/database/view_takedown_details.py` |
| `fff e621 ai cleanup-deleted` | Posts e621 deleted as AI: snapshot, unfavorite, optionally trash files, delete from DB (dry run by default) | `e621 archive/scripts/database/cleanup_ai_generated_posts.py` |
| `fff legacy add-deletion-reason-column` | One-off migration: add posts.deletion_reason | `e621 archive/scripts/database/add_deletion_reason_column.py` |
| `fff legacy migrate-ai-archive-text` | One-off: import data/ai_generated_posts_archive.txt into the DB | `e621 archive/scripts/database/migrate_ai_archive_text_to_db.py` |
| `fff legacy migrate-ai-artists` | One-off: import data/ai_artist.md into ai_flagged_artists | `e621 archive/scripts/database/migrate_ai_artists_to_db.py` |
| `fff e621 verify file-paths` | Verify posts.file_path and repair broken paths | `e621 archive/scripts/database/verify_and_repair_file_paths.py` |
| `fff e621 verify file-names` | Make posts.file_name agree with the files on disk | `e621 archive/scripts/database/verify_and_fix_file_names.py` |
| `fff e621 match-downloads` | Build dl_fav/dl_nsfw/dl_sfw tables and match downloaded files to posts | `e621 archive/scripts/database/create_download_tables_and_match_files.py` |
| `fff e621 ai remove-tagged` | Remove posts with AI tags (move file, unfavorite, downvote, delete from DB) | `e621 archive/scripts/favorites/remove_ai_tagged_posts.py` |
| `fff e621 ai remove-artists` | Remove posts by artists listed in data/ai_artist.md | `e621 archive/scripts/favorites/remove_ai_artist_posts.py` |
| `fff e621 favorites add-missing` | Favorite e621 posts the sorter knows about but that aren't favorited, then sync | `e621 archive/scripts/favorites/favorite_missing_posts.py` |
| `fff e621 favorites swap-duplicates` | Move favorites from 'inferior/duplicate of' posts to their replacements | `e621 archive/scripts/favorites/swap_duplicate_favorites.py` |
| `fff e621 favorites from-md5` | Look up md5s from data/md5_search/md5.txt and favorite the matches | `e621 archive/search_for_posts/md5_favorite_checker.py` |
| `fff e621 files find-deleted` | Look for local files of deleted posts (checks 10 posts, as the original did) | `e621 archive/scripts/file_management/check_deleted_posts_files_enhanced.py` |
| `fff e621 files unfavorited` | Interactive menu for unfavorited posts (archive / delete) | `e621 archive/scripts/file_management/manage_unfavorited_posts.py` |
| `fff e621 files restore` | Restore files listed in a deleted-files CSV (one-off tool) | `e621 archive/scripts/file_management/restore_deleted_files.py` |
| `fff e621 report favorites-diff` | Compare database post ids with live favorites | `e621 archive/scripts/favorites/analyze_favorites_difference.py` |
| `fff e621 report relationships` | Print parent/child relationship statistics | `e621 archive/scripts/tools/check_relationship_stats.py` |
| `fff e621 db-export` | Download e621 DB exports and load them into e621_db_dump_* tables | `e621 archive/scripts/tools/download_and_process_e621_db_exports.py` |
| `fff e621 reset-db` | Drop all e621_posts_db tables (asks to confirm) | `e621 archive/scripts/database/reset_database.py` |
| `fff db inspect-hashes` | Print the image_sorting_hashes schema and hash columns | `e621 archive/scripts/tools/inspect_image_sorting_hashes_db.py` |
| `fff fluffle search` | Reverse-search images with reverse search (+FuzzySearch), process e621/FA matches, sort the rest | `reverse-image search/fluffle.py` |
| `fff fluffle process-e621` | Process stored exact e621 matches: favorite, download, move existing code (applies the blacklist) | `reverse-image search/scripts/process_e621_matches.py` |
| `fff fluffle process-fa` | Process stored exact FA matches (no e621 match): favorite, download, move existing code | `reverse-image search/scripts/process_furaffinity_matches.py` |
| `fff fluffle sync-fa-favorites` | Favorite on FA the images that matched both e621 and FA | `reverse-image search/scripts/sync_fa_favorites.py` |
| `fff fluffle verify-db` | Print recent rows and counts from image_search | `reverse-image search/scripts/verify_database.py` |
| `fff fa rename-authors` | Rename *-FA-* files from author name to author id (mappings from the reverse search DB; interactive) | `reverse-image search/scripts/rename_fa_author_files.py` |
| `fff fa rename-authors-manual` | Same, with mappings from config/fa_author_mappings.yaml (interactive) | `reverse-image search/scripts/rename_fa_author_manual.py` |
| `fff sort` | media sorter: sort, archive, tag/pool/artist sort, maintenance, duplicates, workflow (was main.py/run.py) | `media sorter/main.py`, `media sorter/run.py`, `media sorter/fa_backlog_sort.py`, `media sorter/fff.sorter/__main__.py` |
| `fff e621 fix-no-source-names` | Rename files whose path name differs from posts.file_name (excludes Fav/Artists, Tag Sorted, ...) | `media sorter/fix_no_source_filenames.py` |
| `fff dupes advanced` | Multi-stage duplicate finder (KNOWN BROKEN, project notes section 13) | `media sorter/scripts/find_duplicates_advanced.py` |
| `fff db report space` | Sum file_size per image_sorting_hashes table | `media sorter/table_space_report.py` |
| `fff db report archive-tables` | List the archive_* tables | `media sorter/check_archive_tables.py` |
| `fff db diagnose all-files-view` | Diagnose the all_files view (artist-scan filters) | `media sorter/test_all_files_view.py` |
| `fff db diagnose op-logging` | Exercise file_operations logging (writes and deletes test rows) | `media sorter/test_file_operations_logging.py` |
| `fff legacy migrate-artist-columns` | One-off: add last_artist_check / artist_matched to every table | `media sorter/migrate_artist_columns.py` |
| `fff legacy migrate-paths-forward-slashes` | One-off: rewrite stored file_path values with forward slashes | `media sorter/migrate_paths_to_forward_slashes.py` |
| `fff legacy fix-file-hash-column` | One-off: widen file_operations.file_hash (obsolete) | `media sorter/fix_file_hash_column.py` |
| `fff legacy remove-file-hash-column` | One-off: drop file_operations.file_hash | `media sorter/remove_file_hash_column.py` |
| `fff hash` | Hash files / show info / find duplicates (v1 media_hasher CLI) | `media sorter/_handlers/media_hasher/cli.py` |
| `fff gui duplicates` | Duplicate manager GUI | `media sorter/scripts/duplicate_manager_ui.py` |
| `fff gui tag-sort-config` | Tag sorting rule editor GUI | `media sorter/scripts/tag_sort_config_ui_v2.py`, `media sorter/scripts/tag_sort_config_ui.py` |
| `fff gui artist-config` | Artist config editor GUI (sort_artists.yaml) | `media sorter/scripts/artist_config_ui.py` |
| `fff fa sync-external` | Track FA source links from e621 posts and favorite them on FA | `e621 archive/FA Fav Sync/sync_external_fa_sources.py` |
| `fff gui blacklist-review` | Review blacklisted posts (auto-deletes critical/AI-tagged posts first) | `e621 archive/scripts/ui/blacklist_review_ui.py` |
| `fff gui artist-review` | Review posts by artists from review_artists.yaml | `e621 archive/scripts/ui/artists_review_ui.py` |
| `fff web` | Local web gallery for the archive (Flask) | `e621 archive/scripts/ui/archive_browser.py` |

## `fff sort` sub-commands

`fff sort <command>` is `python main.py <command>` from media sorter (same flags).

| Command | What it does |
|---|---|
| `fff sort adupe` |  |
| `fff sort advanced-duplicates` | Find duplicates using multi-stage pipeline (fast and efficient) |
| `fff sort archive` | Archive old files |
| `fff sort artist-config` | Configure favorite artists (GUI) |
| `fff sort artist-sort` | Sort files by favorite artists |
| `fff sort cleanup` | Delete empty folders |
| `fff sort duplicates` | Manage duplicate files |
| `fff sort maintenance` | Run maintenance tasks |
| `fff sort pool-sort` | Sort files by e621 pool membership |
| `fff sort post-id` | Manage files by e621 post IDs |
| `fff sort setup` | Initial setup and configuration |
| `fff sort sort` | Sort and organize files |
| `fff sort status` | Show system status |
| `fff sort tag-sort` | Sort files by e621 tags |
| `fff sort tag-sort-config` | Configure tag sorting rules (GUI) |
| `fff sort unarchive` | Restore recent files from archives |
| `fff sort workflow` | Full workflow: deleted posts, tag sort, sort+hash, artists, archive, maintenance, cleanup (was run.py) |
| `fff sort fa-backlog` | Move -FA- files into their 'Furaffinity - <Rating>' folders (was fa_backlog_sort.py) |
