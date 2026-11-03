"""fff.common.single_instance: a second copy of a command refuses to start."""
import pytest

from fff.common import paths
from fff.common.single_instance import AlreadyRunning, exclusive_command, single_instance


@pytest.fixture(autouse=True)
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LOCK_DIR", tmp_path / "locks")


def test_second_holder_is_refused_until_the_first_releases():
    with single_instance("fluffle_search"):
        with pytest.raises(AlreadyRunning, match="fff fluffle search"):
            with single_instance("fluffle_search"):
                pass
        with single_instance("daily"):  # other names don't conflict
            pass
    with single_instance("fluffle_search"):  # released
        pass


def test_exclusive_command_returns_1_without_running(capsys):
    calls = []

    @exclusive_command("fluffle_search")
    def main():
        calls.append(1)
        return 0

    with single_instance("fluffle_search"):
        assert main() == 1
    assert calls == []
    assert "[ERROR] Not starting: another 'fff fluffle search' is already running" in capsys.readouterr().out
    assert main() == 0 and calls == [1]
