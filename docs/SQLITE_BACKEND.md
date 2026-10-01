# SQLite backend plan

Status: planned. Nothing here is implemented yet.

## Goal

Add a `database.backend: mysql | sqlite` setting. MySQL stays the default and keeps working
as it does today. With `sqlite`, FFF runs with no database server: three local database
files, created from scratch by FFF. SQLite is an option alongside MySQL, not a replacement.

## Current state (surveyed 2026-10-01, MySQL 8.4)

| | |
|---|---|
| Databases | `e621_posts_db` (16 tables), `image_sorting_hashes` (24), `image_search` (9). No table names collide. About 480 MB and 2.1M rows; the largest table is `posts_tags` (1.67M rows). |
| Connections | `fff/common/db.py` is the intended single entry point, but 18 other files call `mysql.connector.connect` directly. |
| Schema in code | Only 27 of the 49 tables have a `CREATE TABLE` in the code. The 20 per-folder hash tables in `image_sorting_hashes` (`favorites`, `nsfw_image`, `archive_sfw_videos`, ...), `e621_perceptual_hashes` and `image_search.e621_links` exist only on the server, as does the `all_files` view. A fresh install, MySQL or SQLite, can't be built from the code today. |
| Views | 5 in `image_sorting_hashes`, 1 in `e621_posts_db`. Only `all_files` is used by FFF. |
| Server-only objects | 7 `GetTop...` stored procedures and the cross-database daily-totals views. FFF doesn't use them. |
| Other features | No triggers. 19 foreign keys. 4 `ON UPDATE CURRENT_TIMESTAMP` columns, 8 JSON columns, no ENUMs. Collation `utf8mb4_0900_ai_ci`, so comparisons ignore case and accents. |
| SQL volume | 571 `execute` calls in 63 files. |
| Threads | Background threads use the database in `hash_manager`, `archive_manager`, `file_verification` and three GUIs. |

MySQL-specific SQL in the code:

| Construct | Lines / files | SQLite equivalent |
|---|---|---|
| `%s` placeholders | 165 / 29 | Translated to `?` by the cursor wrapper; no query edits. |
| `cursor(dictionary=True)` | 29 / 16 | `row_factory` in the wrapper. |
| Backtick identifiers | 145 / 25 | Work unchanged; SQLite accepts them. |
| `ON DUPLICATE KEY UPDATE` | 27 / 9 | `ON CONFLICT (...) DO UPDATE`, with `VALUES(x)` becoming `excluded.x`. |
| `INSERT IGNORE` / `REPLACE INTO` | 25 / 5 | `INSERT OR IGNORE` / `INSERT OR REPLACE`. |
| `NOW()`, `DATE_SUB`, `INTERVAL`, `DATEDIFF` | 71 / 26 | `datetime('now', 'localtime')`, `datetime(x, '-7 days')`. SQLite's `'now'` is UTC; MySQL's `NOW()` is local time. |
| `information_schema`, `SHOW ...` | 72 / 15 | `sqlite_master`, `PRAGMA table_info`. |
| `ALTER TABLE` | 36 / 5 | `ADD COLUMN` works; changing a column's type needs a table rebuild. |
| `AUTO_INCREMENT`, `ENGINE=`, `CHARSET` | 45 / 12 | `INTEGER PRIMARY KEY AUTOINCREMENT`; drop the rest. |
| `CONCAT`, `IF`, `IFNULL`, `GROUP_CONCAT`, `SUBSTRING_INDEX`, `LOCATE`, `JSON_LENGTH` | about 40 | `\|\|`, `iif`, `ifnull`, `group_concat`, `instr`, `json_array_length`; `SUBSTRING_INDEX` needs a Python function registered with SQLite. |
| `mysql.connector.Error` and error codes | 74 / 16 | One `fff.common.db.Error` family that covers both drivers. |

## Design

- **One database layer.** `fff.common.db` returns a MySQL or SQLite connection depending on
  the backend setting. On SQLite, a cursor wrapper makes calling code look the same as with
  MySQL: `%s` placeholders, `dictionary=True`, `lastrowid`, the same exception classes.
- **One schema definition** that produces the table-creation SQL for both backends. The
  per-folder sorter tables and `all_files` are generated from the folder list in config.
- **A dialect module** with `upsert`, `insert_ignore`, `now`, `date_sub`, `group_concat`,
  `table_exists` and `columns`. It is the only place backend-specific SQL lives. About 60
  statements move onto it; the rest is already portable.
