"""Unified desktop control center for FFF workflows and tools."""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass

from fff.common import paths


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


PAGES = (
    Page("Overview", "Health checks and the daily workflow.", (
        Action("Check setup", "Check Python packages and configured folders.", ("doctor", "--skip-db")),
        Action("Check databases", "Test database connections and required views.", ("doctor",)),
        Action("Preview daily workflow", "Show the stages without running them.", ("daily", "--dry-run")),
        Action("Run daily workflow", "Search, sync, and sort using the current configuration.",
               ("daily",), changes_data=True),
        Action("Configure FFF", "Open guided setup for settings and credentials.", ("setup",), changes_data=True),
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
        Action("Manage duplicates", "Open the duplicate review interface.", ("gui", "duplicates"),
               changes_data=True),
    )),
    Page("Review & Configure", "Open the desktop editors and review interfaces.", (
        Action("Edit tag-sorting rules", "Open the tag-sorting configuration editor.", ("gui", "tag-sort-config"),
               changes_data=True),
        Action("Edit artist rules", "Open the favorite-artist configuration editor.", ("gui", "artist-config"),
               changes_data=True),
        Action("Review artists", "Review archive posts by configured artists.", ("gui", "artist-review"),
               changes_data=True),
        Action("Review blacklisted posts", "Critical/AI-tagged posts may be unfavorited and sent to Recycle Bin.",
               ("gui", "blacklist-review"), changes_data=True),
        Action("Open archive gallery", "Browse the local archive in your web browser.",
               ("web",), browser_url="http://127.0.0.1:5000"),
    )),
    Page("Settings & Databases", "Inspect configuration and manage the test environment.", (
        Action("Show configuration", "Display current settings with credentials redacted.", ("config", "show")),
        Action("Show test folders", "Compare production and test folder paths.", ("config", "test-dirs")),
        Action("Inspect hash database", "Print hash database schema and columns.", ("db", "inspect-hashes")),
        Action("Copy database schema", "Refresh test databases from production schema only.",
               ("db", "copy-schema"), changes_data=True),
        Action("Copy database data", "Replace test database contents with production data.",
             ("db", "copy-data"), changes_data=True, interactive=True),
    )),
)


