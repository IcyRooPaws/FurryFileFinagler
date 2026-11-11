# FurryFileFinagler (`fff`)

FFF keeps your e621 favorites archived on your own disk, finds sources for images that don't
have one, and keeps your media library sorted. It runs nightly on its own, and there's a
desktop Manager and a local web gallery for everything else.

Version 1.31.0 · [Changelog](CHANGELOG.md) · [Command reference](docs/COMMANDS.md) · [Usage guide](docs/USAGE.md)

## Highlights

- **Your e621 favorites, kept locally.** Every favorite is downloaded and stored in MySQL with
  its tags, pools, sources, relationships and deletion details. The database and the files
  stay in step when posts are deleted, unfavorited or re-credited on e621.
- **A local archive gallery.** Browse your archive in an e621-style page: search with
  suggestions, filters, and trending artists, copyrights, characters, species and fetish tags.
  **Trends over time** shows month by month what you were into.
- **Sources for loose images.** Reverse-image search (Fluffle and FuzzySearch) finds where a
  picture came from, downloads the original from e621 or FurAffinity, and favorites it.
- **A library that sorts itself.** Files are routed by rating, tags, pools and favorite artists,
  and old files are archived. Duplicates are caught against the whole library before they land,
  including the same video at a different quality.
- **One nightly run.** `fff daily` backs up the databases, searches for sources, sorts, syncs
  e621, cleans up AI posts and syncs FurAffinity. Each stage is logged, and it never runs twice
  at once.
- **Everything in one window.** The FFF Manager runs every workflow and opens every tool.
- **Set up in minutes.** A guided first-time setup checks each step as you go: the database is
  created, your e621 login is tested, and every folder is laid out and created.
- **Safe by default.**
  - A test environment with separate databases and folders.
  - Dry runs.
  - Holding folders and the recycle bin instead of deleting.
  - Nightly backups with restore.
  - FFF asks before it ever reads browser cookies.

## Features

### Archive gallery

`fff web` (or **Open archive gallery** in the Manager) serves a local gallery at
<http://127.0.0.1:5000>.

- **Search with several tags**, e621-style, with suggestions as you type:

  | You type | Finds |
  |---|---|
  | `wolf solo` | posts with all the tags |
  | `wolf -comic` | posts without a tag |
  | `~fox ~wolf` | posts with either |
  | `wolf*` | names starting with "wolf" |

  - A tag or artist name matches exactly that name. Other words match parts of tag, artist and
    file names, and a number finds a post ID.
  - Shift-click a trending name to add it to the search.
- **Filter** by rating, media type (images, GIFs, videos), active or deleted, and sort by
  created or deleted date.
- **Fast thumbnails.** The grid shows small previews, made once and cached in
  `data/cache/thumbs`; videos show a still frame. A page of 48 posts is under 1 MB instead of
  150–200 MB of original files.
- **Post pages** look like e621's: tag sections colored by category (artists, copyrights,
  characters, species, general, meta, lore), file details, sources, and a deleted notice.
- **Open file** and **Show in folder** buttons open the file on your computer. They're offered
  only to a browser on the same machine.
- **Trending sidebar.** Top-10 artists, copyrights, characters, species and fetish tags for the
  last 7, 30 or 90 days. Rank them by **Most posts**, or by **Rising** (names showing up far
  more than usual).
- **Trends over time.** A month-by-month heatmap of up to a year: what share of each month's
  archived posts carried each name, with your initial import as an "Earlier" column for
  comparison. Hover for numbers, or switch on the table view.

### e621 archive

- Sync favorites and post metadata to the local `e621_posts_db` database, and download the files.
- Match downloaded files to posts. Backfill or repair file paths, names, relationships, pools
  and deletion details.
- When a post leaves your favorites, move every local copy of its files to a holding folder.
  `fff e621 files orphaned` finds copies left behind earlier.
- Clean up posts e621 deleted as AI automatically. Review AI-tagged posts and AI artists, with
  dry runs.
- Manage favorites by post ID or MD5, including duplicate replacement and missing favorites.
- Download and load e621 database exports, inspect takedowns, and generate archive reports.

### Tag tools

- **Generic tags** (`fff gui generic-tags`, also in the Manager). Go through every tag,
  most-used first, and mark it **generic** (on so many posts it says little, like `mammal`),
  **not generic**, or **fetish**. Keyboard-driven: G, N, F, U to clear, Ctrl+Z to undo.
  - Generic tags drop out of the trending lists and trends.
  - The tool gives a ready-to-copy MySQL query that leaves them out.
- **Default fetish list.** Tags listed in `data/kink_tags.txt` count as fetish until you mark
  them otherwise.
- **Blacklist review** can remove a tag from the blacklist, make it critical, or ignore it.

### Reverse-image source search

