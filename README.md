# FurryFileFinagler (`fff`)

`fff` is a desktop and command-line toolkit for maintaining an e621 archive and a local media library. It combines source search, downloads, database maintenance, file sorting, review tools, and a local archive gallery behind one configuration and command interface.

## Features

### e621 archive

- Sync favorites and post metadata to the local `e621_posts_db` database.
- After syncing e621 favorites, check each post's FurAffinity source links and favorite linked FA posts that aren't already favorited. This requires an active FurAffinity login in Firefox.
- Match downloaded files to posts; backfill or repair file paths, names, relationships, pool data, and deletion details.
- Find and restore files for deleted or unfavorited posts, and compare local records with live favorites.
- Manage favorites by post ID or MD5, including duplicate replacement and missing-favorite workflows.
- Review AI-tagged or AI-artist posts, with dry-run support for cleanup actions, to destory them.
- Download and load e621 database exports, inspect takedowns, and generate archive reports.

### Reverse-image source search

- Search configured inbox folders with reverse-image search and FuzzySearch.
- Process e621 and FurAffinity matches, download available sources, and apply configured blacklist and favorite rules.
- Favorite FurAffinity submissions for local images with exact matches on both e621 and FurAffinity; existing FA favorites are left unchanged.
- Find FurAffinity submission links in e621 source data, track them, and favorite submissions that are not already favorited.
- Rename FurAffinity files using database or manually maintained author mappings.
- Track file moves, copies, and renames in the shared `file_operations` log.

### Media library management

- Sort and route images, videos, and GIFs using rating, tag, pool, artist, and directory rules.
- Hash and index files, inspect hash data, and review duplicates.
- Archive older files, restore recent archive entries, clean empty folders, and run maintenance checks.
- Run individual operations or the full workflow with `fff sort workflow`.

### Desktop tools and gallery

- Open the FFF Manager with `fff gui manager` (or run `manager.bat` on Windows). It is one window for everything: workflow pages run commands and show their output in an Activity log, and the tools below open inside the window, stay open as you switch pages, and can be popped out into a separate window.
- Tools: Settings (config and credentials), Tag rules and Artist rules editors, Duplicates, Blacklist review and Artist review. Each tool still runs on its own too (`fff setup`, `fff gui tag-sort-config`, ...), with the same look.
- The manager's environment selector restarts it in `prod` or `test`; workflows and tools then use that environment.
- Browse the archive through a local Flask web gallery (e621 Archive page → Open archive gallery).

### Daily workflow

`fff daily` runs source search, sorting, e621 synchronization (including the linked FurAffinity favorite check), sorting again, and external FurAffinity source synchronization. The FurAffinity steps require an active Firefox login. Use `--dry-run` to preview the stages, or skip selected stages and stop on errors.

The [command reference](docs/COMMANDS.md) lists all available commands. The [usage guide](docs/USAGE.md) covers setup, file effects, the test environment, and checks.

## Quick start

On Windows, run `setup.bat` to create `.venv`, install dependencies, install FFF, and open guided setup. On other platforms, use:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup                   # configure settings and credentials
fff doctor                  # check dependencies, folders, and databases
fff daily --dry-run         # preview the daily stages
```

After setup, run `fff daily` to start the full pipeline. Use `fff --env test <command>` for commands that should use `test_*` databases and the configured test-folder root instead of production paths.

Test mode protects database and local-file operations by redirecting them to test locations. e621, FurAffinity, and FuzzySearch are still live services, so network actions remain real.

## Documentation

| Doc | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Setup, where things live, daily order, what each command touches, the test environment, checks |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Complete command reference (generated) |

## Config files

The real files are gitignored; only the examples are committed.

| Real file | Example |
|---|---|
| `config/config.yaml` | `config/config.example.yaml` (`fff config make-example --force` refreshes it) |
| `config/secrets.yaml` | `config/secrets.example.yaml` |
| `config/tag_sorting.yaml`, `sort_artists.yaml`, `review_artists.yaml`, `fa_author_mappings.yaml` | `config/*.example.yaml` |
| `config/lists/blacklist.txt`, `critical_blacklist.txt`, `ai_tags.txt` | `config/lists/*.example.txt` |

The setup UI starts from the committed examples. It stores credentials separately in the gitignored `config/secrets.yaml`; only example files are committed.

## Tests

```powershell
.venv\Scripts\python -m pytest                     # unit and CLI tests
$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration   # test databases only
python tools/gui_smoke.py manager                   # smoke-test the manager: every page, plus an embedded tool
```
