"""Unified desktop control center for FFF workflows and tools.

Workflows are CLI commands run as child processes (output in the Activity log). Tools are
the desktop editors and reviewers; they open inside this window (see fff.gui.host), stay
alive while you move between pages, and can also be popped out into their own window.
"""
from __future__ import annotations

import importlib
import queue
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass

from fff.common import paths


def stop_process_tree(process: subprocess.Popen) -> None:
    """Stop ``process`` and everything it started. ``terminate()`` alone isn't enough on
    Windows: the venv python.exe is a launcher, so it would leave the real fff process (and
    any stage it is running) going in the background, where it overlaps the next run."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0 or process.poll() is not None:
            return
    process.terminate()


@dataclass(frozen=True)
class Action:
    title: str
    detail: str
    command: tuple[str, ...]
    changes_data: bool = False
    browser_url: str | None = None
    interactive: bool = False


@dataclass(frozen=True)
class Page:
    title: str
    detail: str
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    detail: str
    module: str                 # has build(host)
    command: tuple[str, ...]    # the standalone 'fff ...' command (for "Open in new window")
    warning: str | None = None  # shown before opening; opening asks for confirmation


PAGES = (
    Page("Overview", "Health checks and the daily workflow.", (
        Action("Check setup", "Check Python packages and configured folders.", ("doctor", "--skip-db")),
        Action("Check databases", "Test database connections and required views.", ("doctor",)),
        Action("Preview daily workflow", "Show the stages without running them.", ("daily", "--dry-run")),
        Action("Run daily workflow", "Search, sync, and sort using the current configuration.",
               ("daily",), changes_data=True),
    )),
    Page("e621 Archive", "Favorites, post records, file links, and archive maintenance.", (
        Action("Sync favorites", "Refresh e621 favorites, downloads, and post metadata.",
               ("e621", "sync"), changes_data=True),
        Action("Add missing favorites", "Favorite e621 posts already known to the local library.",
               ("e621", "favorites", "add-missing"), changes_data=True),
        Action("Find deleted-post files", "Search for local files belonging to deleted posts.",
               ("e621", "files", "find-deleted")),
        Action("Review unfavorited files", "Choose whether to archive or delete local files.",
               ("e621", "files", "unfavorited"), changes_data=True, interactive=True),
        Action("Verify file paths", "Check and repair paths stored for e621 posts.",
               ("e621", "verify", "file-paths"), changes_data=True),
        Action("Favorite from MD5 list", "Look up hashes from data/md5_search/md5.txt.",
               ("e621", "favorites", "from-md5"), changes_data=True),
        Action("View takedowns", "Inspect deletion and takedown details.", ("e621", "report", "takedowns")),
        Action("Open archive gallery", "Browse the local archive in your web browser.",
               ("web",), browser_url="http://127.0.0.1:5000"),
    )),
    Page("Source Search", "Find image sources and process e621 or FurAffinity matches.", (
        Action("Search inbox folders", "Run reverse-image and FuzzySearch on configured folders.",
               ("fluffle", "search"), changes_data=True),
        Action("Process e621 matches", "Favorite and download exact e621 matches; apply configured rules.",
               ("fluffle", "process-e621"), changes_data=True),
        Action("Process FurAffinity matches", "Favorite and download exact FurAffinity matches.",
               ("fluffle", "process-fa"), changes_data=True),
        Action("Favorite matched FA posts", "Favorite FA posts matched to the same local image on both sites.",
               ("fluffle", "sync-fa-favorites"), changes_data=True),
        Action("Sync FA source links", "Read FA links from e621 posts and favorite missing FA favorites.",
               ("fa", "sync-external"), changes_data=True),
        Action("Rename FA authors", "Rename files using author IDs from the search database.",
               ("fa", "rename-authors"), changes_data=True, interactive=True),
    )),
    Page("Library", "Sort, hash, archive, and maintain local media.", (
        Action("Run sorting workflow", "Run tag sorting, file sorting, artist sorting, archiving, and maintenance.",
               ("sort", "workflow"), changes_data=True),
        Action("Sort files", "Sort files into configured destinations.", ("sort", "sort"), changes_data=True),
        Action("Sort by tags", "Apply configured e621 tag rules.", ("sort", "tag-sort"), changes_data=True),
        Action("Sort by artists", "Apply the favorite-artist rules.", ("sort", "artist-sort"), changes_data=True),
        Action("Archive old files", "Move eligible files to configured archive folders.",
               ("sort", "archive"), changes_data=True),
        Action("Hash files", "Calculate and update configured file hashes.", ("hash",), changes_data=True),
    )),
    Page("Databases", "Inspect configuration and manage the test environment.", (
        Action("Show configuration", "Display current settings with credentials redacted.", ("config", "show")),
        Action("Show test folders", "Compare production and test folder paths.", ("config", "test-dirs")),
        Action("Inspect hash database", "Print hash database schema and columns.", ("db", "inspect-hashes")),
        Action("Copy database schema", "Refresh test databases from production schema only.",
               ("db", "copy-schema"), changes_data=True),
        Action("Copy database data", "Replace test database contents with production data.",
               ("db", "copy-data"), changes_data=True, interactive=True),
    )),
)

TOOLS = (
    Tool("settings", "Settings", "Edit config.yaml and credentials, then run first-time checks.",
         "fff.gui.setup_ui", ("setup",)),
    Tool("tag-rules", "Tag rules", "Edit the e621 tag-sorting rules in tag_sorting.yaml.",
         "fff.gui.tag_sort_config_ui_v2", ("gui", "tag-sort-config")),
    Tool("artist-rules", "Artist rules", "Edit the favorite-artist sorting rules in sort_artists.yaml.",
         "fff.gui.artist_config_ui", ("gui", "artist-config")),
    Tool("duplicates", "Duplicates", "Scan for duplicate files and choose which copies to keep.",
         "fff.gui.duplicate_manager_ui", ("gui", "duplicates"),
         warning="Deleting, moving or merging duplicates changes files and database rows."),
    Tool("blacklist-review", "Blacklist review", "Review archive posts that carry blacklisted tags.",
         "fff.gui.blacklist_review_ui", ("gui", "blacklist-review"),
         warning="Opening this first unfavorites every post with a critical or AI tag and sends "
                 "its file to the Recycle Bin. Delete does the same for the post on screen."),
    Tool("artist-review", "Artist review", "Review archive posts by the artists in review_artists.yaml.",
         "fff.gui.artists_review_ui", ("gui", "artist-review"),
         warning="Delete unfavorites the post on e621 and sends its file to the Recycle Bin."),
    Tool("generic-tags", "Generic tags", "Mark archive tags as generic or not, and copy a query that leaves "
         "the generic ones out.", "fff.gui.generic_tags_ui", ("gui", "generic-tags")),
)


def current_environment() -> str:
    from fff.common import config as fff_config

    try:
        return fff_config.environment(fff_config.load())
    except Exception:  # no config yet (first run): the Settings tool creates it
        return "prod"


class ManagerApp:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        from fff.gui import theme

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.palette = theme.PALETTE
        theme.apply(root)
        root.title("FFF Manager")
        width = min(1480, max(root.winfo_screenwidth() - 60, 1000))
        height = min(920, max(root.winfo_screenheight() - 80, 700))
        root.geometry(f"{width}x{height}")
        root.minsize(1000, 680)

        self.environment = current_environment()
        self.events: queue.Queue = queue.Queue()
        self.active_process: subprocess.Popen | None = None
        self.current_action: Action | None = None
        self.stop_web_requested = False
        self.closing = False
        self.run_buttons = []
        self.nav_buttons: dict[tuple[str, object], object] = {}
        self.page_views: dict[int, object] = {}
        self.tool_intros: dict[str, object] = {}
        self.tool_hosts: dict[str, object] = {}
        self.tool_apps: dict[str, object] = {}
        self.current: tuple[str, object] = ("page", 0)

        self._build_shell()
        root.protocol("WM_DELETE_WINDOW", self.close_manager)
        self.show(("page", 0))
        self._poll_events()

    # --- layout -----------------------------------------------------------------------------

    def _build_shell(self) -> None:
        tk, ttk, p = self.tk, self.ttk, self.palette
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(shell, width=220, style="Sidebar.TFrame", padding=(14, 20, 14, 16))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="FFF MANAGER", style="SidebarTitle.TLabel").pack(anchor="w", padx=4)
        ttk.Label(sidebar, text="Archive · Search · Library", style="SidebarHint.TLabel").pack(
            anchor="w", padx=4, pady=(2, 18))
        ttk.Label(sidebar, text="WORKFLOWS", style="SidebarHint.TLabel").pack(anchor="w", padx=4, pady=(0, 4))
        for index, page in enumerate(PAGES):
            self._nav_button(sidebar, ("page", index), page.title)
        ttk.Label(sidebar, text="TOOLS", style="SidebarHint.TLabel").pack(anchor="w", padx=4, pady=(18, 4))
        for tool in TOOLS:
            self._nav_button(sidebar, ("tool", tool.key), tool.title)

        env_box = ttk.Frame(sidebar, style="Sidebar.TFrame")
        env_box.pack(side="bottom", fill="x", padx=4)
        ttk.Label(env_box, text="ENVIRONMENT", style="SidebarHint.TLabel").pack(anchor="w", pady=(0, 4))
        self.env_var = tk.StringVar(value=self.environment)
        env_combo = ttk.Combobox(env_box, textvariable=self.env_var, values=("prod", "test"),
                                 state="readonly", width=10)
        env_combo.pack(anchor="w", fill="x")
        env_combo.bind("<<ComboboxSelected>>", self._switch_environment)

        main = ttk.Frame(shell, padding=(24, 18, 24, 12))
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main)
        header.pack(fill="x")
        self.title_label = ttk.Label(header, text="", style="Title.TLabel")
        self.title_label.pack(side="left", anchor="w")
        self.stop_web_button = ttk.Button(header, text="Stop Web UI", style="Tool.TButton",
                                          state="disabled", command=self.stop_web_ui)
        self.stop_web_button.pack(side="right")
        self.close_tool_button = ttk.Button(header, text="Close tool", style="Tool.TButton",
                                            command=self.close_current_tool)
        self.subtitle_label = ttk.Label(main, text="", style="Muted.TLabel")
        self.subtitle_label.pack(anchor="w", pady=(0, 12))

        self.body = ttk.Frame(main)
        self.body.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(main, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        # Workflow pages share one view: the cards of the selected page above the Activity log.
        self.workflow_view = ttk.Frame(self.body)
        self.cards_holder = ttk.Frame(self.workflow_view)
        self.cards_holder.pack(fill="x")
        # The Activity log takes all the height the cards don't need.
        activity = ttk.LabelFrame(self.workflow_view, text="Activity", padding=(10, 8))
        activity.pack(fill="both", expand=True, pady=(12, 0))
        activity.columnconfigure(0, weight=1)
        activity.rowconfigure(0, weight=1)
        self.output = tk.Text(activity, height=9, wrap="word", state="disabled", font="TkFixedFont",
                              padx=6, pady=6)
        self.output.grid(row=0, column=0, sticky="nsew")
        for tag in ("ok", "warning", "error"):
            self.output.tag_configure(tag, foreground=p[tag])
        scroll = ttk.Scrollbar(activity, orient="vertical", command=self.output.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.output.configure(yscrollcommand=scroll.set)

    def _nav_button(self, parent, key, text) -> None:
        button = self.ttk.Button(parent, text=text, style="Nav.TButton", command=lambda: self.show(key))
        button.pack(fill="x", pady=1)
        self.nav_buttons[key] = button

    def _page_view(self, index: int):
        if index in self.page_views:
            return self.page_views[index]
        ttk = self.ttk
        view = ttk.Frame(self.cards_holder)
        for col in range(2):
            view.columnconfigure(col, weight=1, uniform="cards")
        for action_index, action in enumerate(PAGES[index].actions):
            row, col = divmod(action_index, 2)
            card = ttk.Frame(view, style="Card.TFrame", padding=(14, 10))
            card.grid(row=row, column=col, sticky="nsew", padx=(0, 10) if col == 0 else (0, 0), pady=(0, 10))
            card.columnconfigure(0, weight=1)
            ttk.Label(card, text=action.title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, text=action.detail, style="CardBody.TLabel", wraplength=380,
                      justify="left").grid(row=1, column=0, sticky="w", pady=(2, 0))
            if action.changes_data:
                ttk.Label(card, text="Changes files or databases", style="CardWarning.TLabel").grid(
                    row=2, column=0, sticky="w", pady=(4, 0))
            button = ttk.Button(card, text="Open" if action.browser_url else "Run",
                                style="Primary.TButton" if not action.changes_data else "TButton",
                                command=lambda a=action: self.run_action(a))
            button.grid(row=0, column=1, rowspan=3, sticky="e", padx=(12, 0))
            if self.active_process is not None:
                button.configure(state="disabled")
            self.run_buttons.append(button)
        self.page_views[index] = view
        return view

    def _tool_intro(self, tool: Tool):
        if tool.key in self.tool_intros:
            return self.tool_intros[tool.key]
        ttk = self.ttk
        intro = ttk.Frame(self.body)
        card = ttk.Frame(intro, style="Card.TFrame", padding=(22, 18))
        card.pack(anchor="nw", fill="x")
        ttk.Label(card, text=tool.title, style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(card, text=tool.detail, style="CardBody.TLabel", wraplength=640, justify="left").pack(
            anchor="w", pady=(4, 0))
        if tool.warning:
            ttk.Label(card, text=tool.warning, style="CardWarning.TLabel", wraplength=640,
                      justify="left").pack(anchor="w", pady=(8, 0))
        buttons = ttk.Frame(card, style="Surface.TFrame")
        buttons.pack(anchor="w", pady=(14, 0))
        ttk.Button(buttons, text=f"Open {tool.title.lower()}", style="Primary.TButton",
                   command=lambda: self.open_tool(tool)).pack(side="left")
        ttk.Button(buttons, text="Open in a separate window",
                   command=lambda: self.open_tool_window(tool)).pack(side="left", padx=(8, 0))
        self.tool_intros[tool.key] = intro
        return intro

    # --- navigation ---------------------------------------------------------------------------

    def show(self, key) -> None:
        if self.closing:
            return
        self.current = key
        for nav_key, button in self.nav_buttons.items():
            button.configure(style="ActiveNav.TButton" if nav_key == key else "Nav.TButton")
        for child in self.body.winfo_children():
            child.pack_forget()
        for child in self.cards_holder.winfo_children():
            child.pack_forget()
        self.close_tool_button.pack_forget()

        kind, value = key
        if kind == "page":
            page = PAGES[value]
            self.title_label.configure(text=page.title)
            self.subtitle_label.configure(text=page.detail)
            self._page_view(value).pack(fill="both", expand=True)
            self.workflow_view.pack(fill="both", expand=True)
            return
        tool = self._tool(value)
        self.title_label.configure(text=tool.title)
        host = self.tool_hosts.get(tool.key)
        if host is None:
            self.subtitle_label.configure(text=tool.detail)
            self._tool_intro(tool).pack(fill="both", expand=True)
        else:
            self.subtitle_label.configure(text=host.title() or tool.detail)
            self.close_tool_button.pack(side="right", padx=(0, 8))
            host.pack(fill="both", expand=True)
            host.focus_set()

    @staticmethod
    def _tool(key: str) -> Tool:
        return next(tool for tool in TOOLS if tool.key == key)

    def _refresh_nav_label(self, tool: Tool) -> None:
        open_mark = "  ●" if tool.key in self.tool_hosts else ""
        self.nav_buttons[("tool", tool.key)].configure(text=tool.title + open_mark)

    # --- embedded tools -----------------------------------------------------------------------

    def open_tool(self, tool: Tool) -> None:
        from tkinter import messagebox

        from fff.gui import host as tool_host

        if tool.key in self.tool_hosts:
            self.show(("tool", tool.key))
            return
        if tool.warning and not messagebox.askyesno(
                f"Open {tool.title}", f"{tool.detail}\n\n{tool.warning}\n\nContinue?", parent=self.root):
            return

        def title_changed(text: str) -> None:
            if self.current == ("tool", tool.key):
                self.subtitle_label.configure(text=text or tool.detail)

        host = tool_host.ToolHost(self.body, on_title=title_changed)
        self.tool_hosts[tool.key] = host
        tool_host.on_destroy(host, lambda: self._tool_closed(tool))
        self._refresh_nav_label(tool)
        self.show(("tool", tool.key))
        self.status.set(f"Opening {tool.title}…")
        self.root.update_idletasks()
        try:
            self.tool_apps[tool.key] = importlib.import_module(tool.module).build(host)
        except Exception as exc:  # show the failure where the tool would have been
            for child in host.winfo_children():
                child.destroy()
            tool_host.message_panel(host, f"{tool.title} could not open", f"{type(exc).__name__}: {exc}",
                                    tone="warning", actions=(("Close", host.destroy),))
            self.status.set(f"{tool.title} could not open")
            return
        self.status.set(f"{tool.title} is open")

    def _tool_closed(self, tool: Tool) -> None:
        self.tool_hosts.pop(tool.key, None)
        self.tool_apps.pop(tool.key, None)
        if self.closing:
            return
        try:
            self._refresh_nav_label(tool)
        except self.tk.TclError:  # the whole window is being torn down
            self.closing = True
            return
        self.status.set(f"{tool.title} closed")
        if self.current == ("tool", tool.key):
            self.show(self.current)

    def close_current_tool(self) -> None:
        kind, key = self.current
        host = self.tool_hosts.get(key) if kind == "tool" else None
        if host is not None:
            host.request_close()

    def close_all_tools(self) -> bool:
        """Close every open tool (each may ask to save). False if one stayed open."""
        for key, host in list(self.tool_hosts.items()):
            self.show(("tool", key))
            if not host.request_close():
                return False
        return True

    def open_tool_window(self, tool: Tool) -> None:
        try:
            subprocess.Popen(self._fff_command(tool.command), cwd=paths.ROOT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self.status.set(f"Could not open {tool.title}: {exc}")
            return
        self.status.set(f"Opened {tool.title} in a separate window")

    # --- workflow commands --------------------------------------------------------------------

    def _fff_command(self, args: tuple[str, ...]) -> list[str]:
        return [sys.executable, "-m", "fff", "--env", self.environment, *args]

    def log_line(self, text: str, tag: str = "") -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text + "\n", tag)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _set_run_buttons(self, state: str) -> None:
        for button in self.run_buttons:
            button.configure(state=state)

    def run_action(self, action: Action) -> None:
        from tkinter import messagebox

        if self.active_process is not None and self.active_process.poll() is None:
            messagebox.showinfo("FFF Manager", "A command is already running.", parent=self.root)
            return
        if action.changes_data and not messagebox.askyesno(
                "Confirm action",
                f"{action.title}\n\n{action.detail}\n\nThis may contact live services or change "
                f"database/files ({self.environment} environment). Continue?",
                parent=self.root):
            return

        command = self._fff_command(action.command)
        self.log_line(f"\n> {' '.join(command)}", "warning" if action.changes_data else "")
        self.status.set(f"Running: {action.title}")
        self._set_run_buttons("disabled")
        self.current_action = action
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if action.interactive \
                else getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process_options = {"cwd": paths.ROOT, "creationflags": creationflags}
            if not action.interactive:
                process_options.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", bufsize=1)
            self.active_process = subprocess.Popen(command, **process_options)
        except OSError as exc:
            self.active_process = None
            self.current_action = None
            self.status.set("Could not start command")
            self.log_line(f"Could not start command: {exc}", "error")
            self._set_run_buttons("normal")
            return

        if action.browser_url:
            self.stop_web_requested = False
            self.stop_web_button.configure(state="normal")
            self.root.after(1200, lambda url=action.browser_url: webbrowser.open(url))

        def worker(child: subprocess.Popen) -> None:
            if child.stdout is not None:
                for line in child.stdout:
                    self.events.put(("line", line))
            self.events.put(("done", child.wait()))

        threading.Thread(target=worker, args=(self.active_process,), name="fff-manager-command",
                         daemon=True).start()

    def stop_web_ui(self) -> None:
        if self.active_process is None or self.current_action is None or self.current_action.browser_url is None:
            return
        if self.active_process.poll() is None:
            self.stop_web_requested = True
            self.status.set("Stopping local archive gallery…")
            self.log_line("Stopping local archive gallery…", "warning")
            self.stop_web_button.configure(state="disabled")
            stop_process_tree(self.active_process)

    def _poll_events(self) -> None:
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "line":
                    text = value.rstrip("\r\n")
                    lowered = text.lower()
                    self.log_line(text, "error" if "error" in lowered or "fail" in lowered else "")
                elif event == "done":
                    action = self.current_action
                    self.active_process = None
                    self.current_action = None
                    self.stop_web_button.configure(state="disabled")
                    if self.stop_web_requested:
                        self.stop_web_requested = False
                        self.status.set("Archive gallery stopped")
                        self.log_line("\nArchive gallery stopped.", "ok")
                    elif value == 0:
                        self.status.set(f"Finished: {action.title if action else 'command'}")
                        self.log_line(f"\nFinished successfully (exit {value}).", "ok")
                    else:
                        self.status.set(f"Command exited with code {value}")
                        self.log_line(f"\nCommand exited with code {value}.", "error")
                    self._set_run_buttons("normal")
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(100, self._poll_events)

    # --- environment and shutdown -------------------------------------------------------------

    def _switch_environment(self, _event=None) -> None:
        from tkinter import messagebox

        target = self.env_var.get()
        if target == self.environment:
            return
        if self.active_process is not None and self.active_process.poll() is None:
            messagebox.showinfo("FFF Manager", "Wait for the running command to finish first.", parent=self.root)
            self.env_var.set(self.environment)
            return
        if not messagebox.askyesno(
                "Switch environment",
                f"Restart the manager in the {target} environment?\n\nOpen tools are closed first.",
                parent=self.root):
            self.env_var.set(self.environment)
            return
        if not self.close_all_tools():
            self.env_var.set(self.environment)
            return
        try:
            subprocess.Popen([sys.executable, "-m", "fff", "--env", target, "gui", "manager"], cwd=paths.ROOT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            messagebox.showerror("Switch environment", f"Could not restart the manager: {exc}", parent=self.root)
            self.env_var.set(self.environment)
            return
        self.closing = True
        self.root.destroy()

    def close_manager(self) -> None:
        from tkinter import messagebox

        running = self.active_process is not None and self.active_process.poll() is None
        if running and not messagebox.askyesno(
                "Command running", "Close the manager and stop the active command?", parent=self.root):
            return
        if not self.close_all_tools():
            return
        if running:
            stop_process_tree(self.active_process)
        self.closing = True
        self.root.destroy()


def build(root) -> ManagerApp:
    return ManagerApp(root)


def main(argv=None, *, root=None, run_mainloop: bool = True) -> int:
    import tkinter as tk

    from fff.common import consent, first_run

    root = tk.Tk() if root is None else root
    if first_run.is_complete():
        build(root)
        if run_mainloop:
            # First run: actions launched from here have no console to ask in
            root.after(300, lambda: consent.ask_in_dialog(consent.BROWSER_COOKIES, parent=root))
    else:
        # Not set up yet: the guided setup takes the whole window, and the Manager replaces
        # it once setup is finished (the wizard asks for the cookie consent itself).
        from fff.gui import first_run_ui, theme

        theme.apply(root)
        root.title("FFF Manager – first-time setup")
        root.geometry("1180x800")

        def finished():
            for child in root.winfo_children():
                child.destroy()
            build(root)

        first_run_ui.build(root, on_finish=finished)
    if run_mainloop:
        root.mainloop()
    return 0
