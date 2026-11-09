"""Every database connection goes through fff.common.db (improvement plan, part 1)."""
import re

import pytest

from fff.common import config as fff_config
from fff.common import db, paths

PROD = {"fluffle": "image_search", "archive": "e621_posts_db", "sorter": "image_sorting_hashes"}

# Modules allowed to open MySQL connections or pools themselves.
ALLOWED = {"fff/common/db.py"}
DIRECT = re.compile(r"mysql\.connector\.connect\(|MySQLConnectionPool\(|\bpooling\.MySQL")


def cfg(env=None):
    base = {"database": {"host": "h", "port": 3307, "user": "u", "password": "p"}, "databases": dict(PROD)}
    if env:
        base["environment"] = env
    return base


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("FFF_ENV", raising=False)
    fff_config.set_environment(None)
    monkeypatch.setattr(db, "_pools", {})
    yield
    fff_config.set_environment(None)


@pytest.fixture
def connect_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(db.mysql.connector, "connect", lambda **kw: calls.append(kw) or "conn")
    return calls


@pytest.fixture
def pools(monkeypatch):
    created = []

    class FakePool:
        def __init__(self, **kw):
            self.kw = kw
            created.append(self)

        def get_connection(self):
            return ("pooled", self)

    monkeypatch.setattr(db.pooling, "MySQLConnectionPool", FakePool)
    return created


def test_no_direct_connections_outside_the_db_layer():
    offenders = []
    for path in sorted((paths.ROOT / "fff").rglob("*.py")):
        rel = path.relative_to(paths.ROOT).as_posix()
        if rel in ALLOWED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DIRECT.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "connect through fff.common.db instead:\n" + "\n".join(offenders)


def test_pools_are_shared_per_settings(pools):
    c = cfg()
    db.connect_database("e621_posts_db", c)
    db.connect_database("e621_posts_db", c)
    assert len(pools) == 1
    # Different options must not silently reuse a pool built with other options.
    db.connect_database("e621_posts_db", c, autocommit=True)
    db.connect_database("e621_posts_db", c, pool_size=25)
    assert len(pools) == 3
    assert pools[1].kw["autocommit"] is True
    assert pools[2].kw["pool_size"] == 25


def test_pool_redirects_in_test_environment(pools):
    db.get_pool("image_sorting_hashes", cfg("test"))
    assert pools[0].kw["database"] == "test_image_sorting_hashes"


def test_connect_with_uses_settings_overrides_and_config_fallbacks(connect_calls):
    db.connect_with({"host": "other", "user": "", "database": "image_search"}, cfg=cfg())
    kw = connect_calls[-1]
    assert kw["host"] == "other"          # set in the settings dict
    assert kw["user"] == "u"              # empty -> config.yaml
    assert kw["port"] == 3307             # missing -> config.yaml (the fluffle helpers used to drop it)
    assert kw["database"] == "image_search"


def test_connect_with_database_argument_and_redirect(connect_calls):
    db.connect_with({"database": "image_search"}, database="e621_posts_db", cfg=cfg("test"), charset="utf8mb4")
    kw = connect_calls[-1]
    assert kw["database"] == "test_e621_posts_db"
    assert kw["charset"] == "utf8mb4"


def test_connect_with_needs_a_database_name(connect_calls):
    with pytest.raises(ValueError):
        db.connect_with({"host": "h"}, cfg=cfg())
    assert not connect_calls


def test_server_overrides_casts_port():
    assert db.server_overrides({"port": "3308", "host": None, "database": "x"}) == {"port": 3308}


def test_try_connect_with_reports_and_returns_none(monkeypatch, capsys):
    def boom(**kw):
        raise db.mysql.connector.Error("refused")

    monkeypatch.setattr(db.mysql.connector, "connect", boom)
    assert db.try_connect_with({"database": "image_search"}, cfg=cfg()) is None
    assert "image_search" in capsys.readouterr().out


def test_connect_server_selects_no_schema(connect_calls):
    db.connect_server(cfg(), settings={"host": "other"}, autocommit=True)
    kw = connect_calls[-1]
    assert "database" not in kw
    assert kw["host"] == "other" and kw["autocommit"] is True
