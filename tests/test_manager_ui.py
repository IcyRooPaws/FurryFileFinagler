import importlib.util

import pytest

from fff.gui.manager_ui import PAGES, TOOLS


def test_manager_pages_and_actions_are_navigable():
    assert len(PAGES) >= 5
    assert len({page.title for page in PAGES}) == len(PAGES)
    for page in PAGES:
        assert page.title and page.detail
        assert page.actions
        for action in page.actions:
            assert action.title and action.detail
            assert action.command and all(isinstance(part, str) and part for part in action.command)


def test_manager_separates_read_only_and_changing_actions():
    actions = {action.title: action for page in PAGES for action in page.actions}

    assert not actions["Check setup"].changes_data
    assert not actions["Preview daily workflow"].changes_data
    assert actions["Run daily workflow"].changes_data


def test_manager_gallery_uses_local_only_url():
    gallery = next(action for page in PAGES for action in page.actions
                   if action.title == "Open archive gallery")
    assert gallery.browser_url == "http://127.0.0.1:5000"


def test_manager_tools_are_unique_and_embeddable():
    assert len({tool.key for tool in TOOLS}) == len(TOOLS)
    assert len({tool.title for tool in TOOLS}) == len(TOOLS)
    for tool in TOOLS:
        assert tool.title and tool.detail and tool.command
        assert importlib.util.find_spec(tool.module) is not None, tool.module


def test_manager_warns_before_tools_that_delete():
    tools = {tool.key: tool for tool in TOOLS}

    assert "Recycle Bin" in tools["blacklist-review"].warning
    assert tools["duplicates"].warning
    assert tools["settings"].warning is None
    assert tools["tag-rules"].warning is None


# --- Tk-backed tests (skipped where no display is available) --------------------------------

@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    root.geometry("300x200")
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def test_tool_host_accepts_root_window_calls(tk_root):
    from fff.gui.host import ToolHost

    titles, closes = [], []
    host = ToolHost(tk_root, on_title=titles.append)
    host.title("Tag Sort Configuration")
    host.geometry("1280x740")
    host.minsize(820, 480)
    host.configure(bg="#FFFFFF")
    host.protocol("WM_DELETE_WINDOW", lambda: closes.append(True))

    assert titles == ["Tag Sort Configuration"]
    assert host.title() == "Tag Sort Configuration"
    assert host.request_close() is False  # the handler chose not to destroy
    assert closes == [True]


def test_tool_host_quit_closes_only_the_tool(tk_root):
    from fff.gui.host import ToolHost

    host = ToolHost(tk_root)
    host.quit()

    assert not host.winfo_exists()
    assert tk_root.winfo_exists()


def test_tool_host_shortcuts_only_reach_the_visible_tool(tk_root):
    from fff.gui.host import ToolHost, _KeyRouter

    hits = []
    visible, hidden = ToolHost(tk_root), ToolHost(tk_root)
    visible.bind("<Control-s>", lambda e: hits.append("visible"))
    hidden.bind("<Control-s>", lambda e: hits.append("hidden"))
    visible.pack(fill="both", expand=True)
    tk_root.update()

    _KeyRouter.of(visible).dispatch("<Control-s>", None)
    assert hits == ["visible"]

    visible.destroy()
    hidden.pack(fill="both", expand=True)
    tk_root.update()
    _KeyRouter.of(hidden).dispatch("<Control-s>", None)
    assert hits == ["visible", "hidden"]


def test_manager_navigates_every_page_and_tool_without_opening_tools(tk_root):
    from fff.gui.manager_ui import ManagerApp

    app = ManagerApp(tk_root)
    for index, page in enumerate(PAGES):
        app.show(("page", index))
        assert app.title_label.cget("text") == page.title
    for tool in TOOLS:
        app.show(("tool", tool.key))
        assert app.title_label.cget("text") == tool.title
    assert app.tool_hosts == {}
    assert str(app.stop_web_button.cget("state")) == "disabled"
