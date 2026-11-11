import pytest

from fff.cli.app import build_parser, main
from fff.common import first_run

pytestmark = pytest.mark.usefixtures("fresh_install")  # these tests are about the setup itself


@pytest.fixture
def opened(monkeypatch):
    """Which window 'fff setup' opened, and with which config path."""
    calls = []
    from fff.gui import first_run_ui, setup_ui
    monkeypatch.setattr(first_run_ui, "main", lambda config_path: calls.append(("wizard", config_path)) or 0)
    monkeypatch.setattr(setup_ui, "main", lambda config_path: calls.append(("settings", config_path)) or 0)
    return calls


def test_setup_opens_the_wizard_before_setup_is_finished(opened, tmp_path):
    custom_config = tmp_path / "custom.yaml"  # doesn't exist: a fresh install

    assert main(["--config", str(custom_config), "setup"]) == 0
    assert opened == [("wizard", str(custom_config))]


def test_setup_opens_the_settings_editor_once_finished(opened, tmp_path):
    first_run.finish({})
    assert main(["--config", str(tmp_path / "custom.yaml"), "setup"]) == 0
    assert main(["--config", str(tmp_path / "custom.yaml"), "setup", "--wizard"]) == 0
    assert [window for window, _path in opened] == ["settings", "wizard"]


def test_setup_command_is_registered_with_help():
    args = build_parser().parse_args(["setup"])
    assert args.command == "setup"
    assert not args.needs_config
    assert not build_parser().parse_args(["gui", "manager"]).needs_config  # the Manager runs the wizard itself


def test_commands_refuse_to_run_until_setup_is_finished(capsys, tmp_path):
    first_run.save_state({"completed": False})
    assert main(["doctor"]) == 2
    assert first_run.NOT_SET_UP in capsys.readouterr().err
