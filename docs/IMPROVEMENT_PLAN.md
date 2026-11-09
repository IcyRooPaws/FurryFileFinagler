# Improvement plan

Status: planned. Surveyed 2026-11-09 at commit `c0de038` (57.5k lines of Python, 226 tests passing).

This plan covers code-health work (parts 1–9) and new features (parts 10–21). Each part is
split into phases that can each be merged on their own. Sizes are rough: **S** is an evening,
**M** is a few sessions, **L** is a week or more of part-time work.

## Order

| Stage | Parts | Why this order |
|---|---|---|
| 1. Foundations | 8 (tooling), 1 (database layer), 3 (HTTP client) | Mechanical, low risk; everything after builds on them |
| 2. Safety net | 2 (schema in code), 13 (backups), 5 (exceptions) | A fresh install and a restore become possible |
| 3. Cleanup | 4 (logging), 7 (dead code), 9 (small fixes) | Gradual; done alongside other work |
| 4. Restructure | 6 (split large modules) | Easier once logging and the database layer are shared |
| 5. Features | 10–21 | In any order; dependencies are noted per part |

The SQLite backend (part 20) has its own plan in [SQLITE_BACKEND.md](SQLITE_BACKEND.md). Parts 1
and 2 here are its phases 1 and 2, without the SQLite-specific work.

---

# Code health

## Part 1. One database connection layer

**Status: done** (branch `refactor/db-connection-layer`). Only `fff/common/db.py` opens
connections or pools; `tests/test_db_layer.py` enforces it. New helpers: `connect_with`
(module settings dicts, whose host/port/user/password may override `database:`),
`try_connect_with` (print and return None), `connect_server` (no schema) and `get_pool`.
Also fixed on the way:
- `archive/db.py: connect_to_mysql` and `connect_to_image_sorting_hashes_db` built a new
  pool (5 and 3 connections) on every call; they now open one connection.
- The shared pool cache ignored connection options, so callers asking for different options
  could share a pool. The cache key now includes every option and the pool size.
- The fluffle helpers, `connect_to_mysql` and `fa_unfavorite` never passed `port`.
- `DatabaseManager` instances now share one 25-connection pool instead of one each.

**Goal:** every connection goes through `fff.common.db`, so the test-environment redirect
(`redirect_database`) always applies and a second backend can be added in one place.

**Today:** 16 files outside `fff/common/db.py` call `mysql.connector.connect` directly, and five
fluffle modules each define their own `connect_database()` / `init_database()`.

1. **Fluffle (S).** `search.py`, `fa_matches.py`, `e621_matches.py`, `rename_fa_authors.py`,
   `sync_external_fa_sources.py`, `sync_fa_favorites.py`, `verify_database.py`. Replace each
   local helper with `db.connect("fluffle")` / `db.connect_database(name)`.
   *Done when:* no `connect_database` definitions remain in `fff/fluffle/`.
2. **Sorter (S).** `features/tag_sorter.py`, `features/deleted_sorter.py`,
   `handlers/database_manager.py`, `scripts/diagnose_all_files_view.py`,
   `scripts/fix_no_source_filenames.py`.
3. **Archive and GUI (S).** `archive/fa_unfavorite.py`,
   `archive/scripts/tools/inspect_image_sorting_hashes_db.py`, `gui/tag_sort_config_ui_v2.py`.
   Background-thread users take `pooled=False` or their own pooled connection per thread.
4. **Guard test (S).** A test that fails if `mysql.connector.connect(` appears outside
   `fff/common/db.py` and `fff/common/schema_copy.py` (which connects without a database to
   create schemas). Allowed exceptions are listed in the test.
   *Done when:* the guard test passes and `fff --env test daily --dry-run` logs only `test_*` databases.

## Part 2. Schema defined in code

**Goal:** an empty MySQL server can be turned into a working FFF install from the repo alone.

**Today:** 27 of 49 tables have a `CREATE TABLE` in code. The per-folder hash tables,
`e621_perceptual_hashes`, `image_search.e621_links` and the `all_files` view exist only on the
live server. Schema changes are ad-hoc `ALTER TABLE` checks, for example in
`archive/db.py: create_tables_if_not_exists` (305 lines).

1. **Snapshot (S).** Dump the live schema of all three databases (`mysqldump --no-data
   --routines`) into `sql/schema/`. Interim recovery path and the reference for phase 2.
