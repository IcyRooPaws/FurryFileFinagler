# Library consistency plan

Status as of 2026-11-19: phases 0 and 1 are committed, phase 1 has not yet been run against the
real library, phases 2 to 6 are open.

**Goal:** when `fff daily` ends, no file has a stale path in any database, and what the
databases say about an e621 post matches e621.

This plan came out of an audit on 2026-11-19 (49 findings, each re-checked by a second reviewer:
37 confirmed, 12 corrected in part, none refuted). It is separate from
[IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md), which covers code health and new features. Sizes use
the same scale: **S** is an evening, **M** a few sessions, **L** a week or more.

## Order

| Order | Phase | Status |
|---|---|---|
| 1 | 0. Tag sort writes the new path through | Done, committed (`db5c1b9`) |
| 2 | 1. Posts that already have a file follow e621 | Committed; not yet run for real |
| 3 | 4. Safety guards | Open, next |
| 4 | 2. Re-evaluate files already in tag folders | Open |
| 5 | 3. Guarantee paths at the end of a run | Open |
| 6 | 5. Remaining small fixes | Open |
| 7 | 6. Duplicate manager | Open |

Phase 4 was pulled ahead of phase 2: e621's v2 API becomes the default around December 2026, and
phase 1 changes the same sync the two data-loss guards protect. The phase numbers are the ones
used while planning, so they are not in running order.

## Decisions made so far

- **e621 is the source of truth for e621 posts, including the file.** When e621 replaces a
  post's file, the local file is replaced too.
- **Source links are the exception: they are only ever added.** A link e621 later removes from a
  post is kept.
- **Old files are kept, not deleted.** A replaced file moves to
  `archive.e621.replaced_directory`.
- **Phase 4 before phase 2** (see above).

## Still to decide

- **1,318 posts whose local file is not e621's file although e621 did not change it.** Most of
  these local files are larger than e621's, so they look like originals from another source.
  Nothing touches them today. Decide whether "source of truth" should cover them. Five of the 22
  restored posts (phase 1) are in this group: they hold a lower-resolution stand-in fetched while
  the post was deleted.
- **Takedown links of restored posts.** Kept today, as history, the way source links are kept.
  The alternative is to remove them when a post is restored.
- **Posts removed as AI-deleted that e621 later restores.** They are no longer favorites, so the
  sync cannot see them. A report-only command could re-check the 47 archived ids in one request.
- **Whether to try phase 1 without touching files first.** Setting
  `redownload_replaced_files: false` for one run applies every database change and only logs the
  224 changed files.

---

## Phase 0. Tag sort writes the new path through (S)

**Status: done**, committed as `db5c1b9`.

**Problem.** In `fff daily` the e621 sync wrote `posts.file_path`, and the second sort workflow
then moved the file into a tag folder and updated only the sorter database. Ten posts ended
that day's run with a path that no longer existed (post 6857241 was the one that showed it).

**Done.** `TagSorter.move_files_to_destination` (tag rules and pool rules) now updates
`posts.file_path` after each batch of moves:
- a post whose path was the file's old location is re-pointed;
- a post named in the file name whose recorded file is missing is re-pointed too;
- a post that points at another copy that still exists is left alone;
- a database failure never fails the sort.

**Not covered here:** every other mover (main sort, artist sort, archive, duplicate manager).
That is phase 3.

---

## Phase 1. Posts that already have a file follow e621 (M)

**Status: committed, not yet run against the real library.**
528 tests pass, including an integration test that runs the real statements and the whole
file-replacement flow against the `test_*` databases. Two rounds of independent review were
applied.

**Problem.** `fff e621 sync` re-ingested a post in full only when it was new, deleted on e621, or
its file was missing. Every other favorite got its tag links compared and nothing else, so its
rating, sources, pools, relationships, deleted flag and the file itself stayed as they were on
the day it was first ingested.

**Done** (new module `fff/archive/metadata_sync.py`, called from `fff/archive/sync.py`):

1. **Post details.** Rating, file URL, `updated_at`, pool membership and parent/child links are
   compared with e621 and rewritten where they differ. Everything is compared first and written
   in one transaction.
2. **Restored posts.** A post e621 no longer lists as deleted has its deleted flag, deletion date
   and deletion reason cleared, and the log names the post ids.
