import os

import pytest

INTEGRATION = os.environ.get("FFF_INTEGRATION") == "1"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: needs the real config and MySQL; runs only with FFF_INTEGRATION=1, "
        "and then always against the test_* databases")
    if INTEGRATION:
        # Integration tests must never see production databases or folders.
        os.environ["FFF_ENV"] = "test"


def pytest_collection_modifyitems(config, items):
    if INTEGRATION:
        return
    skip = pytest.mark.skip(reason="integration test: set FFF_INTEGRATION=1 to run (uses the test_* databases)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _setup_state_in_tmp(tmp_path, monkeypatch):
    """Tests run as a finished install, with their own setup_state.json.

    They never touch the real data/setup_state.json, where an unfinished one would lock the
    install out of every command. Finished is set explicitly rather than inferred from a
    config.yaml on disk: CI has none, and CLI tests hand main() a config in memory, so the
    setup gate would stop them there (and only there). Tests of the setup itself start from a
    fresh install with the ``fresh_install`` fixture.
    """
    from fff.common import first_run

    monkeypatch.setattr(first_run, "STATE_FILE", tmp_path / "setup_state.json")
    first_run.save_state({"completed": True})


@pytest.fixture
def fresh_install():
    """No setup progress yet: whether FFF counts as set up depends on config.yaml, as on a new install."""
    from fff.common import first_run

    first_run.STATE_FILE.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _integration_uses_test_environment(request):
    """Belt and braces: an integration test runs with the environment forced to test."""
    if "integration" not in request.keywords:
        yield
        return
    from fff.common import config as fff_config

    fff_config.set_environment("test")
    try:
        yield
    finally:
        fff_config.set_environment(None)