2. **Schema module (M).** `fff/common/schema.py` holds every table, index, foreign key and
   view. The per-folder sorter tables and `all_files` are generated from the folder list in config.
3. **`fff db init` (M).** Creates missing databases, tables and views; never drops or alters.
   `fff doctor` reports missing tables and points at `fff db init`.
4. **Versioned migrations (M).** A `schema_version` table and numbered migration functions
   replace the scattered `ALTER TABLE` / `information_schema` checks. Each module's
   `create_tables_if_not_exists` becomes a call to `db init` plus pending migrations.
   *Done when:* a test builds all three databases on an empty `test_*` server and the result
   matches the live schema (column names, types, indexes).

## Part 3. Shared HTTP client

**Goal:** every e621, FurAffinity and FuzzySearch request has the same retries, rate limiting,
timeout and User-Agent.

**Today:** `clients/e621.py: api_get` keeps rate-limit state in module globals, retries only on
`Timeout` (forever, with a 60 s sleep), and returns 429/5xx and connection errors to the caller.
The FA client backs off properly but with its own code. Nine modules outside `fff/clients/`
make their own `requests` calls: `archive/db.py`, `archive/download.py`,
`archive/file_verification.py`, `gui/blacklist_review_ui.py`,
`archive/scripts/tools/download_and_process_e621_db_exports.py` and four scripts in
`archive/scripts/favorites/`.

1. **`fff/clients/http.py` (M).** One `requests.Session` per service with a `urllib3.Retry`
   adapter (429, 500, 502, 503, 504, connection errors; capped attempts; honors `Retry-After`),
   a thread-safe per-host rate limiter, and the configured User-Agent.
2. **Configuration (S).** `e621.min_request_interval`, `e621.max_retries` and
   `furaffinity.min_request_interval` in `config.yaml`, defaulting to today's values (1 s for
   e621). Regenerate the example with `fff config make-example --force`.
3. **Move the e621 client onto it (S).** `api_get` becomes a thin wrapper; the globals go away.
4. **Move the FA and FuzzySearch clients (S).** `_fa_get` keeps its signature, so callers don't change.
5. **Move direct callers (M).** The nine modules above call the client functions. A guard test
   rejects `requests.get` / `requests.post` outside `fff/clients/`.
   *Done when:* tests that mock HTTP (with the `responses` library) cover a 429 then 200, a
   connection error then 200, and retries running out.

## Part 4. Logging instead of print

**Goal:** everything a scheduled run reports ends up in `logs/`, at a level that can be filtered.

**Today:** 1,968 `print(` calls. Two older setup modules (`archive/logging_setup.py`,
`sorter/logging_setup.py`) sit alongside `common/logging.py`. FA retry warnings are printed
only, so they never reach a log file.

1. **One setup (S).** Move the remaining callers of the two older `logging_setup.py` modules
   onto `fff.common.logging`, then delete them. Add a console handler that keeps the current
   look (colors, `[WARN]`-style prefixes).
2. **Clients and daily-path modules first (M).** `fff/clients/`, `archive/sync.py`,
   `fluffle/search.py`, `fluffle/fa_matches.py`, `fluffle/e621_matches.py`, `sorter/workflow.py`.
   `print` becomes `logger.info/warning/error`; progress bars stay as console output.
3. **Everything else (L, gradual).** Convert each module the next time it's edited. Progress is
   tracked by a count in this file.
   *Done when:* a failed FA request during `fff daily` appears in `logs/fluffle/`.

## Part 5. Exception handling

**Goal:** no error disappears without a log line.

**Today:** 17 bare `except:` and 83 `except Exception:` handlers. Bare excepts are in
`archive/db.py`, `archive/download.py`, `common/schema_copy.py`, `fluffle/search.py`,
`gui/blacklist_review_ui.py`, `gui/duplicate_manager_ui.py` and `sorter/cli/commands.py`.

1. **Bare excepts (S).** Replace each with the specific exception, or `except Exception` plus
   `logger.exception`. Ctrl+C then stops runs reliably.
2. **Swallowed exceptions (M).** Add `logger.exception` to every `except Exception` that
   returns or continues without logging. In the gallery's `/media` route, a database error
   returns 500, not 404.