3. **Source links** are added, never removed.
4. **Tag categories.** `tags.tag_type` follows e621 when a tag is recategorised.
5. **`lastChecked`** is stamped for every post compared, so it means "last compared with e621".
6. **Replaced files.** When e621's md5 for a post differs from the stored one:
   - a local copy that already is e621's file is kept and only the row is corrected (a file with
     the right content under the old extension is renamed);
   - otherwise the new file is downloaded and checked against e621's md5, copied into the old
     file's folder under a temporary name, the old copies are moved to the replaced directory,
     and the new file is renamed into place. A hand-placed file keeps its placement.
7. **The ingest keeps the stored md5** of a post that has a local file, so a re-ingest can no
   longer hide a file e621 replaced. The missing-file download records the md5 of the file it
   fetched.
8. **The artist/rating rename** now updates the sorter row and writes to `file_operations`.

**Guards built in:**
- nothing is replaced when more than 500 posts (or 2% of the library) report a changed file;
- pool and relationship links are left alone when more than 100 posts (or 1%) would lose all of
  theirs in one run;
- a file in a protected directory, a file two posts record, or a file open in another program is
  skipped;
- replacement stops after five failures in a row;
- file replacement is off unless `archive.e621.redownload_replaced_files` is true.

**What the first real sync will change** (rehearsed read-only against production on 2026-11-19):

| Change | Posts |
|---|---|
| Files downloaded and swapped in place (452 MB) | 214 |
| Row corrected only, a local copy is already current | 10 |
| Rating corrected | 17 |
| Restored posts un-marked as deleted | 22 |
| Source links added (none removed) | 827 |
| Parent/child links added or corrected | 1,843 |
| Pool membership corrected | 37 |
| Tag categories corrected | 38 |
| `updated_at` refreshed | 37,408 |

Side effect to expect: the newly added source links include about 150 FurAffinity submissions,
which the external FA sync at the end of the run will favorite on FurAffinity.

**Left to do in this phase:**
1. Run one real sync and compare the log with the table above.
2. Add a changelog entry and version for it (not done with the commit).

**Restored posts ("no longer deleted").** Checked by three independent traces and a verifier on
2026-11-19. All 22 posts restored today are detected and un-marked. Closed as a result:
- a restored post that goes through the full ingest (its file was missing) now also loses its old
  deletion date and reason, and is named in the log (`clear_restored_posts`);
- a restored post whose file also changed no longer keeps the e621 post-page link as its file URL.

Confirmed and left open, none of which affects today's 22:
- `post_takedowns` rows of a restored post are kept, so the takedown report still lists it (21
  rows after the first sync). See "Still to decide".
- A post deleted for an AI reason, or unfavorited by `swap-duplicates`, is removed from the
  favorites by the next sync, so a later restore of it can never be seen (47 posts are in the AI
  archive).
- With `move_deleted_posts` on (it is off), nothing moves a restored post's file back out of the
  deleted folder.
- A restore and a second deletion that both happen between two runs keep the first deletion's
  date and reason.

*Done when:* after a sync, comparing the fetched pages with the database shows no differences
in rating, deleted flag, pools and relationships, no e621 source link is missing locally, and no
post's stored md5 differs from e621's except the ones the log names as skipped.

---

## Phase 4. Safety guards (M)

**Status: open, next.**

1. **Unreachable library drive (S).** `verify_and_repair_file_paths` sets `file_path` to NULL for
   every post whose file it cannot find, with no check that the library root is reachable. A
   sync with the drive offline would clear every path and queue the whole library for download.
   Add an abort threshold, and treat a failed sorter lookup as "unknown", not "not found".
2. **Partial favorites list (S).** A partial or stale page cache is accepted as the full list, so
   every post missing from it is handled as unfavorited: archived, its files moved, its row
   deleted. The cache cleanup runs in the wrong order, which disables the existing "any stale
   page means refetch" rule: a sync started 15 to 20 minutes after the previous fetch began
   loads only the younger pages (confirmed by simulation: 80 of 125). An interrupted fetch
   leaves the same kind of partial set. Accept a cached session only when it is complete (a
   completion marker, or pages 1..N with an empty last page, all inside the age limit), or drop
   cache reuse. Also refuse the unfavorite step when the fetched count is well below the number
   of posts in the database. Until this is done, do not start a second `fff e621 sync` within
   20 minutes of the previous one. Four posts in `unfavorited_posts_archive` are favorites again
   and may be earlier victims of this.
3. **e621 v2 API (M).** The fluffle e621 client reads only the old response shape, and several
   single-post calls do not ask for the extended tag format. When v2 becomes the default these
   stop seeing tags. Make them read both shapes, with fixture tests for each, and make the tag
   refresh fail loudly when many posts arrive without a tag list.
