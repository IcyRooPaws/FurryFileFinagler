from fff.cli.app import build_parser, main


def test_setup_command_does_not_require_existing_config(monkeypatch, tmp_path):
    called = {}

    def fake_setup(config_path):
        called["config_path"] = config_path
        return 0

    from fff.gui import setup_ui
    monkeypatch.setattr(setup_ui, "main", fake_setup)
    custom_config = tmp_path / "custom.yaml"

    assert main(["--config", str(custom_config), "setup"]) == 0
    assert called["config_path"] == str(custom_config)


def test_setup_command_is_registered_with_help():
    args = build_parser().parse_args(["setup"])
    assert args.command == "setup"
    assert not args.needs_config