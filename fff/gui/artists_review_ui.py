#!/usr/bin/env python3
# fff: run as 'fff gui artist-review'; main(argv=None) ignores argv (the original had no options).
# fff: reads config/review_artists.yaml (was config/artists.yaml); logs go to data/logs/archive (was ./logs).
"""
Artist Review UI

Interactive tool to review posts by favorite artists defined in `artists.yaml`
(fff: config/review_artists.yaml).

Flow:
- Load `artists.yaml` mapping (DisplayName -> [aliases])
- Present a selection panel to choose one artist (display names are shown)
- Build SQL LIKE patterns for aliases and fetch posts that have matching artist names in the DB
- Filter posts without accessible images and previously "kept" posts
- Launch the existing `BlacklistReviewUI` to review the posts (re-using UI from `blacklist_review_ui.py`)

"""

import os
import logging
import tkinter as tk
from tkinter import ttk, messagebox
import yaml

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql
from fff.archive.logging_setup import setup_logging
from fff.gui import host as tool_host, theme

# Import utilities and UI from blacklist_review_ui
from fff.gui.blacklist_review_ui import (
    BlacklistReviewUI,
    load_kept_posts,
    filter_kept_posts,
    filter_posts_with_accessible_images,
    load_review_keybindings,
)


def load_artists_yaml(path="artists.yaml"):
    if not os.path.exists(path):
        logging.error("artists.yaml not found: %s", path)
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        # Normalize: ensure each value is a list of aliases
        normalized = {}
        for display_name, aliases in data.items():
            if aliases is None:
                normalized[display_name] = [display_name]
            elif isinstance(aliases, (list, tuple)):
                normalized[display_name] = [str(a).strip() for a in aliases if a]
            else:
                normalized[display_name] = [str(aliases).strip()]
        return normalized
    except Exception as e:
        logging.error("Failed to read artists.yaml: %s", e)
        return None


