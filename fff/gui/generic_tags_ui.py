"""Generic tags: go through the archive's tags and mark which ones are generic (or fetish).

Run as 'fff gui generic-tags' or from the FFF Manager. Tags are listed most-used first,
since those matter most. With the list focused: G marks the selection generic, N not
generic, F fetish, U clears it, Ctrl+Z undoes, Ctrl+A selects every listed tag. Fetish
marks feed the archive browser's Fetish trending list. Each mark is saved
to tag_classifications straight away. The side panel shows a MySQL query that leaves
the generic tags out, ready to copy.
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from fff.archive import generic_tags
from fff.archive.generic_tags import FETISH, GENERIC, MARKS, SPECIFIC
from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql
from fff.gui import host as tool_host, theme

logger = logging.getLogger(__name__)

MAX_ROWS = 1000  # Treeview gets sluggish beyond this; the search box narrows the list
ALL_TYPES = "All types"
SHOW_OPTIONS = {"To review": "todo", "Generic": GENERIC, "Not generic": SPECIFIC, "Fetish": FETISH,
                "All tags": "all"}
STATUS_TEXT = {None: "", GENERIC: "Generic", SPECIFIC: "Not generic", FETISH: "Fetish"}
ROW_TAG = {None: (), GENERIC: ("generic",), SPECIFIC: ("specific",), FETISH: ("fetish",)}


def _load():
    """Off the Tk thread: connect, make sure the table exists, read every tag."""
    conn = connect_to_mysql(load_config().get("mysql", {}))
    if conn is None:
        raise RuntimeError("Could not connect to the archive database")
    try:
        generic_tags.ensure_table(conn)
        return conn, generic_tags.load_tags(conn)
    except BaseException:
        conn.close()
        raise


class GenericTagsApp:
    def __init__(self, host, conn, tags):
        self.host, self.conn, self.tags = host, conn, tags
        self.by_id = {tag["tag_id"]: tag for tag in tags}
        self.undo_stack: list[list[tuple[int, str | None]]] = []
        self._search_job = None
        self._copied_job = None
        p = theme.PALETTE

        outer = ttk.Frame(host, padding=(18, 14))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="Generic tags", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(head, text="Mark tags that are on so many posts they say little (mammal, canine, ...), and "
                             "fetish tags for the browser's Fetish trending list. Most-used tags come first; "
                             "each mark is saved straight away.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.progress_var = tk.StringVar()
        ttk.Label(head, textvariable=self.progress_var).pack(anchor="w", pady=(6, 0))

        filters = ttk.Frame(outer)
        filters.grid(row=1, column=0, sticky="ew", pady=(12, 8))
        ttk.Label(filters, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        search = ttk.Entry(filters, textvariable=self.search_var, width=32)
        search.pack(side="left", padx=(6, 16))
        self.search_var.trace_add("write", lambda *_: self._search_soon())
        search.bind("<Down>", lambda _e: self._focus_list())
        ttk.Label(filters, text="Type").pack(side="left")
        types = sorted({tag["type"] for tag in tags}, key=str.lower)
        self.type_var = tk.StringVar(value=ALL_TYPES)
        type_box = ttk.Combobox(filters, textvariable=self.type_var, values=[ALL_TYPES, *types],
                                state="readonly", width=14)
        type_box.pack(side="left", padx=(6, 16))
        type_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(filters, text="Show").pack(side="left")
        self.show_var = tk.StringVar(value="To review")
        show_box = ttk.Combobox(filters, textvariable=self.show_var, values=list(SHOW_OPTIONS),
                                state="readonly", width=12)
        show_box.pack(side="left", padx=(6, 0))
        show_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        panes = ttk.PanedWindow(outer, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")

        # --- tag list --------------------------------------------------------------
        left = ttk.Frame(panes)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(left, columns=("name", "type", "posts", "status"), show="headings",
                                 selectmode="extended")
        for column, text, width, anchor, stretch in (("name", "Tag", 320, "w", True),
                                                     ("type", "Type", 100, "w", False),
                                                     ("posts", "Posts", 90, "e", False),
                                                     ("status", "Marked", 100, "w", False)):
            self.tree.heading(column, text=text, anchor=anchor)
            self.tree.column(column, width=width, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("generic", background=p["warning_soft"])
        self.tree.tag_configure("specific", background=p["ok_soft"])
        self.tree.tag_configure("fetish", background=p["violet_soft"])
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.list_note = tk.StringVar()
        ttk.Label(left, textvariable=self.list_note, style="Muted.TLabel").grid(row=1, column=0, sticky="w",
                                                                              pady=(6, 0))
        # Keys go on the list itself so typing in the search box never marks anything.
        for keys, value in ((("g", "G"), GENERIC), (("n", "N"), SPECIFIC), (("f", "F"), FETISH),
                            (("u", "U", "<BackSpace>", "<Delete>"), None)):
            for key in keys:
                self.tree.bind(key, lambda _e, v=value: self.mark(v) or "break")
        self.tree.bind("<Control-z>", lambda _e: self.undo() or "break")
        self.tree.bind("<Control-a>", lambda _e: self.tree.selection_set(self.tree.get_children()) or "break")
        panes.add(left, weight=3)

        # --- actions and query -------------------------------------------------------
        right = ttk.Frame(panes, padding=(16, 0, 0, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(6, weight=1)
        ttk.Label(right, text="Mark selected tags", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(right)
        buttons.grid(row=1, column=0, sticky="w", pady=(6, 0))
        for column, (text, command, style) in enumerate((
                ("Generic  (G)", lambda: self.mark(GENERIC), "Primary.TButton"),
                ("Not generic  (N)", lambda: self.mark(SPECIFIC), "TButton"),
                ("Fetish  (F)", lambda: self.mark(FETISH), "TButton"),
                ("Clear  (U)", lambda: self.mark(None), "Secondary.TButton"),
                ("Undo  (Ctrl+Z)", self.undo, "Secondary.TButton"))):
            ttk.Button(buttons, text=text, command=command, style=style).grid(row=0, column=column, padx=(0, 6))
        ttk.Label(right, text="Keys work while the list has focus; marking moves on to the next tag. "
                              "Shift/Ctrl-click or Ctrl+A to mark many at once.",
                  style="Muted.TLabel", wraplength=420, justify="left").grid(row=2, column=0, sticky="w", pady=(6, 0))

        ttk.Separator(right).grid(row=3, column=0, sticky="ew", pady=14)
        ttk.Label(right, text="Filter query", style="Section.TLabel").grid(row=4, column=0, sticky="w")
        options = ttk.Frame(right)
        options.grid(row=5, column=0, sticky="w", pady=(4, 6))
        self.inline_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(options, text="Read tag_classifications (stays current)", variable=self.inline_var,
                        value=False, command=self.update_query).pack(anchor="w")
        ttk.Radiobutton(options, text="List the generic tags in the query", variable=self.inline_var,
                        value=True, command=self.update_query).pack(anchor="w")
        self.skip_types_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Also leave out every General and Meta tag", variable=self.skip_types_var,
                        command=self.update_query).pack(anchor="w", pady=(4, 0))

        query_frame = ttk.Frame(right)
        query_frame.grid(row=6, column=0, sticky="nsew")
        query_frame.columnconfigure(0, weight=1)
        query_frame.rowconfigure(0, weight=1)
        self.query_text = tk.Text(query_frame, wrap="none", height=14, font=theme.mono(9),
                                  background=p["surface"], foreground=p["ink"], insertbackground=p["ink"],
                                  relief="flat", highlightthickness=1, highlightbackground=p["line"],
                                  padx=8, pady=6)
        self.query_text.grid(row=0, column=0, sticky="nsew")
        qscroll = ttk.Scrollbar(query_frame, orient="vertical", command=self.query_text.yview)
        qscroll.grid(row=0, column=1, sticky="ns")
        self.query_text.configure(yscrollcommand=qscroll.set)
        copy_row = ttk.Frame(right)
        copy_row.grid(row=7, column=0, sticky="w", pady=(8, 0))
        ttk.Button(copy_row, text="Copy query", style="Primary.TButton", command=self.copy_query).pack(side="left")
        self.copied_var = tk.StringVar()
        ttk.Label(copy_row, textvariable=self.copied_var, style="Ok.TLabel").pack(side="left", padx=(10, 0))
        panes.add(right, weight=2)

        self.refresh()
        self._focus_list()

    # --- list --------------------------------------------------------------------------
    def _filters(self) -> dict:
        kind = self.type_var.get()
        return {"kind": "" if kind == ALL_TYPES else kind, "status": SHOW_OPTIONS[self.show_var.get()],
                "search": self.search_var.get()}

    def _search_soon(self):
        if self._search_job:
            self.host.after_cancel(self._search_job)
        self._search_job = self.host.after(200, self.refresh)

    def _row_values(self, tag):
        return (tag["name"], tag["type"], f"{tag['posts']:,}", STATUS_TEXT[tag["mark"]])

    def refresh(self):
        """Rebuild the list from the filters."""
        self._search_job = None
        filters = self._filters()
        matching = [tag for tag in self.tags if generic_tags.matches(tag, **filters)]
        self.tree.delete(*self.tree.get_children())
        for tag in matching[:MAX_ROWS]:
            self.tree.insert("", "end", iid=str(tag["tag_id"]), values=self._row_values(tag),
                             tags=ROW_TAG[tag["mark"]])
        if len(matching) > MAX_ROWS:
            self.list_note.set(f"Showing the {MAX_ROWS:,} most-used of {len(matching):,} matching tags. "
                               "Search to narrow the list.")
        else:
            self.list_note.set(f"{len(matching):,} matching tag{'s' if len(matching) != 1 else ''}.")
        children = self.tree.get_children()
        if children:
            self._select(children[0])
        self._update_counts()

    def _select(self, iid):
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)

    def _focus_list(self):
        self.tree.focus_set()
        if not self.tree.focus() and self.tree.get_children():
            self._select(self.tree.get_children()[0])
        return "break"

    def _update_rows(self, tag_ids):
        """Show changed tags in place; drop the ones the filters no longer match."""
        filters = self._filters()
        for tag_id in tag_ids:
            iid = str(tag_id)
            if not self.tree.exists(iid):
                continue
            tag = self.by_id[tag_id]
            if generic_tags.matches(tag, **filters):
                self.tree.item(iid, values=self._row_values(tag), tags=ROW_TAG[tag["mark"]])
            else:
                self.tree.delete(iid)
        self._update_counts()

    def _update_counts(self):
        counts = {mark: 0 for mark in MARKS}
        for tag in self.tags:
            if tag["mark"]:
                counts[tag["mark"]] += 1
        self.progress_var.set(f"{sum(counts.values()):,} of {len(self.tags):,} tags reviewed  ·  "
                              f"{counts[GENERIC]:,} generic  ·  {counts[SPECIFIC]:,} not generic  ·  "
                              f"{counts[FETISH]:,} fetish")
        self.update_query()

    # --- marking -----------------------------------------------------------------------
    def _save(self, tag_ids, mark) -> bool:
        try:
            if hasattr(self.conn, "ping"):
                self.conn.ping(reconnect=True, attempts=2, delay=1)
            generic_tags.set_mark(self.conn, tag_ids, mark)
        except Exception as exc:
            logger.exception("Saving tag classifications failed")
            messagebox.showerror("Could not save", f"The change was not saved:\n{exc}", parent=self.host)
            return False
        for tag_id in tag_ids:
            self.by_id[tag_id]["mark"] = mark
        return True

    def mark(self, mark):
        selection = self.tree.selection()
        if not selection:
            return
        children = self.tree.get_children()
        positions = sorted(children.index(iid) for iid in selection)
        tag_ids = [int(iid) for iid in selection]
        changed = [(tag_id, self.by_id[tag_id]["mark"]) for tag_id in tag_ids
                   if self.by_id[tag_id]["mark"] != mark]
        if changed:
            if not self._save([tag_id for tag_id, _ in changed], mark):
                return
            self.undo_stack.append(changed)
            self._update_rows(tag_ids)
        # Move on: rows that stayed are skipped; removed rows let the next one slide up.
        children = self.tree.get_children()
        if not children:
            return
        stayed = self.tree.exists(selection[-1])
        target = positions[-1] + 1 if stayed else positions[0]
        self._select(children[min(target, len(children) - 1)])

    def undo(self):
        if not self.undo_stack:
            return
        batch = self.undo_stack.pop()
        for previous in (None, *MARKS):
            ids = [tag_id for tag_id, old in batch if old == previous]
            if ids and not self._save(ids, previous):
                self.undo_stack.append(batch)
                return
        ids = [tag_id for tag_id, _ in batch]
        # Undone tags may belong back in a filtered list (e.g. "To review"), so rebuild it.
        self.refresh()
        visible = [str(tag_id) for tag_id in ids if self.tree.exists(str(tag_id))]
        if visible:
            self.tree.selection_set(visible)
            self.tree.focus(visible[0])
            self.tree.see(visible[0])

    # --- query -------------------------------------------------------------------------
    def query(self) -> str:
        inline = [tag["name"] for tag in self.tags if tag["mark"] == GENERIC] if self.inline_var.get() else None
        return generic_tags.filter_sql(inline_names=inline, skip_generic_types=self.skip_types_var.get())

    def update_query(self):
        self.query_text.configure(state="normal")
        self.query_text.delete("1.0", "end")
        self.query_text.insert("1.0", self.query())
        self.query_text.configure(state="disabled")

    def copy_query(self):
        self.host.clipboard_clear()
        self.host.clipboard_append(self.query())
        self.copied_var.set("Copied")
        if self._copied_job:
            self.host.after_cancel(self._copied_job)
        self._copied_job = self.host.after(2500, lambda: self.copied_var.set(""))


def build(host):
    """Load the tags in the background, then show the tool inside ``host`` (ToolHost or Tk root)."""
    loading = tool_host.message_panel(host, "Loading tags", "Counting how often each tag is used...", busy=True)

    def close():
        tool_host.close(host)

    def failed(exc):
        loading.destroy()
        logger.error("Generic tags could not load: %s", exc)
        tool_host.message_panel(host, "Generic tags could not start", str(exc), tone="warning",
                                actions=(("Close", close),))

    def ready(result):
        loading.destroy()
        conn, tags = result
        tool_host.on_destroy(host, conn.close)
        if not tags:
            tool_host.message_panel(host, "No tags yet", "The archive has no tags to review. Sync some posts first.",
                                    actions=(("Close", close),))
            return
        host._generic_tags_app = GenericTagsApp(host, conn, tags)  # keep a reference for the tool's lifetime

    tool_host.run_in_background(host, _load, ready, failed)


def main(argv=None):  # argv is ignored (no options); lets the CLI call it uniformly
    root = tk.Tk()
    theme.apply(root)
    root.title("Generic Tags")
    root.geometry("1280x820")
    build(root)
    root.mainloop()
    try:
        root.destroy()  # closes the connection (on_destroy)
    except tk.TclError:
        pass


if __name__ == "__main__":
    main()
