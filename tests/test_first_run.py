"""fff.common.first_run (progress, checks, folder helpers) and the setup wizard window."""
import shutil
import time
from pathlib import Path

import pytest
import yaml

from fff.common import config as fff_config
from fff.common import first_run, paths

EXAMPLE = paths.CONFIG_DIR / "config.example.yaml"
SECRETS_EXAMPLE = paths.CONFIG_DIR / "secrets.example.yaml"


def example():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def secrets_example():
    return yaml.safe_load(SECRETS_EXAMPLE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- progress

def test_installs_from_before_the_wizard_count_as_set_up(tmp_path):
    config = tmp_path / "config.yaml"
    assert not first_run.is_complete(config)  # fresh install: no config, no state
    config.write_text("environment: prod\n", encoding="utf-8")
    assert first_run.is_complete(config)      # existing config, no state file


def test_progress_is_saved_and_rerunning_never_unfinishes(tmp_path):
    fresh = first_run.begin(tmp_path / "missing.yaml")
    assert fresh["completed"] is False and not first_run.is_complete()
    first_run.record(fresh, "database", step="e621", features={"sorting": True})
    assert first_run.load_state()["done"] == ["database"]
    assert first_run.load_state()["step"] == "e621"
    first_run.finish(fresh)
    assert first_run.is_complete()
    assert first_run.begin(tmp_path / "missing.yaml")["completed"] is True  # re-running the wizard


def test_an_existing_config_stays_set_up_when_the_wizard_is_rerun(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("environment: prod\n", encoding="utf-8")
    assert first_run.begin(config)["completed"] is True
    assert first_run.is_complete(config)


# --------------------------------------------------------------------------- values

def test_unfinished_values_lists_example_placeholders():
    problems = first_run.unfinished_values(example(), secrets_example(), {"reverse_search": True})
    assert "Database user is not filled in" in problems
    assert "e621 API key is not filled in" in problems
    assert "e621 user agent is not filled in" in problems
    assert "FuzzySearch API key is not filled in" in problems
    assert "Database password is still CHANGE_ME" in problems


def test_unfinished_values_is_empty_once_filled_in():
    config, secrets = example(), secrets_example()
    config["database"]["user"] = "fff"
    config["e621"]["user_agent"] = first_run.default_user_agent("someone")
    config["archive"]["e621"]["favorites_username"] = "someone"
    secrets["database"]["password"] = "hunter2"
    secrets["e621"] = {"login": "someone", "api_key": "abc123"}
    assert first_run.unfinished_values(config, secrets, {"reverse_search": False}) == []
    assert first_run.unfinished_values(config, secrets, {"reverse_search": True}) == [
        "FuzzySearch API key is not filled in"]


# --------------------------------------------------------------------------- folders

def test_move_root_moves_every_folder_under_it_and_nothing_else():
    config = example()
    config["extra"] = {"near_miss": "D:/MediaX/stays", "lines": "D:/Media/a\nD:/Other/b\n"}
    moved = first_run.move_root(config, "D:/Media", "E:\\Library\\")
    assert moved["archive"]["download"]["images"]["safe"] == "E:/Library/Pictures/- SFW Pics"
    assert moved["sorter"]["other"]["fav_directory"] == "E:/Library/- Fav"
    assert moved["extra"] == {"near_miss": "D:/MediaX/stays", "lines": "E:/Library/a\nD:/Other/b\n"}
    assert moved["sorter"]["archive_directories"]["sfw"]["images"] == "D:/Archive/Pictures/SFW"
    assert config["archive"]["download"]["images"]["safe"] == "D:/Media/Pictures/- SFW Pics"  # a copy


def test_replace_folder_changes_every_setting_that_uses_it():
    config = example()
    moved = first_run.replace_folder(config, "d:\\media\\- need to be sorted", "F:/Inbox")
    assert moved["fluffle"]["e621"]["download_directory"] == "F:/Inbox"
    assert moved["archive"]["download"]["missing_files_directory"] == "F:/Inbox"
    assert moved["sorter"]["SortMobileDownloadFolder"]["source_directories"].startswith("F:/Inbox")


def test_folders_are_listed_once_and_created(tmp_path):
    config = example()
    for root, _label, _hint in first_run.ROOTS:
        config = first_run.move_root(config, root, (tmp_path / Path(root).name).as_posix())
    cfg = fff_config.deep_merge(config, secrets_example())
    rows = first_run.folders(cfg)
    assert len({row["path"].lower() for row in rows}) == len(rows)
    inbox = next(row for row in rows if row["path"].endswith("- Need to be Sorted"))
    assert len(inbox["settings"]) > 1  # one folder, several settings
    assert not any(row["exists"] for row in rows)
    assert first_run.roots_in_use(cfg) == []  # every root moved away from the examples

    assert first_run.create_folders(row["path"] for row in rows) == []
    assert all(row["exists"] for row in first_run.folders(cfg))


def test_roots_in_use_finds_the_example_roots():
    cfg = fff_config.deep_merge(example(), secrets_example())
    assert [root for root, _label, _hint in first_run.roots_in_use(cfg)] == [
        "D:/Media", "D:/Work", "D:/Archive", "C:/Users/you"]


# --------------------------------------------------------------------------- e621

class _Response:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


@pytest.mark.parametrize("status, ok, text", [
    (200, True, "Signed in to e621 as Someone."),
    (401, False, "rejected the username or API key"),
    (404, False, "has no user named 'someone'"),
    (503, False, "HTTP 503"),
])
def test_check_e621_reports_each_answer(monkeypatch, status, ok, text):
    from fff.clients import http
    calls = []
    monkeypatch.setattr(http, "get", lambda service, url, **kw: calls.append((url, kw)) or
                        _Response(status, {"name": "Someone"}))
    result = first_run.check_e621("someone", "key", "FFF/1.0 (by someone on e621)")
    assert result[0] is ok and text in result[1]
    url, kwargs = calls[0]
    assert url == "https://e621.net/users/someone.json"
    assert kwargs["auth"] == ("someone", "key")
    assert kwargs["headers"]["User-Agent"] == "FFF/1.0 (by someone on e621)"


def test_check_e621_needs_real_values_before_asking(monkeypatch):
    from fff.clients import http
    monkeypatch.setattr(http, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert first_run.check_e621("CHANGE_ME", "key", "agent")[0] is False
    assert first_run.check_e621("someone", "", "agent")[0] is False
    assert first_run.check_e621("someone", "key", "YourAppName/1.0 (by your_e621_username)")[0] is False


# --------------------------------------------------------------------------- the wizard window

@pytest.fixture
def wizard_env(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    root.withdraw()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(EXAMPLE, config_dir / "config.example.yaml")
    shutil.copy(SECRETS_EXAMPLE, config_dir / "secrets.example.yaml")
    monkeypatch.setattr(paths, "CONFIG_DIR", config_dir)
    from fff.gui import theme
    theme.apply(root)
    yield root, config_dir
    root.destroy()
    fff_config._cache = None


def pump(root, until, seconds=10.0):
    end = time.monotonic() + seconds
    while not until() and time.monotonic() < end:
        root.update()
        time.sleep(0.02)
    root.update()


def test_wizard_walks_every_step_and_gates_next(wizard_env, tmp_path, monkeypatch):
    from fff.gui import first_run_ui

    root, config_dir = wizard_env
    finished = []
    monkeypatch.setattr(first_run, "final_checks", lambda c, s, f: [("Everything", True, "fine")])
    wizard = first_run_ui.SetupWizard(root, on_finish=lambda: finished.append(True))
    state = lambda: str(wizard.next_button.cget("state"))  # noqa: E731

    assert wizard.key() == "welcome" and state() == "normal"
    wizard.feature_vars["sorting"].set(True)
    wizard.next()
    assert wizard.key() == "database"
    assert (config_dir / "config.yaml").exists() and not first_run.is_complete()
    assert state() == "disabled"                        # nothing tested yet
    wizard.passed["database"] = True
    wizard.refresh_nav()
    wizard.next()

    assert wizard.key() == "e621" and state() == "disabled"
    wizard.passed["e621"] = True
    wizard.next()

    assert wizard.key() == "folders" and state() == "disabled"   # example folders don't exist
    for root_path in list(wizard.root_values):
        wizard.move_root(root_path, (tmp_path / "lib" / Path(root_path).name).as_posix())
    wizard.create_missing()
    assert state() == "normal"
    wizard.next()

    assert wizard.key() == "finish"                     # Extras skipped: no optional features
    pump(root, lambda: state() == "normal")
    wizard.next()
    assert finished == [True] and first_run.is_complete()
    saved = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    assert saved["archive"]["download"]["images"]["safe"].startswith((tmp_path / "lib").as_posix())


def test_wizard_resumes_where_it_was_left(wizard_env):
    from fff.gui import first_run_ui

    root, _config_dir = wizard_env
    first_run.record(first_run.begin(), "database", step="e621", features={"reverse_search": True})
    wizard = first_run_ui.SetupWizard(root)
    assert wizard.key() == "e621"
    assert not wizard.skipped("extras")                # reverse search was picked
    assert wizard.satisfied("database")                # done before, not changed since


def test_manager_shows_the_wizard_until_setup_is_finished(wizard_env):
    from fff.gui import first_run_ui, manager_ui

    root, _config_dir = wizard_env
    first_run.save_state({"completed": False})
    built = []
    real_wizard = first_run_ui.SetupWizard
    first_run_ui.SetupWizard = lambda *a, **k: built.append(real_wizard(*a, **k)) or built[-1]
    try:
        manager_ui.main(root=root, run_mainloop=False)
    finally:
        first_run_ui.SetupWizard = real_wizard
    assert len(built) == 1 and "first-time setup" in root.title()