- Search your inbox folders with Fluffle and FuzzySearch.
- Download matches from e621 and FurAffinity, applying your blacklist and favorite rules.
- Favorite FurAffinity submissions for images with exact matches on both sites.
- Find FurAffinity links in e621 sources and favorite them. When a post leaves your e621
  favorites, its FA source is unfavorited too.
- Rename FurAffinity files from database or manual author mappings.
- Every move, copy and rename is logged to the shared `file_operations` table.

FurAffinity features sign in with the FurAffinity cookies from your Firefox-based browser
(Waterfox, Firefox, LibreWolf, Floorp or Zen). FFF asks once before reading them; agree in setup,
in the Manager, or with `fff consent browser-cookies`.

### Media library

- Sort images, videos and GIFs by rating, tag, pool, favorite artist and folder rules. Safe-rated
  files go to the SFW folders.
- Hash and index every file. Incoming files are checked against the whole library (MD5 first,
  then perceptual hashes), and duplicates go to a duplicates folder.
- The **Duplicates** tool reviews duplicate groups. **Video visual match** finds the same video
  re-encoded, letterboxed or trimmed.
- Archive older files, restore recent ones, clean empty folders, and run maintenance checks.
- Run single operations, or the full workflow with `fff sort workflow`.

### Desktop Manager

`fff gui manager` (or `manager.bat` on Windows) is one window for everything.

- **Workflow pages** run commands and show their output in an Activity log.
- **Tools** open inside the window, stay open as you switch pages, and can be popped out:
  Settings, Tag rules, Artist rules, Duplicates, Blacklist review, Artist review and Generic tags.
- **Environment selector** restarts the Manager in `prod` or `test`.
- On a fresh install, the Manager shows the guided setup first.

### Nightly run

`fff daily` runs these stages in order, continuing past a failed stage and writing a summary to
`logs/daily/`:

1. Back up the databases.
2. Reverse-image source search.
3. Sort the library.
4. Sync e621 favorites.
5. Process AI posts.
6. Sort again, for the new downloads.
7. Sync FurAffinity sources.

Use `--dry-run` to preview the stages, `--skip-…` to leave stages out, and `--stop-on-error`.
`fff_daily.ps1` runs it from Windows Task Scheduler. A scheduled run can't ask questions, so
agree to the FurAffinity cookie use first.

### Databases and safety

- `fff db init` creates the three databases and every table, and is safe to re-run.
  `fff doctor` checks packages, folders, databases, tables and backup age.
- `fff db backup` takes a consistent backup of all three databases; `fff db restore` restores
  into the test databases unless you ask for production.
- **Test environment.** `fff --env test <command>`, or the Manager's selector, points every
  database and folder at a test copy. e621, FurAffinity and FuzzySearch are still live
  services, so network actions stay real.
- Every web request goes through one client with rate limits and capped retries. Only one copy
  of the nightly run or source search runs at a time.

## Quick start

On Windows, run **`setup.bat`**. It creates `.venv`, installs everything, and opens the
**guided first-time setup**. FFF can't be used until that's finished.

The setup walks through these steps, and won't move on until each one passes:

1. Choose the features you'll use.
2. Connect to MySQL and create every table.
3. Sign in to e621.
4. Lay out and create your folders.
5. Optional services: FuzzySearch and FurAffinity.
6. Final checks.

You can stop at any point and pick up where you left off. Then start FFF with `manager.bat`.

On other platforms:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup                   # guided first-time setup (required); afterwards, the settings editor
fff doctor                  # check dependencies, folders and databases
fff daily --dry-run         # preview the nightly stages
fff web                     # open the archive gallery at http://127.0.0.1:5000
```

After setup, `fff daily` runs the full pipeline. `fff setup --wizard` runs the guided setup
again.

## Documentation

| Doc | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Setup, where things live, daily order, what each command touches, the test environment, checks |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Complete command reference (generated) |
| [CHANGELOG.md](CHANGELOG.md) | Every version, newest first |

## Config files

The real files are gitignored; only the examples are committed. The guided setup starts from the
examples and keeps credentials separately in `config/secrets.yaml`.

| Real file | Example |
|---|---|
| `config/config.yaml` | `config/config.example.yaml` (`fff config make-example --force` refreshes it) |
| `config/secrets.yaml` | `config/secrets.example.yaml` |
| `config/tag_sorting.yaml`, `sort_artists.yaml`, `review_artists.yaml`, `fa_author_mappings.yaml` | `config/*.example.yaml` |
| `config/lists/blacklist.txt`, `critical_blacklist.txt`, `blacklist_ignore.txt`, `ai_tags.txt` | `config/lists/*.example.txt` |
| `data/kink_tags.txt` (optional) | Default fetish tags, one per line |

## Tests

```powershell
.venv\Scripts\python -m pytest                     # unit and CLI tests
$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration   # test databases only
python tools/gui_smoke.py manager                   # smoke-test the manager: every page, plus an embedded tool
.venv\Scripts\python -m ruff check fff tests tools  # lint (rules in pyproject.toml)
```
