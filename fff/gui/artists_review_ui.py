#!/usr/bin/env python3
# fff: implemented in e621 archive/scripts/ui/artists_review_ui.py.
# fff: run as 'fff gui artist-review'; main(argv=None) ignores argv (the original had no options).
# fff: reads config/review_artists.yaml (was config/artists.yaml); logs go to data/logs/archive (was ./logs).
"""
Artist Review UI

Interactive tool to review posts by favorite artists defined in `artists.yaml`
(fff: config/review_artists.yaml).

Flow:
- Load `artists.yaml` mapping (DisplayName -> [aliases])
- Present a small selection UI to choose one artist (display names are shown)
- Build SQL LIKE patterns for aliases and fetch posts that have matching artist names in the DB
- Filter posts without accessible images and previously "kept" posts
- Launch the existing `BlacklistReviewUI` to review the posts (re-using UI from `blacklist_review_ui.py`)

"""

import os
import sys
import logging
import tkinter as tk
from tkinter import ttk, messagebox
import yaml
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql
from fff.archive.logging_setup import setup_logging

# Import utilities and UI from blacklist_review_ui
from fff.gui.blacklist_review_ui import (
    BlacklistReviewUI,
    load_keybindings,
    load_kept_posts,
    filter_kept_posts,
    filter_posts_with_accessible_images,
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


def choose_artist_gui(artists_map):
    """Show a simple selection window and return chosen (display_name, alias_list) or (None, None) if canceled."""
    root = tk.Tk()
    root.title("Select Artist to Review")
    root.geometry("520x420")

    ttk.Label(root, text="Select an artist to review:", font=("Arial", 12, "bold")).pack(pady=(10, 6))

    search_var = tk.StringVar()

    def on_search(*_):
        q = search_var.get().strip().lower()
        lb.delete(0, tk.END)
        for name in sorted(artists_map.keys()):
            if not q or q in name.lower():
                lb.insert(tk.END, name)

    search_entry = ttk.Entry(root, textvariable=search_var)
    search_entry.pack(fill="x", padx=12)
    search_var.trace_add('write', on_search)

    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True, padx=12, pady=8)

    lb = tk.Listbox(frame, activestyle="dotbox")
    lb.pack(side="left", fill="both", expand=True)

    scroll = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
    scroll.pack(side="right", fill="y")
    lb.config(yscrollcommand=scroll.set)

    # Populate listbox
    for name in sorted(artists_map.keys()):
        lb.insert(tk.END, name)

    chosen = {'name': None}

    def do_load():
        sel = lb.curselection()
        if not sel:
            messagebox.showinfo("Select Artist", "Please select an artist from the list.")
            return
        index = sel[0]
        name = lb.get(index)
        chosen['name'] = name
        root.destroy()

    def do_cancel():
        root.destroy()

    btn_frame = ttk.Frame(root)
    btn_frame.pack(fill="x", padx=12, pady=(0, 12))

    load_btn = ttk.Button(btn_frame, text="Load Posts for Selected Artist", command=do_load)
    load_btn.pack(side="left")

    cancel_btn = ttk.Button(btn_frame, text="Cancel", command=do_cancel)
    cancel_btn.pack(side="right")

    root.mainloop()

    if not chosen['name']:
        return None, None
    return chosen['name'], artists_map.get(chosen['name'], [])


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


def main(argv=None):  # fff: argv is ignored (no options); lets the CLI call it uniformly
    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs (CWD)
    logging.info("Starting Artist Review UI")

    config = load_config()
    mysql_config = config.get("mysql", {})
    e621_config = config.get("e621", {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        messagebox.showerror("Error", "Failed to connect to database")
        sys.exit(1)

    artists_yaml_path = archive_paths.REVIEW_ARTISTS  # fff: artists.yaml renamed (decision 3)
    artists_map = load_artists_yaml(str(artists_yaml_path))
    if artists_map is None:
        messagebox.showerror("Error", f"Failed to load {artists_yaml_path}")
        conn.close()
        sys.exit(1)

    display_name, aliases = choose_artist_gui(artists_map)
    if not display_name:
        logging.info("Artist selection canceled by user")
        conn.close()
        sys.exit(0)

    logging.info("Selected artist: %s (aliases: %s)", display_name, aliases)

    posts = fetch_posts_by_artist(conn, aliases)
    logging.info("Found %s posts for artist %s", len(posts), display_name)

    posts = filter_posts_with_accessible_images(posts)

    kept_posts_artists_path = archive_paths.KEPT_POSTS_ARTISTS
    kept_posts = load_kept_posts(str(kept_posts_artists_path))
    posts = filter_kept_posts(posts, kept_posts)

    if not posts:
        message = f"No posts found for artist {display_name} with accessible images."
        messagebox.showinfo("No Posts", message)
        conn.close()
        sys.exit(0)

    keybinds_path = archive_paths.KEYBINDS
    if keybinds_path.exists():
        keybinds = load_keybindings(str(keybinds_path))
    else:
        keybinds = load_keybindings("keybinds.yaml")

    root = tk.Tk()
    # Use the BlacklistReviewUI but set the context label to indicate artist review
    app = BlacklistReviewUI(
        root,
        posts,
        conn,
        e621_config,
        keybinds,
        window_title=f"Artist Review: {display_name}",
        context_label_text=f"Artist: {display_name}",
        context_label_color="blue",
        kept_file=str(kept_posts_artists_path),
    )

    root.mainloop()

    conn.close()
    logging.info("Artist Review UI closed")


if __name__ == "__main__":
    main()
