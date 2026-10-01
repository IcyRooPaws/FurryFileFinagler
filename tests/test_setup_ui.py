from pathlib import Path

import yaml

from fff.gui import setup_ui


def test_load_documents_uses_examples_without_overwriting_them(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.example.yaml").write_text("database:\n  host: localhost\n", encoding="utf-8")
    (config_dir / "secrets.example.yaml").write_text("database:\n  password: CHANGE_ME\n", encoding="utf-8")
    monkeypatch.setattr(setup_ui.paths, "CONFIG_DIR", config_dir)

    config_path, secrets_path, config, secrets = setup_ui.load_documents()

    assert config == {"database": {"host": "localhost"}}
    assert secrets == {"database": {"password": "CHANGE_ME"}}
    assert config_path == config_dir / "config.yaml"
    assert secrets_path == config_dir / "secrets.yaml"
    assert not config_path.exists()
    assert not secrets_path.exists()


def test_load_documents_adds_missing_defaults_and_keeps_user_values(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.example.yaml").write_text(
        "database:\n  host: localhost\n  port: 3306\nfluffle:\n  enabled: true\n", encoding="utf-8")
    (config_dir / "secrets.example.yaml").write_text("database:\n  password: CHANGE_ME\n", encoding="utf-8")
    (config_dir / "config.yaml").write_text("database:\n  host: db.internal\ncustom: value\n", encoding="utf-8")
    monkeypatch.setattr(setup_ui.paths, "CONFIG_DIR", config_dir)

    _, _, config, secrets = setup_ui.load_documents()

    assert config == {"database": {"host": "db.internal", "port": 3306},
                      "fluffle": {"enabled": True}, "custom": "value"}
    assert secrets == {"database": {"password": "CHANGE_ME"}}


def test_collect_documents_preserves_types_and_same_named_secret_sections():
    class Input:
        def __init__(self, value):
            self.value = value

        def get(self, *args):
            return self.value

    defaults = {"database": {"host": "localhost", "port": 3306, "enabled": True}}
    config_editors = {
        ("database", "host"): ("entry", Input("db.example"), "localhost"),
        ("database", "port"): ("entry", Input("3307"), 3306),
        ("database", "enabled"): ("bool", Input(False), True),
    }
    secrets_editors = {
        ("database", "password"): ("entry", Input("secret"), "CHANGE_ME"),
    }

    config = setup_ui._collect_documents(defaults, config_editors)
    secrets = setup_ui._collect_documents({"database": {"password": "CHANGE_ME"}}, secrets_editors)

    assert config == {"database": {"host": "db.example", "port": 3307, "enabled": False}}
    assert secrets == {"database": {"password": "secret"}}


def test_save_documents_writes_independent_yaml_files(tmp_path: Path):
    config_path = tmp_path / "config" / "config.yaml"
    secrets_path = config_path.with_name("secrets.yaml")

    setup_ui.save_documents(config_path, secrets_path,
                            {"database": {"host": "db"}},
                            {"database": {"password": "secret"}})

    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {"database": {"host": "db"}}
    assert yaml.safe_load(secrets_path.read_text(encoding="utf-8")) == {
        "database": {"password": "secret"}}