3. **Lint rule (S).** Enable ruff `E722` (bare except) and `BLE001` (blind except) so new
   cases are flagged (needs part 8).

## Part 6. Split the largest modules

**Goal:** no file over ~1,500 lines and no function over ~150, so changes are reviewable
and the logic can be tested.

| Target | Size | Split |
|---|---|---|
| `archive/db.py` | 4,667 lines, 85 functions | `archive/db/` package: `posts.py`, `tags.py`, `relationships.py`, `paths.py`, `deletions.py`, `hashing.py` |
| `gui/duplicate_manager_ui.py` | 5,066 lines | `duplicates/model.py` (grouping, queries, similarity search), `widgets.py` (GIF, video, preview cache), `app.py` |
| `gui/tag_sort_config_ui_v2.py` | 4,238 lines | `tag_rules/model.py` (`RuleNode`, load and save), `panels.py`, `app.py` |
| `sorter/cli/commands.py` | 3,344 lines | One module per command group; `_fix_database_issues` (496 lines) and `_verify_data_consistency` (426) become one function per check |
| `fluffle/search.py: main` | 460-line function | Search loop, result routing and summary as separate functions |

1. **`archive/db.py` (M).** The package re-exports the old names from `__init__.py`, so imports
   don't change. Pure moves only, no behavior changes, in their own commit.
2. **Sorter commands (M).** Same approach. Add tests for each check that comes out of
   `_fix_database_issues`.
3. **Duplicate manager (L).** Move the data and query logic out first and test it without Tk,
   then split the widgets.
4. **Tag-rules editor (L).** Same order as the duplicate manager.
5. **Fluffle `main` functions (M).** `search.py`, `fa_matches.py` and `e621_matches.py`.
   *Rule for every phase:* the code is moved in one commit and changed in the next, and
   `python tools/gui_smoke.py` passes after each GUI split.

## Part 7. Dead and duplicate code

1. **Tag-rules editor v1 (S).** Point `sorter/cli/commands.py:3298` at
   `tag_sort_config_ui_v2`, then delete `gui/tag_sort_config_ui.py`.
2. **Old hasher (S–M).** `fff/sorter/handlers/media_hasher/` (~960 lines) is reachable only
   through `fff hash` ("v1 media_hasher CLI", via `fff/sorter/hash_cli.py`). Move `fff hash`
   onto `fff/hashing`, or retire it, then delete the package.
3. **Finished one-off scripts (S).** Review `archive/scripts/database/` (the `backfill_*`
   scripts, `migrate_ai_artists_to_db`, `update_artist_filenames`, ...). Delete scripts whose
   job is done and that the manager and daily run don't call; their commands drop out of
   `COMMANDS.md`.
4. **Stale files (S).** Remove `__pycache__` copies whose source is gone (for example
   `config_migrate`). They are already gitignored, so this is local cleanup only.

## Part 8. Tooling

1. **CI (S).** A GitHub Actions workflow runs `pytest` on Windows and Python 3.12 for every push
   and PR. The suite needs no database and takes about 6 s.
2. **Ruff (S).** `[tool.ruff]` in `pyproject.toml` with a small starting rule set (`E9`, `F`,
   `W605`, `E722`), plus a pre-commit hook. Fix what it reports, starting with the invalid `"\T"`
   escape in `archive/scripts/file_management/restore_deleted_files.py:19`. Add rules gradually.
3. **One dependency list (S).** `pyproject.toml` stays the source of truth. `requirements.txt`
   becomes a pinned lock file generated with `pip-compile` (or `uv pip compile`).
4. **Test gaps (M–L).** Mocked-HTTP tests for the code that changes things on remote services:
   e621 favorite and unfavorite, AI cleanup, FA favorite and unfavorite. Then archive sync
   against the test databases (`-m integration`).

## Part 9. Small fixes

1. **(S)** Make the e621 request interval and the 5,000-call pause configurable (combine with
   part 3, phase 2).
2. **(S)** Add a `sql_in(values)` helper for `IN (%s, ...)` lists and a whitelist check for
   table names in f-string SQL (138 `execute(f"...")` calls today; all reviewed ones are safe).
3. **(S)** Fix the README typo "destory".

---

# Features

## Part 10. Run dashboard and notifications

Depends on: part 4 phase 2 (structured counts in logs).

