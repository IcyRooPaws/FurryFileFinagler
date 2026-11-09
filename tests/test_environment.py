"""The prod/test database toggle."""
import re

import pytest

from fff.common import config as fff_config
from fff.common import paths

PROD = {"fluffle": "image_search", "archive": "e621_posts_db", "sorter": "image_sorting_hashes"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("FFF_ENV", raising=False)
    fff_config.set_environment(None)
    yield
    fff_config.set_environment(None)


def cfg(env=None, **extra):
    base = {
        "database": {"host": "h", "user": "u", "password": "p"},
        "databases": dict(PROD),
        # Stored names that must be ignored in favour of the environment:
        "archive": {"mysql": {"table": "posts", "image_hash_db": {"database": "image_sorting_hashes", "tables": ["a"]}}},
        "sorter": {"mysql": {"database": "image_sorting_hashes", "image_hash_db": {"database": "image_sorting_hashes"}}},
    }
    if env:
        base["environment"] = env
    base.update(extra)
    return base


def _view_names(c):
    fl, ar, so = (fff_config.legacy_view(m, c) for m in ("fluffle", "archive", "sorter"))
    return {
        "fluffle": fl["database"]["database"],
        "archive": ar["mysql"]["database"],
        "archive.image_hash_db": ar["mysql"]["image_hash_db"]["database"],
        "sorter": so["mysql"]["database"],
        "sorter.image_hash_db": so["mysql"]["image_hash_db"]["database"],
    }


def test_prod_is_default():
    assert fff_config.environment(cfg()) == "prod"
    assert fff_config.database_names(cfg()) == PROD
    assert _view_names(cfg()) == {"fluffle": "image_search", "archive": "e621_posts_db",
                                  "archive.image_hash_db": "image_sorting_hashes",
                                  "sorter": "image_sorting_hashes", "sorter.image_hash_db": "image_sorting_hashes"}


def test_test_environment_prefixes_every_database_everywhere():
    c = cfg("test")
    assert fff_config.database_names(c) == {k: "test_" + v for k, v in PROD.items()}
    assert all(name.startswith("test_") for name in _view_names(c).values())
    assert _view_names(c)["archive.image_hash_db"] == "test_image_sorting_hashes"


def test_precedence_flag_over_env_var_over_config(monkeypatch):
    c = cfg("prod")
    monkeypatch.setenv("FFF_ENV", "test")
    assert fff_config.environment(c) == "test"
    fff_config.set_environment("prod")
    assert fff_config.environment(c) == "prod"


def test_invalid_environment_rejected(monkeypatch):
    with pytest.raises(fff_config.ConfigError):
        fff_config.environment(cfg("staging"))
    with pytest.raises(fff_config.ConfigError):
        fff_config.set_environment("dev")


def test_no_double_prefix():
    c = cfg("test", databases={**PROD, "archive": "test_e621_posts_db"})
    assert fff_config.database_name(c, "archive") == "test_e621_posts_db"


def test_redirect_database_safety_net():
    assert fff_config.redirect_database(cfg(), "e621_posts_db") == "e621_posts_db"
    t = cfg("test")
    assert fff_config.redirect_database(t, "e621_posts_db") == "test_e621_posts_db"
    assert fff_config.redirect_database(t, "image_sorting_hashes") == "test_image_sorting_hashes"
    assert fff_config.redirect_database(t, "something_else") == "something_else"


def test_connect_database_redirects_in_test(monkeypatch):
    from fff.common import db

    seen = {}
    monkeypatch.setattr(db.mysql.connector, "connect", lambda **kw: seen.update(kw) or "conn")
    db.connect_database("e621_posts_db", cfg("test"), pooled=False)
    assert seen["database"] == "test_e621_posts_db"
    db.connect("sorter", cfg("test"), pooled=False)
    assert seen["database"] == "test_image_sorting_hashes"


def test_cli_env_flag(monkeypatch, capsys):
    from fff.cli import app

    monkeypatch.setattr(fff_config, "load", lambda *a, **k: cfg("prod"))
    monkeypatch.setattr(app, "_cmd_doctor", lambda args: print(fff_config.environment(cfg("prod"))) or 0)
    app.build_parser  # noqa: B018
    assert app.main(["--env", "test", "doctor"]) == 0
    out = capsys.readouterr()
    assert "TEST environment" in out.err and "test_e621_posts_db" in out.err
    assert out.out.strip() == "test"


DB_NAME = re.compile(r"""["'](?:test_)?(?:e621_posts_db|image_sorting_hashes|image_search)["'.]""")
ALLOWED = {"common/config.py", "common/config_examples.py"}


def test_no_hard_coded_database_names_in_package():
    """Modules must use configured schema names so the test toggle applies consistently."""
    offenders = []
    root = paths.ROOT / "fff"
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DB_NAME.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, "use fff_config.database_names(cfg):\n" + "\n".join(offenders)


# --- test paths --------------------------------------------------------------------

@pytest.mark.parametrize("prod,test", [
    ("P:/IPL/Pictures/- SFW Pics", "E:/Testing/P/IPL/Pictures/- SFW Pics"),
    (r"P:\IPL\- Need to be Sorted", r"E:\Testing\P\IPL\- Need to be Sorted"),
    ("E:/Work/AI", "E:/Testing/E/Work/AI"),
    ("c:/Users/IPL/Downloads", "E:/Testing/C/Users/IPL/Downloads"),
    ("P:/", "E:/Testing/P"),
    ("E:/Testing/P/IPL/already", "E:/Testing/P/IPL/already"),      # already under the test root
    ("logs/sorter", "logs/sorter"),                                  # relative
    ("https://e621.net/posts", "https://e621.net/posts"),            # not a path
    ("YourApp/1.0 (by you)", "YourApp/1.0 (by you)"),
])
def test_test_path_rerouting(prod, test):
    assert fff_config.test_path(prod, cfg("test")) == test
    assert fff_config.test_path(prod, cfg("prod")) == prod


def test_project_data_goes_to_data_test():
    assert fff_config.test_path(str(paths.DATA_DIR / "ai_artist.md"), cfg("test")) == str(paths.TEST_DATA_DIR / "ai_artist.md")
    assert fff_config.test_path(str(paths.E621_PAGE_CACHE), cfg("test")) == str(paths.TEST_DATA_DIR / "cache" / "e621_pages")
    already = str(paths.TEST_DATA_DIR / "x.md")
    assert fff_config.test_path(already, cfg("test")) == already
    assert fff_config.test_path(str(paths.DATA_DIR / "ai_artist.md"), cfg("prod")) == str(paths.DATA_DIR / "ai_artist.md")


def test_other_project_folders_are_not_rerouted():
    for inside in (str(paths.LISTS_DIR / "ai_tags.txt"), str(paths.LOG_DIR / "archive")):
        assert fff_config.test_path(inside, cfg("test")) == inside


def test_archive_data_paths_follow_environment(monkeypatch):
    from fff.archive import paths as archive_paths

    monkeypatch.setattr(fff_config, "load", lambda *a, **k: cfg("prod"))
    assert archive_paths.AI_ARTIST_MD == paths.DATA_DIR / "ai_artist.md"
    monkeypatch.setattr(fff_config, "load", lambda *a, **k: cfg("test"))
    assert archive_paths.AI_ARTIST_MD == paths.TEST_DATA_DIR / "ai_artist.md"
    assert archive_paths.PAGE_CACHE == paths.TEST_DATA_DIR / "cache" / "e621_pages"
    assert archive_paths.AI_TAGS == paths.LISTS_DIR / "ai_tags.txt"  # config/ stays put


def test_custom_root_and_opt_out():
    assert fff_config.test_path("P:/IPL/x", cfg("test", test_paths_root="D:/Sandbox")) == "D:/Sandbox/P/IPL/x"
    assert fff_config.test_path("P:/IPL/x", cfg("test", test_paths_root=None)) == "P:/IPL/x"


def test_reroute_tree_handles_lists_and_multiline():
    tree = {"a": ["P:/IPL/one", "rel"], "b": {"src": "P:\\IPL\\in\n  P:\\IPL\two\n"}, "n": 3, "flag": True}
    out = fff_config.reroute_paths(tree, cfg("test"))
    assert out == {"a": ["E:/Testing/P/IPL/one", "rel"],
                   "b": {"src": "E:\\Testing\\P\\IPL\\in\n  E:\\Testing\\P\\IPL\two\n"}, "n": 3, "flag": True}


def test_real_config_views_have_no_prod_paths_in_test():
    """With the real config in test mode, every absolute folder is under the test root or this project."""
    if not (paths.CONFIG_DIR / "config.yaml").exists():
        pytest.skip("no local config.yaml")
    real = fff_config.deep_merge(fff_config.load(reload=True), {"environment": "test"})
    root = fff_config.test_paths_root(real)
    leaks = []

    def walk(obj, where):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{where}.{k}")
        elif isinstance(obj, list):
            for v in obj:
                walk(v, where)
        elif isinstance(obj, str):
            for line in obj.splitlines() or [obj]:
                line = line.strip()
                if not re.match(r"^[A-Za-z]:[\/]", line):
                    continue
                in_project = fff_config._under(line, str(paths.ROOT))
                prod_data = fff_config._under(line, str(paths.DATA_DIR)) and not fff_config._under(line, str(paths.TEST_DATA_DIR))
                if prod_data or not (fff_config._under(line, root) or in_project):
                    leaks.append(f"{where}: {line}")

    for module in fff_config.MODULES:
        walk(fff_config.legacy_view(module, real), module)
    assert not leaks, "\n".join(leaks)
