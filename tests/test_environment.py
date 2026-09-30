"""The prod/test database toggle."""
import re
from pathlib import Path

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
    """Ported code must take schema names from fff.common.config.database_names(), or the
    test toggle would silently miss it (the existing code hard-code them ~200 times)."""
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
