"""The guided first-time setup wizard.

Opened by 'fff setup' (and setup.bat) while setup isn't finished, by 'fff setup --wizard' at
any time, and by the FFF Manager in place of its pages until setup is finished. Each step
has a check that must pass before Next is enabled; the logic lives in fff.common.first_run.

Steps: Welcome (pick features) -> Database (connect, create tables) -> e621 account
(signed in online) -> Folders (four roots, create the missing ones) -> Extras (FuzzySearch
key, FurAffinity cookie consent; only for the features picked) -> Final checks.

Settings are saved to config.yaml/secrets.yaml at every Next, so closing the window keeps
the progress; the next start resumes at the same step.
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml

from fff.common import consent, first_run
from fff.common import config as fff_config
from fff.gui import host as tool_host
from fff.gui import setup_ui, theme

STEPS = (
    ("welcome", "Welcome"),
    ("database", "Database"),
    ("e621", "e621 account"),
    ("folders", "Folders"),
    ("extras", "Extras"),
    ("finish", "Final checks"),
)
INTROS = {
    "welcome": "This sets up everything FFF needs: a MySQL database, your e621 account and the folders it "
               "works in. It takes a few minutes. FFF can't be used until it's done; you can stop at any "
               "point and pick up where you left off, and change anything later in Settings.",
    "database": "FFF keeps your archive, tags and file hashes in MySQL. Enter the server you installed and "
                "an account that may create databases, test the connection, then let FFF create its "
                "databases and tables.",
    "e621": "FFF reads your favorites and tags through the e621 API, signed in as you.",
    "folders": "Pick four places and FFF lays out its folders under them (you can change any single "
               "folder below). Then create the ones that don't exist yet.",
    "extras": "Settings for the optional features you picked.",
    "finish": "Last, FFF checks everything together. When every line is ticked, finish setup.",
}
WRAP = 680


def _get(doc: dict, path: tuple[str, ...]):
    for key in path:
        doc = doc.get(key) if isinstance(doc, dict) else None
    return doc


class SetupWizard:
    def __init__(self, parent, *, config_path: str | Path | None = None, on_finish=None):
        self.parent = parent
        self.on_finish = on_finish or (lambda: tool_host.close(parent))
        self.config_path, self.secrets_path, self.config, self.secrets = setup_ui.load_documents(config_path)
        self.state = first_run.begin(config_path)
        self.features = {**first_run.DEFAULT_FEATURES, **(self.state.get("features") or {})}
        self.done = set(self.state.get("done", []))
        self.passed: dict[str, bool] = {}       # checks passed this session for the current values
        self.root_values: dict[str, str] = dict(self.state.get("roots") or {})  # example root -> chosen
        self.busy = False
        self.fa_consent = tk.BooleanVar(value=consent.has_consent(consent.BROWSER_COOKIES))
        self.feature_vars = {key: tk.BooleanVar(value=value) for key, value in self.features.items()}
        p = theme.PALETTE

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(outer, style="Sidebar.TFrame", padding=(20, 22, 26, 22))
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="First-time setup", style="SidebarTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="FurryFileFinagler", style="SidebarHint.TLabel").pack(anchor="w", pady=(0, 18))
        self.step_labels = {}
        for key, title in STEPS:
            label = tk.Label(sidebar, text=title, anchor="w", background=p["sidebar"],
                             foreground=p["sidebar_hint"], font=theme.font(11), padx=0, pady=5)
            label.pack(anchor="w", fill="x")
            self.step_labels[key] = label

        main = ttk.Frame(outer, padding=(30, 24, 30, 16))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self.title = ttk.Label(main, style="Title.TLabel")
        self.title.grid(row=0, column=0, sticky="w")
        self.intro = ttk.Label(main, style="Muted.TLabel", wraplength=WRAP, justify="left")
        self.intro.grid(row=1, column=0, sticky="nw", pady=(4, 16))
        self.main = main
        self.body = ttk.Frame(main)  # replaced on every step, so no step inherits another's grid weights
        self.body.grid(row=2, column=0, sticky="nsew")

        footer = ttk.Frame(main)
        footer.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(1, weight=1)
        self.back_button = ttk.Button(footer, text="← Back", style="Secondary.TButton", command=self.back)
        self.back_button.grid(row=0, column=0)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=160)
        self.next_button = ttk.Button(footer, text="Next →", style="Primary.TButton", command=self.next)
        self.next_button.grid(row=0, column=2)

        keys = [key for key, _ in STEPS]
        resume = self.state.get("step")
        self.index = keys.index(resume) if resume in keys else 0
        self.show(self.index)

    # ------------------------------------------------------------------ framework
    def key(self, index=None) -> str:
        return STEPS[self.index if index is None else index][0]

    def skipped(self, key: str) -> bool:
        return key == "extras" and not (self.features.get("reverse_search") or self.features.get("furaffinity"))

    def satisfied(self, key: str) -> bool:
        if key == "welcome":
            return True
        if key in ("database", "e621"):
            return self.passed.get(key, key in self.done)
        if key == "folders":
            return all(row["exists"] for row in first_run.folders(self.cfg()))
        if key == "extras":
            return not self.features.get("reverse_search") or not first_run.is_placeholder(
                _get(self.secrets, ("fuzzysearch", "api_key")))
        return self.passed.get("finish", False)

    def cfg(self) -> dict:
        return fff_config.deep_merge(self.config, self.secrets)

    def changed(self, key: str) -> None:
        """A value on step ``key`` changed: its check has to pass again."""
        self.passed[key] = False
        self.done.discard(key)
        self.passed["finish"] = False
        self.refresh_nav()

    def show(self, index: int) -> None:
        self.index = index
        key = self.key()
        self.body.destroy()
        self.body = ttk.Frame(self.main)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.title.configure(text=dict(STEPS)[key])
        self.intro.configure(text=INTROS[key])
        getattr(self, f"build_{key}")(self.body)
        self.refresh_nav()

    def refresh_nav(self) -> None:
        p = theme.PALETTE
        for i, (key, title) in enumerate(STEPS):
            if i == self.index:
                text, color, font = f"▸  {title}", p["sidebar_text"], theme.font(11, "bold")
            elif self.skipped(key):
                text, color, font = f"–  {title} (not needed)", p["sidebar_hint"], theme.font(11)
            elif key in self.done:
                text, color, font = f"✓  {title}", p["sidebar_text"], theme.font(11)
            else:
                text, color, font = f"    {title}", p["sidebar_hint"], theme.font(11)
            self.step_labels[key].configure(text=text, foreground=color, font=font)
        self.back_button.configure(state="normal" if self.index > 0 and not self.busy else "disabled")
        last = self.key() == "finish"
        self.next_button.configure(text="Finish setup" if last else "Next →",
                                   state="normal" if self.satisfied(self.key()) and not self.busy else "disabled")

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.progress.grid(row=0, column=1, sticky="e", padx=12)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.grid_remove()
        self.refresh_nav()

    def run(self, work, done, status: ttk.Label | None = None) -> None:
        """Run ``work`` off the Tk thread; ``done(result)`` back on it. Errors go to ``status``."""
        self.set_busy(True)

        def ok(result):
            self.set_busy(False)
            done(result)

        def failed(exc):
            self.set_busy(False)
            if status is not None and status.winfo_exists():
                self.say(status, False, f"Something went wrong: {exc}")

        tool_host.run_in_background(self.parent, work, ok, failed)

    @staticmethod
    def say(label: ttk.Label, ok: bool | None, text: str) -> None:
        style = "Muted.TLabel" if ok is None else "Ok.TLabel" if ok else "Error.TLabel"
        label.configure(text=text, style=style)

    def save(self) -> bool:
        try:
            setup_ui.save_documents(self.config_path, self.secrets_path, self.config, self.secrets)
            fff_config.load(self.config_path, self.secrets_path, reload=True)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            messagebox.showerror("Setup", f"Could not save the settings:\n{exc}", parent=self.parent)
            return False
        return True

    def next(self) -> None:
        key = self.key()
        if self.busy or not self.satisfied(key) or not self.save():
            return
        if key == "welcome":
            self.features = {name: var.get() for name, var in self.feature_vars.items()}
        if key == "extras" and self.features.get("furaffinity"):
            consent.set_consent(consent.BROWSER_COOKIES, self.fa_consent.get())
        self.done.add(key)
        if key == "finish":
            first_run.record(self.state, key, features=self.features, roots=self.root_values)
            first_run.finish(self.state)
            self.on_finish()
            return
        following = next(i for i in range(self.index + 1, len(STEPS)) if not self.skipped(self.key(i)))
        first_run.record(self.state, key, features=self.features, roots=self.root_values, step=self.key(following))
        self.show(following)

    def back(self) -> None:
        if self.busy or self.index == 0:
            return
        self.show(next(i for i in range(self.index - 1, -1, -1) if not self.skipped(self.key(i))))

    # ------------------------------------------------------------------ widgets
    def field(self, parent, row: int, label: str, doc: dict, path: tuple[str, ...], step: str, *,
              secret=False, hint: str | None = None, number=False, on_change=None) -> tk.StringVar:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=4)
        value = _get(doc, path)
        var = tk.StringVar(value="" if value is None else str(value))
        entry = ttk.Entry(parent, textvariable=var, width=46, show="•" if secret else "")
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        if secret:
            shown = tk.BooleanVar(value=False)
            ttk.Checkbutton(parent, text="Show", variable=shown,
                            command=lambda: entry.configure(show="" if shown.get() else "•")).grid(
                row=row, column=2, sticky="w", padx=(8, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel", wraplength=WRAP - 160, justify="left").grid(
                row=row + 1, column=1, columnspan=2, sticky="w", pady=(0, 6))

        def write(*_):
            raw = var.get().strip()
            new = int(raw) if number and raw.isdigit() else raw
            if _get(doc, path) == new:
                return
            setup_ui._set_path(doc, path, new)
            if on_change:
                on_change(raw)
            self.changed(step)

        var.trace_add("write", write)
        return var

    def status_label(self, parent, row: int) -> ttk.Label:
        label = ttk.Label(parent, style="Muted.TLabel", wraplength=WRAP, justify="left")
        label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        return label

    # ------------------------------------------------------------------ steps
    def build_welcome(self, body) -> None:
        ttk.Label(body, text="What will you use FFF for?", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Checkbutton(body, text="Archive my e621 favorites (always on: everything else builds on it)",
                        state="disabled", variable=tk.BooleanVar(value=True)).grid(row=1, column=0, sticky="w")
        row = 2
        for key, (title, detail) in first_run.FEATURES.items():
            ttk.Checkbutton(body, text=title, variable=self.feature_vars[key],
                            command=lambda: (self.features.update(
                                {name: var.get() for name, var in self.feature_vars.items()}), self.refresh_nav())
                            ).grid(row=row, column=0, sticky="w", pady=(8, 0))
            ttk.Label(body, text=detail, style="Muted.TLabel", wraplength=WRAP - 30, justify="left").grid(
                row=row + 1, column=0, sticky="w", padx=(26, 0))
            row += 2
        ttk.Label(body, text="You can change these later; they only decide which setup steps apply.",
                  style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=(16, 0))

    def build_database(self, body) -> None:
        body.columnconfigure(1, weight=1)
        self.connected = self.passed.get("database", False)
        status = None

        def reset(_raw=None):
            self.connected = False
            create.configure(state="disabled")
            if status is not None:
                self.say(status, None, "Settings changed: test the connection again.")

        c, s = self.config, self.secrets
        self.field(body, 0, "Server", c, ("database", "host"), "database", hint="Usually localhost.", on_change=reset)
        self.field(body, 2, "Port", c, ("database", "port"), "database", number=True, on_change=reset)
        self.field(body, 3, "User", c, ("database", "user"), "database", on_change=reset)
        self.field(body, 4, "Password", s, ("database", "password"), "database", secret=True, on_change=reset)
        ttk.Label(body, text="Database names", style="Section.TLabel").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(14, 2))
        for row, (module, label) in enumerate((("archive", "e621 archive"), ("sorter", "Library hashes"),
                                               ("fluffle", "Reverse search")), start=6):
            self.field(body, row, label, c, ("databases", module), "database", on_change=reset)
        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=3, sticky="w", pady=(14, 0))
        test = ttk.Button(buttons, text="Test connection", style="Secondary.TButton")
        test.pack(side="left")
        create = ttk.Button(buttons, text="Create databases and tables", style="Primary.TButton",
                            state="normal" if self.connected else "disabled")
        create.pack(side="left", padx=8)
        status = self.status_label(body, 10)
        if self.satisfied("database"):
            self.say(status, True, "Database ready. Change anything above to check it again.")

        def tested(result):
            ok, message = result
            self.connected = ok
            create.configure(state="normal" if ok else "disabled")
            self.say(status, ok, message + (" Now create the databases and tables." if ok else ""))

        def created(result):
            made, problems = result
            if problems:
                self.passed["database"] = False
                self.say(status, False, "Tables are still missing: " + "; ".join(problems))
            else:
                count = sum(len(tables) for tables in made.values())
                self.passed["database"] = True
                self.say(status, True, f"Created {count} tables. The database is ready." if count
                         else "Every database and table was already there. The database is ready.")
            self.refresh_nav()

        test.configure(command=lambda: self.run(lambda: first_run.check_server(self.cfg()), tested, status))
        create.configure(command=lambda: self.save() and self.run(
            lambda: first_run.prepare_databases(self.config_path, self.secrets_path), created, status))

    def build_e621(self, body) -> None:
        body.columnconfigure(1, weight=1)
        c, s = self.config, self.secrets
        login_now = _get(s, ("e621", "login")) or ""
        follows = {  # fields that copy the username until edited by hand
            "favorites": first_run.is_placeholder(_get(c, ("archive", "e621", "favorites_username")))
            or _get(c, ("archive", "e621", "favorites_username")) == login_now,
            "agent": first_run.is_placeholder(_get(c, ("e621", "user_agent")))
            or _get(c, ("e621", "user_agent")) == first_run.default_user_agent(login_now),
        }
        holder = {}

        def login_changed(raw):
            if first_run.is_placeholder(raw):
                return
            if follows["favorites"]:
                holder["favorites"].set(raw)
            if follows["agent"]:
                holder["agent"].set(first_run.default_user_agent(raw))

        self.field(body, 0, "Username", s, ("e621", "login"), "e621", on_change=login_changed)
        self.field(body, 1, "API key", s, ("e621", "api_key"), "e621", secret=True,
                   hint="On e621: Account › Settings › Manage API access. Create a key and paste it here.")
        holder["favorites"] = self.field(
            body, 3, "Archive favorites of", c, ("archive", "e621", "favorites_username"), "e621",
            hint="Whose favorites FFF keeps. Usually you.",
            on_change=lambda raw: follows.update(favorites=raw == (_get(s, ("e621", "login")) or "")))
        holder["agent"] = self.field(
            body, 5, "User agent", c, ("e621", "user_agent"), "e621",
            hint="e621 asks every tool to say who runs it. The suggested one is fine.",
            on_change=lambda raw: follows.update(
                agent=raw == first_run.default_user_agent(_get(s, ("e621", "login")) or "")))
        if not first_run.is_placeholder(login_now):  # never build values from the example username
            if follows["favorites"]:
                holder["favorites"].set(login_now)
            if follows["agent"]:
                holder["agent"].set(first_run.default_user_agent(login_now))
        check = ttk.Button(body, text="Check account", style="Primary.TButton")
        check.grid(row=7, column=0, columnspan=3, sticky="w", pady=(14, 0))
        status = self.status_label(body, 8)
        if self.satisfied("e621"):
            self.say(status, True, "Account checked. Change anything above to check it again.")

        def checked(result):
            ok, message = result
            self.passed["e621"] = ok
            self.say(status, ok, message)
            self.refresh_nav()

        check.configure(command=lambda: self.run(lambda: first_run.check_e621(
            _get(s, ("e621", "login")) or "", _get(s, ("e621", "api_key")) or "",
            _get(c, ("e621", "user_agent")) or ""), checked, status))

    def build_folders(self, body) -> None:
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        if not self.root_values:
            self.root_values = {root: root for root, _label, _hint in first_run.roots_in_use(self.cfg())}
        roots = ttk.Frame(body)
        roots.grid(row=0, column=0, sticky="ew")
        roots.columnconfigure(1, weight=1)
        labels = {root: (label, hint) for root, label, hint in first_run.ROOTS}
        for row, (example, current) in enumerate(self.root_values.items()):
            label, hint = labels.get(example, (example, ""))
            ttk.Label(roots, text=label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=3)
            var = tk.StringVar(value=current)
            entry = ttk.Entry(roots, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            ttk.Button(roots, text="Browse…", style="Secondary.TButton",
                       command=lambda e=example, v=var: self.browse_root(e, v)).grid(
                row=row, column=2, padx=(8, 0))
            ttk.Label(roots, text=hint, style="Muted.TLabel").grid(row=row, column=3, sticky="w", padx=(10, 0))
            entry.bind("<Return>", lambda _e, e=example, v=var: self.move_root(e, v.get()))
            entry.bind("<FocusOut>", lambda _e, e=example, v=var: self.move_root(e, v.get()))

        table = ttk.Frame(body)
        table.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.folder_tree = ttk.Treeview(table, columns=("path", "status", "used"), show="headings", height=11)
        for column, text, width, stretch in (("path", "Folder", 520, True), ("status", "Status", 90, False),
                                             ("used", "Used by", 230, False)):
            self.folder_tree.heading(column, text=text, anchor="w")
            self.folder_tree.column(column, width=width, stretch=stretch, anchor="w")
        self.folder_tree.tag_configure("missing", foreground=theme.PALETTE["warning"])
        self.folder_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.folder_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.folder_tree.configure(yscrollcommand=scroll.set)
        self.folder_tree.bind("<Double-1>", lambda _e: self.change_folder())

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Create missing folders", style="Primary.TButton",
                   command=self.create_missing).pack(side="left")
        ttk.Button(buttons, text="Change selected folder…", style="Secondary.TButton",
                   command=self.change_folder).pack(side="left", padx=8)
        self.folder_status = self.status_label(body, 3)
        self.fill_folders()

    def fill_folders(self) -> None:
        tree = self.folder_tree
        tree.delete(*tree.get_children())
        rows = first_run.folders(self.cfg())
        for row in rows:
            # 'archive.download.images.safe' -> 'images › safe' (the full setting is in the tooltip-less
            # settings editor; two parts are enough to tell the rating/media folders apart)
            used = " › ".join(row["settings"][0].split(".")[-2:]).replace("_", " ")
            if len(row["settings"]) > 1:
                used += f" +{len(row['settings']) - 1} more"
            tree.insert("", "end", iid=row["path"],
                        values=(first_run.short_path(row["path"], 72), "exists" if row["exists"] else "missing", used),
                        tags=() if row["exists"] else ("missing",))
        missing = sum(not row["exists"] for row in rows)
        self.say(self.folder_status, not missing,
                 f"All {len(rows)} folders exist." if not missing
                 else f"{missing} of {len(rows)} folders don't exist yet. Create them, or change where they go.")
        self.refresh_nav()

    def browse_root(self, example: str, var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(parent=self.parent, title="Choose a folder")
        if chosen:
            var.set(chosen)
            self.move_root(example, chosen)

    def move_root(self, example: str, new: str) -> None:
        new = new.strip()
        current = self.root_values.get(example, example)
        if not new or first_run._norm(new).lower() == first_run._norm(current).lower():
            return
        self.config = first_run.move_root(self.config, current, new)
        self.root_values[example] = first_run._norm(new)
        self.fill_folders()

    def change_folder(self) -> None:
        selected = self.folder_tree.selection()
        if not selected:
            self.say(self.folder_status, None, "Select a folder in the list first.")
            return
        old = selected[0]
        chosen = filedialog.askdirectory(parent=self.parent, title=f"Replace {old}", mustexist=False)
        if chosen:
            self.config = first_run.replace_folder(self.config, old, chosen)
            self.fill_folders()

    def create_missing(self) -> None:
        missing = [row["path"] for row in first_run.folders(self.cfg()) if not row["exists"]]
        errors = first_run.create_folders(missing)
        self.fill_folders()
        if errors:
            self.say(self.folder_status, False, "Could not create: " + "; ".join(errors[:3]))

    def build_extras(self, body) -> None:
        body.columnconfigure(1, weight=1)
        row = 0
        if self.features.get("reverse_search"):
            ttk.Label(body, text="FuzzySearch", style="Section.TLabel").grid(row=row, column=0, columnspan=3,
                                                                             sticky="w", pady=(0, 4))
            self.field(body, row + 1, "API key", self.secrets, ("fuzzysearch", "api_key"), "extras", secret=True,
                       hint="Reverse image search uses FuzzySearch; request an API key on fuzzysearch.net.")
            row += 3
        if self.features.get("furaffinity"):
            ttk.Label(body, text="FurAffinity", style="Section.TLabel").grid(row=row, column=0, columnspan=3,
                                                                             sticky="w", pady=(10, 4))
            ttk.Label(body, text=consent.PROMPTS[consent.BROWSER_COOKIES].replace("\n", " "), wraplength=WRAP,
                      justify="left").grid(row=row + 1, column=0, columnspan=3, sticky="w")
            ttk.Checkbutton(body, text="Allow FFF to read my FurAffinity cookies", variable=self.fa_consent).grid(
                row=row + 2, column=0, columnspan=3, sticky="w", pady=(8, 0))
            ttk.Label(body, text="Without this, FurAffinity steps are skipped. You can allow it later.",
                      style="Muted.TLabel").grid(row=row + 3, column=0, columnspan=3, sticky="w")

    def build_finish(self, body) -> None:
        body.columnconfigure(1, weight=1)
        self.check_rows = ttk.Frame(body)
        self.check_rows.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.check_rows.columnconfigure(2, weight=1)
        again = ttk.Button(body, text="Run checks again", style="Secondary.TButton", command=self.run_final_checks)
        again.grid(row=1, column=0, sticky="w", pady=(14, 0))
        self.finish_status = self.status_label(body, 2)
        self.run_final_checks()

    def run_final_checks(self) -> None:
        if self.busy or not self.save():
            return
        self.passed["finish"] = False
        for child in self.check_rows.winfo_children():
            child.destroy()
        self.say(self.finish_status, None, "Checking…")

        def show(results):
            for row, (title, ok, detail) in enumerate(results):
                ttk.Label(self.check_rows, text="✓" if ok else "✗", style="Ok.TLabel" if ok else "Error.TLabel",
                          font=theme.font(12, "bold")).grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=3)
                ttk.Label(self.check_rows, text=title).grid(row=row, column=1, sticky="nw", padx=(0, 16), pady=3)
                ttk.Label(self.check_rows, text=detail, style="Muted.TLabel", wraplength=WRAP - 200,
                          justify="left").grid(row=row, column=2, sticky="nw", pady=3)
            self.passed["finish"] = all(ok for _title, ok, _detail in results)
            self.say(self.finish_status, self.passed["finish"],
                     "Everything is ready. Finish setup to start using FFF." if self.passed["finish"]
                     else "Fix the lines marked ✗ (Back takes you to their step), then run the checks again.")
            self.refresh_nav()

        self.run(lambda: first_run.final_checks(self.config, self.secrets, self.features), show, self.finish_status)


def build(host, config_path: str | Path | None = None, on_finish=None) -> SetupWizard:
    return SetupWizard(host, config_path=config_path, on_finish=on_finish)


def main(config_path: str | Path | None = None) -> int:
    """Standalone window; 0 once setup is finished, 1 if it was closed before that."""
    root = tk.Tk()
    theme.apply(root)
    root.title("FurryFileFinagler – first-time setup")
    root.geometry("1180x800")
    root.minsize(980, 680)
    try:
        SetupWizard(root, config_path=config_path, on_finish=root.destroy)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        root.destroy()
        print(f"Could not load the setup configuration: {exc}")
        return 2
    root.mainloop()
    return 0 if first_run.is_complete(config_path) else 1