- **SQLite layout.** Files at `data/db/<env>/<name>.sqlite`; the `test` environment gets its
  own files. The other two databases are attached under their current names, so qualified
  names like `image_sorting_hashes.favorites` keep working. WAL mode, `busy_timeout` and
  `foreign_keys=ON` are switched on. One connection per thread, no pool.

## Phases

1. **Database layer and backend setting.** Route the 18 direct `mysql.connector.connect`
   callers through `fff.common.db`. Add the SQLite connection and cursor wrapper. With the
   backend left at `mysql`, behavior is unchanged.
2. **Schema definition and `fff db init`.** Collect all 49 tables, `all_files`, indexes and
   foreign keys into one definition, using the live server as the reference. `fff db init`
   creates missing tables on either backend. On SQLite, the 4 auto-updated timestamp columns
   become `AFTER UPDATE` triggers, and name, tag, artist and path columns get
   `COLLATE NOCASE` to match MySQL's case-insensitive lookups. This phase is useful on its
   own: it makes a fresh MySQL install possible too.
3. **Rewrite non-portable SQL** with the dialect helpers, area by area: archive, sorter,
   fluffle, then the GUIs. Add a test that fails if MySQL-only syntax appears outside the
   dialect module, so new code stays portable.
4. **Tools that inspect the database.** `doctor`, `db inspect-hashes` and the `all_files`
   diagnostics learn both backends. On SQLite, `db copy-schema` and `db copy-data` become
   file copies (`VACUUM INTO`) into the test folder.
5. **Copy data between backends.** `fff db copy --from mysql --to sqlite` (and the reverse)
   copies in batches and checks row counts and per-table checksums. Use it to seed SQLite
   from existing data or to move back. Type mapping: datetimes become ISO text, JSON becomes
   text, `tinyint(1)` becomes 0/1, DECIMAL becomes REAL (review the 18 DECIMAL columns first).
6. **Concurrent writes on SQLite.** A scheduled `fff daily` can run while the manager is
   open, and SQLite allows one writer at a time. Keep write transactions short (batch
   commits in hash and archive scans), and add a test with two concurrent writers.
7. **Tests on both backends.** A shared database fixture runs the suite against in-memory
   SQLite always, and against the test MySQL databases when `FFF_INTEGRATION=1`. Parity
   tests check that the same commands leave the same rows on both backends.
8. **Setup, Settings and docs.** The Settings tool and setup flow get a backend choice;
   choosing SQLite hides host, user and password and runs `fff db init`. `doctor` reports
   the active backend. README and USAGE describe both options.

## Not available on SQLite

The `GetTop...` stored procedures and the cross-database daily-totals views stay MySQL-only.
FFF doesn't use them.

## Risks

- **Two backends to maintain, permanently.** Every new query must work on both. The
  portability test (phase 3) and the both-backends test run (phase 7) enforce it.
- **Behavior differences between backends.** The likely ones:
  - case- and accent-insensitive matching: `NOCASE` covers ASCII case only and can't ignore accents;
  - local time vs UTC;
  - the row count reported after an upsert (MySQL reports 2 for an update, SQLite 1);
  - loose typing (`'123' = 123`);
  - `GROUP_CONCAT(DISTINCT ... SEPARATOR ...)`, which SQLite can't combine (9 uses).

  The parity tests are there to catch these.
- **Local disk only.** SQLite files must not sit on a network share. Use the MySQL backend
  when several machines need the same data.

## Scripts outside the main code paths

`fff/legacy`, `fff/archive/scripts` and `fff/sorter/scripts` hold 34 files that run SQL.
Most are live commands, so they are grouped here:

- **One-off migrations (6):** deleted on 2026-10-01 (`fff legacy add-deletion-reason-column`,
  `migrate-ai-archive-text`, `migrate-artist-columns`, `migrate-paths-forward-slashes`,
  `fix-file-hash-column`, `remove-file-hash-column`). `fff legacy migrate-ai-artists` remains;
  it runs no SQL of its own.
- **Used by the manager or `fff daily` (9):** `favorites add-missing`, `verify file-paths`,
  `report takedowns`, `files find-deleted`, `files unfavorited` and `db inspect-hashes` are
  manager buttons. `backfill_deletion_reasons`, `remove_ai_artist_posts` and
  `remove_ai_tagged_posts` are imported by `fff/archive/sync.py`. These must be ported.
- **Standalone commands (19):** backfills, reports, verifiers, diagnostics, `e621 reset-db`
  and `e621 db-export`. Decide per command whether to port, mark MySQL-only, or delete.
