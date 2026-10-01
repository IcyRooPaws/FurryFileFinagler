"""Where FFF's files live in fff, and test-environment aware loading of its YAML files.

The original read configs/config.yaml, configs/tag_sorting.yaml and configs/artists.yaml
relative to the current working directory, and wrote Logs/, post_id_list.txt and
move_log.log there too.
"""
from pathlib import Path

import yaml

from fff.common import config as fff_config
from fff.common import paths

# Artist sorting settings are stored in config/sort_artists.yaml.
TAG_SORTING = paths.CONFIG_DIR / "tag_sorting.yaml"
SORT_ARTISTS = paths.CONFIG_DIR / "sort_artists.yaml"

# was ./Logs
LOG_DIR = paths.LOG_DIR / "sorter"

# data/: resolved on each access so the test environment gets data/test/...
_DATA_PATHS = {
    "DATA_DIR": paths.DATA_DIR / "sorter",
    "REPORTS_DIR": paths.DATA_DIR / "sorter" / "reports",
    "POST_ID_LIST": paths.DATA_DIR / "sorter" / "post_id_list.txt",   # was ./post_id_list.txt
    "MOVE_LOG": paths.DATA_DIR / "sorter" / "move_log.log",           # was ./move_log.log
}


def __getattr__(name):
    if name in _DATA_PATHS:
        return paths.for_env(_DATA_PATHS[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_yaml_for_env(path):
    """Load a YAML file for *sorting* code: in the test environment its absolute folders
    (e.g. a rule's target_directory P:/IPL/...) are rerouted under the test root.

    Code that writes the file back (the config GUIs, sort_artists_yaml) must read the raw
    file instead, or a test-mode save would write test paths into the real config.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    try:
        cfg = fff_config.load()
    except fff_config.ConfigError:
        return data
    return fff_config.reroute_paths(data, cfg) if fff_config.is_test(cfg) else data


def db_names() -> dict:
    """module -> schema for the active environment (prod or test_*)."""
    return fff_config.database_names(fff_config.load())


def ensure(path: Path) -> Path:
    Path(path).mkdir(parents=True, exist_ok=True)
    return Path(path)
