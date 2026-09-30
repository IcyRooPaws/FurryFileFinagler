"""Compatibility shim for e621 archive's modules/config.py.

``load_config()`` returns the archive's legacy view of the unified config (same keys the
original config.yaml had, already path-normalized), so ported call sites stay unchanged.
"""
import yaml

from fff.common import config as fff_config
from fff.common import paths
from fff.common.config import normalize_config_paths  # noqa: F401  (re-exported)
from fff.common.fileops import normalize_file_path  # noqa: F401  (re-exported)


def find_project_root():
    return paths.ROOT


def find_config_file(config_filename="config.yaml"):
    return str(paths.CONFIG_DIR / config_filename)


def load_config(config_path=None):
    """The archive's config. An explicit path loads that YAML the way the original did."""
    if config_path is not None:
        with open(config_path, "r") as f:
            return normalize_config_paths(yaml.safe_load(f))
    return fff_config.legacy_view("archive")


__all__ = ["find_project_root", "find_config_file", "load_config", "normalize_config_paths",
           "normalize_file_path"]
