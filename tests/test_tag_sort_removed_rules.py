"""Saving the tag-sort GUI after deleting a rule sends that rule's files to "Need to be Sorted"."""
import pytest
import yaml


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def setup(tmp_path, monkeypatch, tk_root):
    from tkinter import messagebox

    from fff.common import config as fff_config
    from fff.gui import tag_sort_config_ui_v2 as tsc
    from fff.sorter.handlers import database_manager

    queue = tmp_path / "Need to be Sorted"
    queue.mkdir()
    keep, gone = tmp_path / "Fav" / "Yoshi", tmp_path / "Fav" / "Koopas"
    for folder, name in ((keep, "yoshi.png"), (gone, "koopa.png"), (gone / "Bowser", "bowser.png")):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(name, encoding="utf-8")

    cfg_path = tmp_path / "tag_sorting.yaml"
    cfg_path.write_text(yaml.safe_dump({"enabled": True, "rules": [
        {"rule_id": "keep", "needed_tags": ["yoshi"], "target_directory": str(keep)},
        {"rule_id": "gone", "needed_tags": ["koopa"], "target_directory": str(gone)},
    ]}), encoding="utf-8")

    main_cfg = {"SortMobileDownloadFolder": {"source_directories": f"{queue}\n"},
                "destination_directories": {}, "other": {"fav_directory": str(tmp_path / "Fav")}}
    monkeypatch.setattr(tsc, "_load_main_config", lambda *a, **k: main_cfg)
    monkeypatch.setattr(fff_config, "load", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no config")))
    monkeypatch.setattr(database_manager, "DatabaseManager",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no database")))
    answers = {"move": True}
    asked = []

    def askyesno(title, message, **_):
        asked.append(title)
        return answers["move"] if title == "Removed Rules" else True

    monkeypatch.setattr(messagebox, "askyesno", askyesno)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: asked.append("warning"))

    app = tsc.TagSortConfigApp(tk_root, cfg_path)
    gone_idx = next(i for i, n in enumerate(app._flat) if n.rule.get("rule_id") == "gone")
    return {"app": app, "gone_idx": gone_idx, "queue": queue, "keep": keep, "gone": gone,
            "answers": answers, "asked": asked, "cfg_path": cfg_path}


def test_deleting_a_rule_and_saving_moves_its_files_to_need_to_be_sorted(setup):
    s = setup
    s["app"]._cmd_delete(s["gone_idx"])
    s["app"]._save()

    assert "Removed Rules" in s["asked"]
    assert sorted(p.name for p in s["queue"].iterdir()) == ["bowser.png", "koopa.png"]
    assert not s["gone"].exists()
    assert (s["keep"] / "yoshi.png").exists()
    rules = yaml.safe_load(s["cfg_path"].read_text(encoding="utf-8"))["rules"]
    assert [r["rule_id"] for r in rules] == ["keep"]


def test_answering_no_leaves_the_files_and_does_not_ask_again(setup):
    s = setup
    s["answers"]["move"] = False
    s["app"]._cmd_delete(s["gone_idx"])
    s["app"]._save()
    s["app"]._save()

    assert s["asked"].count("Removed Rules") == 1
    assert (s["gone"] / "koopa.png").exists()
    assert not any(s["queue"].iterdir())


def test_saving_without_removing_a_rule_moves_nothing(setup):
    s = setup
    s["app"]._save()

    assert "Removed Rules" not in s["asked"]
    assert (s["gone"] / "koopa.png").exists()