def main(argv=None, *, root=None, run_mainloop: bool = True) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    palette = {"page": "#F1F6F6", "surface": "#FFFFFF", "sidebar": "#183B45",
               "sidebar_text": "#E5F0F0", "ink": "#19343B", "muted": "#60777D",
               "accent": "#087F8C", "accent_hover": "#066A75", "line": "#D4E0E2",
               "ok": "#18794E", "warning": "#A45B08", "error": "#B42318"}

    root = tk.Tk() if root is None else root
    root.title("FFF Manager")
    root.geometry("1220x860")
    root.minsize(940, 680)
    root.configure(background=palette["page"])

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TFrame", background=palette["page"])
    style.configure("Surface.TFrame", background=palette["surface"])
    style.configure("TLabel", background=palette["page"], foreground=palette["ink"],
                    font=("Segoe UI", 10))
    style.configure("Muted.TLabel", foreground=palette["muted"], font=("Segoe UI", 9))
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 21), foreground=palette["ink"])
    style.configure("Sidebar.TFrame", background=palette["sidebar"])
    style.configure("SidebarTitle.TLabel", background=palette["sidebar"], foreground="#FFFFFF",
                    font=("Segoe UI Semibold", 16))
    style.configure("SidebarHint.TLabel", background=palette["sidebar"], foreground="#B9D0D2",
                    font=("Segoe UI", 9))
    style.configure("Primary.TButton", background=palette["accent"], foreground="#FFFFFF",
                    padding=(14, 8), font=("Segoe UI Semibold", 10), borderwidth=0)
    style.map("Primary.TButton", background=[("active", palette["accent_hover"])])
    style.configure("Tool.TButton", padding=(12, 7), font=("Segoe UI Semibold", 9))

    shell = ttk.Frame(root)
    shell.pack(fill="both", expand=True)
    sidebar = ttk.Frame(shell, width=225, style="Sidebar.TFrame", padding=(18, 22))
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)
    ttk.Label(sidebar, text="FFF MANAGER", style="SidebarTitle.TLabel").pack(anchor="w")
    ttk.Label(sidebar, text="Archive · Search · Library", style="SidebarHint.TLabel").pack(
        anchor="w", pady=(4, 24))
    ttk.Label(sidebar, text="WORKSPACES", style="SidebarHint.TLabel").pack(anchor="w", pady=(0, 8))

    main_area = ttk.Frame(shell, padding=(26, 20, 26, 16))
    main_area.pack(side="left", fill="both", expand=True)
    header = ttk.Frame(main_area)
    header.pack(fill="x", pady=(0, 14))
    title_label = ttk.Label(header, text="Overview", style="Title.TLabel")
    title_label.pack(side="left", anchor="w")
    environment = tk.StringVar(value="prod")
    stop_web_requested = False
    env_box = ttk.Combobox(header, textvariable=environment, values=("prod", "test"),
                           state="readonly", width=10)
    env_box.pack(side="right", padx=(8, 0))
    ttk.Label(header, text="Environment").pack(side="right")
    subtitle_label = ttk.Label(main_area, text="Choose a workflow or open a focused tool.",
                               style="Muted.TLabel")
    subtitle_label.pack(anchor="w", pady=(0, 14))

    content = ttk.Frame(main_area)
    content.pack(fill="both", expand=True)
    activity = ttk.LabelFrame(main_area, text="Activity", padding=(10, 8))
    activity.pack(fill="x", pady=(14, 0))
    activity.columnconfigure(0, weight=1)
    activity.rowconfigure(0, weight=1)
    output = tk.Text(activity, height=9, wrap="word", state="disabled", relief="flat",
                     background=palette["surface"], foreground=palette["ink"],
                     font=("Cascadia Mono", 9), padx=6, pady=6)
    output.grid(row=0, column=0, sticky="nsew")
    output.tag_configure("ok", foreground=palette["ok"])
    output.tag_configure("warning", foreground=palette["warning"])
    output.tag_configure("error", foreground=palette["error"])
    output_scroll = ttk.Scrollbar(activity, orient="vertical", command=output.yview)
    output_scroll.grid(row=0, column=1, sticky="ns")
    output.configure(yscrollcommand=output_scroll.set)
    status = tk.StringVar(value="Ready")
    status_label = ttk.Label(main_area, textvariable=status, style="Muted.TLabel")
    status_label.pack(anchor="w", pady=(7, 0))

    nav_buttons = []
    events: queue.Queue = queue.Queue()
    active_process: subprocess.Popen | None = None
    current_action: Action | None = None
    stop_web_button = ttk.Button(header, text="Stop Web UI", style="Tool.TButton",
                                 state="disabled")
    stop_web_button.pack(side="right", padx=(0, 18))

    def stop_web_ui() -> None:
        nonlocal stop_web_requested
        if active_process is None or current_action is None or current_action.browser_url is None:
            return
        if active_process.poll() is None:
            stop_web_requested = True
            status.set("Stopping local archive gallery…")
            log_line("Stopping local archive gallery…", "warning")
            stop_web_button.configure(state="disabled")
            active_process.terminate()

    stop_web_button.configure(command=stop_web_ui)

    def log_line(text: str, tag: str = "") -> None:
        output.configure(state="normal")
        output.insert("end", text + "\n", tag)
        output.see("end")
        output.configure(state="disabled")

    def poll_events() -> None:
        nonlocal active_process, current_action, stop_web_requested
        try:
            while True:
                event, value = events.get_nowait()
                if event == "line":
                    text = value.rstrip("\r\n")
                    log_line(text, "error" if "error" in text.lower() or "fail" in text.lower() else "")
                elif event == "browser":
                    webbrowser.open(value)
                elif event == "done":
                    action = current_action
                    active_process = None
                    current_action = None
                    stop_web_button.configure(state="disabled")
                    if stop_web_requested:
                        stop_web_requested = False
                        status.set("Archive gallery stopped")
                        log_line("\nArchive gallery stopped.", "ok")
                    elif value == 0:
                        status.set(f"Finished: {action.title if action else 'command'}")
                        log_line(f"\nFinished successfully (exit {value}).", "ok")
                    else:
                        status.set(f"Command exited with code {value}")
                        log_line(f"\nCommand exited with code {value}.", "error")
                    for button in run_buttons:
                        button.configure(state="normal")
        except queue.Empty:
            pass
        root.after(100, poll_events)

    run_buttons = []

    def run_action(action: Action) -> None:
        nonlocal active_process, current_action, stop_web_requested
        if active_process is not None and active_process.poll() is None:
            messagebox.showinfo("FFF Manager", "A command is already running.", parent=root)
            return
        if action.changes_data and not messagebox.askyesno(
                "Confirm action",
                f"{action.title}\n\n{action.detail}\n\nThis may contact live services or change database/files. Continue?",
                parent=root):
            return

        command = [sys.executable, "-m", "fff"]
        if environment.get() == "test":
            command.extend(("--env", "test"))
        command.extend(action.command)
        log_line(f"\n> {' '.join(command)}", "warning" if action.changes_data else "")
        status.set(f"Running: {action.title}")
        for button in run_buttons:
            button.configure(state="disabled")
        current_action = action
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if action.interactive \
                else getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process_options = {"cwd": paths.ROOT, "creationflags": creationflags}
            if not action.interactive:
                process_options.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", bufsize=1)
            active_process = subprocess.Popen(command, **process_options)
        except OSError as exc:
            active_process = None
            current_action = None
            status.set("Could not start command")
            log_line(f"Could not start command: {exc}", "error")
            for button in run_buttons:
                button.configure(state="normal")
            return

        process = active_process
        if action.browser_url:
            stop_web_requested = False
            stop_web_button.configure(state="normal")
            root.after(1200, lambda url=action.browser_url: webbrowser.open(url))

        def worker(child: subprocess.Popen) -> None:
            if child.stdout is not None:
                for line in child.stdout:
                    events.put(("line", line))
            events.put(("done", child.wait()))

        threading.Thread(target=worker, args=(process,), name="fff-manager-command", daemon=True).start()

    def show_page(index: int) -> None:
        run_buttons.clear()
        for widget in content.winfo_children():
            widget.destroy()
        for nav_index, button in enumerate(nav_buttons):
            button.configure(style="ActiveNav.TButton" if nav_index == index else "Nav.TButton")
        page = PAGES[index]
        title_label.configure(text=page.title)
        subtitle_label.configure(text=page.detail)
        for col in range(2):
            content.columnconfigure(col, weight=1)
        for action_index, action in enumerate(page.actions):
            row, col = divmod(action_index, 2)
            card = ttk.LabelFrame(content, text=action.title, padding=(14, 11))
            card.grid(row=row, column=col, sticky="nsew", padx=(0, 10) if col == 0 else (0, 0),
                      pady=(0, 10))
            ttk.Label(card, text=action.detail, style="Muted.TLabel", wraplength=350,
                      justify="left").pack(anchor="w", fill="x", pady=(0, 11))
            button = ttk.Button(card, text="Open" if action.command[:1] == ("gui",) or action.command == ("web",)
                                else "Run", style="Tool.TButton", command=lambda a=action: run_action(a))
            button.pack(anchor="e")
            run_buttons.append(button)

    style.configure("Nav.TButton", background=palette["sidebar"], foreground=palette["sidebar_text"],
                    anchor="w", padding=(10, 8), font=("Segoe UI", 10), borderwidth=0)
    style.map("Nav.TButton", background=[("active", palette["accent_hover"])])
    style.configure("ActiveNav.TButton", background=palette["accent"], foreground="#FFFFFF",
                    anchor="w", padding=(10, 8), font=("Segoe UI Semibold", 10), borderwidth=0)
    for index, page in enumerate(PAGES):
        button = ttk.Button(sidebar, text=page.title, style="Nav.TButton",
                            command=lambda i=index: show_page(i))
        button.pack(fill="x", pady=2)
        nav_buttons.append(button)

    def close_manager() -> None:
        if active_process is not None and active_process.poll() is None:
            if not messagebox.askyesno("Command running", "Close the manager and stop the active command?",
                                       parent=root):
                return
            active_process.terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_manager)
    show_page(0)
    poll_events()
    if run_mainloop:
        root.mainloop()
    return 0