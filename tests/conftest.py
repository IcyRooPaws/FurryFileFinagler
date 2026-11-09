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