1. **Machine-readable run summary (S).** `fff daily` also writes `logs/daily/<run>.json` with
   each stage's status, duration and counts (downloaded, moved, favorited, deleted).
2. **Manager page (M).** A "Runs" page lists recent runs, shows stage status at a glance, and
   opens the log for a stage.
3. **Notifications (S).** Optional `notifications:` in config with a Windows toast, a Discord
   webhook or ntfy. Sent when a stage fails, or for every run.

## Part 11. Undo file operations

Depends on: `file_operations` (exists).

1. **`fff ops list` (S).** Shows recent runs and operations from `file_operations`, grouped by run.
2. **`fff ops undo --run <id> | --since <time>` (M).** Reverses moves and renames in reverse
   order, skips entries whose target has changed since, and updates database paths. Dry run by default.
3. **Manager button (S).** An "Undo last sort run" button with a preview.

## Part 12. Resumable long jobs

1. **Checkpoint table (S).** `job_progress(job, key, value, updated_at)` in the sorter database.
2. **Favorites sync (M).** Store the last completed page or post ID and resume from it.
3. **Hashing and archive scans (M).** Store the last processed path for each folder.

## Part 13. Database backups

1. **`fff db backup` (S).** Runs `mysqldump` for the three databases into a configured folder
   and keeps the last N copies.
2. **Daily stage (S).** An optional first stage of `fff daily`, plus a `fff doctor` warning when
   the newest backup is more than N days old.
3. **`fff db restore` (M).** Restores into `test_*` by default; production only with `--prod`
   and a confirmation step.

## Part 14. Archive gallery upgrades

1. **Search (M).** Tag search with `-tag` exclusion, `rating:` and `type:`, and sorting by score,
   favorite date or upload date.
2. **Browsing (M).** Infinite scroll, keyboard navigation, a slideshow and video autoplay.
3. **Post detail (S).** Source links (e621, FA), pools, relationships, and local duplicates
   from the hash tables.

## Part 15. Library health report

1. **Queries (M).** Files on disk with no database row, rows whose file is missing, favorited
   posts with no local file, and size per library folder. Reuse `sql/sorter/` and the
   existing verifiers.
2. **`fff report health` (S).** Console summary plus an HTML report in `logs/reports/`.
3. **Manager page (S).** Same report, with buttons for the matching fix commands.

## Part 16. Faster perceptual-hash duplicate search

Do this only if the Duplicates tool is noticeably slow.

1. **Measure (S).** Time the similarity search at current library size.
2. **Index (M).** A BK-tree (or multi-index hashing) over the image perceptual hashes, built on
   load and reused across searches.
3. **Video (M).** The same for video fingerprints in `hashing/video_similarity.py`.

## Part 17. More source sites

1. **Detection (S).** Recognize Bluesky, Weasyl, Inkbunny and X/Twitter URLs in e621 sources
   and FuzzySearch results, and store them in `image_search`.
2. **Downloads (M per site).** A client for each site in `fff/clients/`, using the shared HTTP
   client (part 3). Start with Bluesky, since it is already an inbox folder.
3. **Routing (S).** New "found on X" destinations and file naming rules.

## Part 18. Watch-folder mode

1. **`fff watch` (M).** Watches the inbox folders (`watchdog`) and waits until a new file has
   stopped changing.
2. **Pipeline (M).** Runs source search and sorting for that file only, under the same
   single-instance lock as `fff daily`.
3. **Manager toggle (S).** Start and stop from the manager, with activity in the log.

## Part 19. Artist tracking

1. **Watch list (S).** Track artists from `sort_artists.yaml` plus an opt-in list.
2. **New-post check (M).** For each tracked artist, find e621 and FA posts newer than the last
   check that aren't in the archive.
3. **Review (M).** A manager page to favorite, download or skip each one.

## Part 20. SQLite backend

See [SQLITE_BACKEND.md](SQLITE_BACKEND.md) (8 phases). Parts 1 and 2 of this plan cover its
phases 1 and 2.

## Part 21. Packaged release

Depends on: part 20, so users don't need a MySQL server.

1. **Build (M).** PyInstaller (or a `uv`-managed install) produces a Windows folder with `fff.exe`
   and the manager.
2. **First run (S).** The setup flow runs `fff db init` and creates the config from the examples.
3. **Releases (S).** CI builds on tag push and attaches the zip to a GitHub release.
