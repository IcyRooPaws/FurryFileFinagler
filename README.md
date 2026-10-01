# FurryFileFinagler (`fff`)

One toolkit combining **e621 archive** (e621 favorites archive), **reverse-image search**
(reverse image search and source downloads) and **media sorter** (library sorting, hashing,
archiving, duplicates). Every command implementation has an `fff` command. Each port has been
checked against its original on identical input: database contents, files and HTTP calls
all match.

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
fff setup      # builds config/config.yaml + secrets.yaml from the three current features
fff doctor              # checks packages, folders and all three databases

fff e621 sync           # favorites -> e621_posts_db
fff fluffle search      # find sources for inbox images
fff sort workflow       # sort, hash, archive the library
fff daily               # or: all three, in that order
```

Use `fff --env test <command>` (or `environment: test` in `config/config.yaml`) to run
against the `test_*` databases and the `E:\Testing` copy of the library.

## Documentation

| Doc | Contents |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Setup, where things live, daily order, what each command touches, the test environment, checks |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Every command, with the original script it replaces (generated) |
| [project notes](project notes) | The merge plan, decisions, known issues and progress |
| []() | How the current implementation was ported (for changing a ported module) |
| [](README.md) | The three current features' own docs |

## Config files

The real files are gitignored; only the examples are committed.

| Real file | Example |
|---|---|
| `config/config.yaml` | `config/config.example.yaml` (`fff config make-example --force` refreshes it) |
| `config/secrets.yaml` | `config/secrets.example.yaml` |
| `config/tag_sorting.yaml`, `sort_artists.yaml`, `review_artists.yaml`, `fa_author_mappings.yaml` | `config/*.example.yaml` |
| `config/lists/blacklist.txt`, `critical_blacklist.txt`, `ai_tags.txt` | `config/lists/*.example.txt` |

## Tests

```powershell
.venv\Scripts\python -m pytest                     # unit, pinning and CLI tests
$env:FFF_INTEGRATION=1; .venv\Scripts\python -m pytest -m integration   # test databases only
python tools/gui_smoke.py                          # every GUI opens and loads data
```