class ArtistPicker(ttk.Frame):
    """Search-and-pick list of artists; calls ``on_choose(display_name)`` or ``on_cancel()``."""

    def __init__(self, parent, artists_map, on_choose, on_cancel):
        super().__init__(parent, padding=(18, 14))
        self.artists_map = artists_map
        self.on_choose = on_choose

        ttk.Label(self, text="Select an artist to review", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self, text=f"{len(artists_map)} artists from review_artists.yaml. Type to filter.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(self, textvariable=self.search_var)
        search_entry.pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._refresh())

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, pady=8)
        self.listbox = tk.Listbox(frame, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scroll.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scroll.set)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._choose())
        self.listbox.bind("<Return>", lambda _e: self._choose())
        search_entry.bind("<Return>", lambda _e: self._choose())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Load Posts for Selected Artist", style="Primary.TButton",
                   command=self._choose).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=on_cancel).pack(side="right")
        self._refresh()
        search_entry.focus_set()

    def _refresh(self):
        q = self.search_var.get().strip().lower()
        self.listbox.delete(0, tk.END)
        for name in sorted(self.artists_map):
            if not q or q in name.lower():
                self.listbox.insert(tk.END, name)
        if self.listbox.size() == 1:
            self.listbox.selection_set(0)

    def _choose(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Select Artist", "Please select an artist from the list.", parent=self)
            return
        self.on_choose(self.listbox.get(sel[0]))


def fetch_posts_by_artist(conn, alias_list):
    """Fetch posts where artist name resembles any of the provided aliases.

    Uses SQL LIKE '%alias%' matching for each alias.
    Returns list of dicts with fields similar to `fetch_blacklisted_posts` (plus `artists`).
    """
    if not alias_list:
        return []
    cursor = conn.cursor()

    # Build LIKE conditions for each alias
    like_clauses = ' OR '.join(['a.name LIKE %s'] * len(alias_list))

    query = f"""
        SELECT DISTINCT p.id, p.file_name, p.file_path, p.file_url, p.rating,
               GROUP_CONCAT(DISTINCT a.name SEPARATOR ', ') as artists
        FROM posts p
        JOIN posts_artists pa ON pa.post_id = p.id
        JOIN artists a ON a.artist_id = pa.artist_id
        WHERE ({like_clauses})
        AND p.deleted = 0
        GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating
        ORDER BY p.id
    """

    # Build parameters - wrap each alias for LIKE matching
    params = [f"%{alias}%" for alias in alias_list]

    try:
        cursor.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return posts
    finally:
        cursor.close()


def _load_artist_posts(aliases):
    """Off the Tk thread: connect and collect this artist's reviewable posts."""
    config = load_config()
    conn = connect_to_mysql(config.get("mysql", {}))
    if conn is None:
        raise RuntimeError("Failed to connect to database")
    try:
        posts = fetch_posts_by_artist(conn, aliases)
        posts = filter_posts_with_accessible_images(posts)
        posts = filter_kept_posts(posts, load_kept_posts(str(archive_paths.KEPT_POSTS_ARTISTS)))
    except BaseException:
        conn.close()
        raise
    return {"conn": conn, "posts": posts, "e621_config": config.get("e621", {}),
            "keybinds": load_review_keybindings()}


def build(host):
    """Pick an artist, then review their posts, all inside ``host`` (ToolHost or Tk root)."""
    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs (CWD)
    logging.info("Starting Artist Review UI")

    def close():
        tool_host.close(host)

    artists_yaml_path = archive_paths.REVIEW_ARTISTS
    artists_map = load_artists_yaml(str(artists_yaml_path))
    if artists_map is None:
        tool_host.message_panel(host, "Artist review could not start",
                                f"Failed to load {artists_yaml_path}", tone="warning",
                                actions=(("Close", close),))
        return

    def chosen(display_name):
        aliases = artists_map.get(display_name, [])
        logging.info("Selected artist: %s (aliases: %s)", display_name, aliases)
        picker.destroy()
        loading = tool_host.message_panel(host, f"Loading {display_name}",
                                          "Finding this artist's posts in the archive…", busy=True)

        def failed(exc):
            loading.destroy()
            logging.error("Artist review could not load posts: %s", exc)
            tool_host.message_panel(host, "Artist review could not start", str(exc), tone="warning",
                                    actions=(("Close", close),))

        def ready(loaded):
            loading.destroy()
            tool_host.on_destroy(host, loaded["conn"].close)
            posts = loaded["posts"]
            logging.info("Found %s posts for artist %s", len(posts), display_name)
            if not posts:
                tool_host.message_panel(host, "No posts to review",
                                        f"No posts found for artist {display_name} with accessible images.",
                                        actions=(("Close", close),))
                return
            # Use the BlacklistReviewUI but set the context label to indicate artist review
            BlacklistReviewUI(
                host,
                posts,
                loaded["conn"],
                loaded["e621_config"],
                loaded["keybinds"],
                window_title=f"Artist Review: {display_name}",
                context_label_text=f"Artist: {display_name}",
                context_label_color=theme.PALETTE["accent"],
                kept_file=str(archive_paths.KEPT_POSTS_ARTISTS),
            )

        tool_host.run_in_background(host, lambda: _load_artist_posts(aliases), ready, failed)

    def cancelled():
        logging.info("Artist selection canceled by user")
        close()

    picker = ArtistPicker(host, artists_map, chosen, cancelled)
    picker.place(relx=0.5, rely=0, relwidth=0.6, relheight=1, anchor="n")


def main(argv=None):  # fff: argv is ignored (no options); lets the CLI call it uniformly
    root = tk.Tk()
    theme.apply(root)
    root.title("Artist Review")
    root.geometry("1200x900")
    build(root)
    root.mainloop()
    try:
        root.destroy()  # quit_app only stops the loop; this closes the connection (on_destroy)
    except tk.TclError:
        pass
    logging.info("Artist Review UI closed")


if __name__ == "__main__":
    main()
