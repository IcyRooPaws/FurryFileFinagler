"""Every config file has a committed example, and examples never carry real values."""
import re

import pytest
import yaml

from fff.common import config_examples, paths

SECRET_KEYS = {"password", "api_key", "login"}


def _example_for(path):
    return path.with_name(path.name.replace(path.suffix, f".example{path.suffix}"))


def _real_config_files():
    files = [p for p in paths.CONFIG_DIR.glob("*.yaml") if ".example." not in p.name]
    files += [p for p in paths.LISTS_DIR.glob("*.txt") if ".example." not in p.name]
    return files


EXPECTED = ["config.yaml", "secrets.yaml", "tag_sorting.yaml", "sort_artists.yaml", "review_artists.yaml",
            "lists/blacklist.txt", "lists/critical_blacklist.txt", "lists/blacklist_ignore.txt",
            "lists/ai_tags.txt"]


@pytest.mark.parametrize("name", EXPECTED)
def test_known_config_files_have_examples(name):
    assert _example_for(paths.CONFIG_DIR / name).exists(), name


def test_every_present_config_file_has_example():
    missing = [p.name for p in _real_config_files() if not _example_for(p).exists()]
    assert not missing, f"add an .example version for: {missing}"


def _walk(obj, key=None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, key)
    else:
        yield key, obj


@pytest.mark.parametrize("path", sorted(paths.CONFIG_DIR.glob("*.example.yaml")), ids=lambda p: p.name)
def test_examples_hold_no_secrets(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key, value in _walk(data):
        if key in SECRET_KEYS and value:
            assert value == config_examples.SECRET_PLACEHOLDER, f"{path.name}: {key} has a real-looking value"


def test_config_example_matches_config_structure():
    real, example = paths.CONFIG_DIR / "config.yaml", paths.CONFIG_DIR / "config.example.yaml"
    if not real.exists():
        pytest.skip("no local config.yaml")

    def keys(obj, prefix=""):
        out = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(prefix + str(k))
                out |= keys(v, f"{prefix}{k}.")
        return out

    real_keys = keys(yaml.safe_load(real.read_text(encoding="utf-8")))
    example_keys = keys(yaml.safe_load(example.read_text(encoding="utf-8")))
    assert real_keys == example_keys, "run 'fff config make-example --force' after changing config.yaml's layout"


def test_make_example_sanitizes():
    cfg = {"database": {"host": "192.168.1.50", "user": "ipl"},
           "e621": {"user_agent": "Mine/1.0 (by me)", "blacklist": ["private"]},
           "a": {"dir": "P:/IPL/Pictures/x", "multi": "P:\\IPL\\in\n", "home": "C:/Users/IPL/Downloads/f",
                 "other": "Z:/Secret/stuff", "rel": "logs/sorter", "n": 5}}
    ex = config_examples.make_example(cfg)
    text = yaml.safe_dump(ex)
    assert not re.search(r"(?i)ipl|192.168.1|secret|private|by me", text)
    assert ex["a"]["dir"] == "D:/Media/Pictures/x" and ex["a"]["home"] == "C:/Users/you/Downloads/f"
    assert ex["a"]["rel"] == "logs/sorter" and ex["a"]["n"] == 5