4. **A response without `flags` (S).** The full ingest reads a missing flags object as "not
   deleted", which would un-delete a deleted post and drop its placeholder URL. Refuse the run
   when posts arrive without it.

*Done when:* a sync started with the library drive disconnected changes no paths, a sync fed a
truncated favorites list unfavorites nothing, and the fluffle client passes its tests on both
response shapes.

---

## Phase 2. Re-evaluate files already in tag folders (L)

**Status: open.**

**Problem.** The tag sorter only moves files into rule folders. A file whose post later loses the
matching tag, gains an excluded tag, or whose rule is edited stays where it was first put.

1. **Fix the tag-rule error (S).** `_drop_files_named_after_favorite_artists` returns nothing
   where a count is expected, so 15 rules raise an error on every run, after their files have
   moved. `return len(claimed)` fixes it.
2. **Log the rule that matched (S).** The move reason lists the rule's tags, not the tags the
   post matched, which makes a correct move read as a wrong one.
3. **Report only (M).** A dry-run command that lists every file sitting in a rule folder its
   current tags no longer select, and where it would go. No moves.
4. **Review the report**, then decide per rule which ones may re-file.
5. **Opt-in moves (M)**, through `move_files_to_destination` so both databases update. They must
   respect protected folders, ignored folders, the favorite-artist hold-back and the SFW/NSFW
   split.

Also in this area: "Apply Post Dates Now" in the tag-rule editor runs one rule without the
priority exclusions, so it can pull files out of higher-priority folders.

*Done when:* the report runs clean for the rules that were opted in.

---

## Phase 3. Guarantee paths at the end of a run (M)

**Status: open.**

**Problem.** The only code that repairs `posts.file_path` runs inside the e621 sync, and the
second sort workflow moves files after it. Phase 0 closed this for tag moves only; the main
sort, artist sort and archive step still leave the path stale until the next day's sync. The
archive step is the largest historical source.

1. **Final reconcile stage in `fff daily` (S).** After the second sort, re-check every recorded
   path (about 1.4 seconds for the library) in a repair-only mode that never clears a path. The
   standalone `fff e621 verify file-paths` script must not be used as is: it clears unresolved
   paths and writes forward-slash paths.
2. **Move the write-through into `move_file_with_logging` (M)**, so every sorter move updates
   `posts.file_path` at move time, and remove the tag sorter's own copy from phase 0. A move into
   the duplicates folder must not re-point the post there.
3. **Report failures (S).** The sync and the sort workflow exit 0 whatever happened, so
   `fff daily` prints OK when the path repair or tag refresh failed. Return real exit codes and
   add an end-of-run check that counts posts pointing at a missing file.

*Done when:* a full daily run ends with 0 posts pointing at a missing file, and a failed step
shows as failed in the daily summary.

---

## Phase 5. Remaining small fixes (S)

**Status: open.**

- The `conditional_dnp_` prefix strip renames files with no update to either database or the
  move log.
- One shared path format (the archive stores backslashes, the sorter forward slashes) and a
  `posts.file_path` column wider than 255 characters.
- Manual `fff sort tag-sort` and `pool-sort` read tags from localhost, not the configured host.
- File names with a double extension (`.mp4.webm`) or a `_1` collision suffix hide the post id
  from every file-name parser.

---

## Phase 6. Duplicate manager (L)

**Status: open.**

- None of its moves update `posts` or write to `file_operations`.
- Merges delete the kept file's sorter row and then update it, so nothing is updated.
- Swap fails on the unique path key when both rows are in the same table, after the files have
  already been swapped.
- Replace, swap and merge never write the perceptual hash columns.

Fix: one helper that records every move in both databases and the move log, and exchange row
contents inside one transaction for swaps.

---

## Verified, not yet in a phase

- The archive step deletes a source file on a perceptual-hash match without checking that the
  archived copy still exists.
- A tracked file moved to the duplicates folder keeps its sorter row, re-pointed at the
  duplicates folder, and the artist scan can then move it back into the library.
- Sorter rows are found by file name alone in several places, and the maintenance fixer can
  delete both rows of a same-named pair.
- `fileops` and the sorter's `DatabaseManager` resolve a folder's table differently; the
  `fileops` version does not know the archived-favorites, deleted and manual-sort folders.
- The archive's list of sorter tables is hard-coded and misses two tables that exist.
- Seven file paths are recorded by two posts each.
- The fluffle `images.filepath` column is written once and never updated after a move.
