#!/usr/bin/env python3
# fff: the tkinter review UI (--review) is kept as-is.
"""
Validate and fix post ID mismatches in filenames.

Post filenames should follow the format: {artist}-{rating}-{post_id}.{ext}
This script checks that the post_id in the filename matches the database post ID.

Usage:
    python scripts/database/validate_post_ids_in_filenames.py [--scan-only] [--review] [--fix] [--batch-limit N]

    --scan-only       Only scan and report issues without fixing (default)
    --fix            Actually fix mismatches (renames files + updates artist names)
    --review         Open a UI to choose the owning post ID for each mismatch group
    --batch-limit N  Limit to first N posts (for testing)
    --dry-run        Show what would be done without making changes
"""

import sys
import os
import argparse
import re
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import (
    connect_to_mysql,
    scan_for_post_id_mismatches,
    extract_post_id_from_filename,
    check_and_update_file_artist_names,
    normalize_file_path
)
from fff.archive.logging_setup import setup_logging
from fff.gui import theme


def group_mismatches(mismatches):
    """Group scan results by physical file path for one ownership decision per file."""
    groups = {}
    for item in mismatches:
        group = groups.setdefault(item["file_path"], {
            "file_path": item["file_path"],
            "filename": item["filename"],
            "filename_id": item["extracted_id"],
            "posts": [],
        })
        group["posts"].append(item["post_id"])
    return list(groups.values())


def fetch_review_candidates(conn, groups):
    """Add the filename-ID candidate when that post exists in the database."""
    candidate_ids = sorted({
        group["filename_id"]
        for group in groups
        if group["filename_id"] is not None
    })
    if not candidate_ids:
        return

    cursor = conn.cursor()
    try:
        placeholders = ",".join(["%s"] * len(candidate_ids))
        cursor.execute(
            f"SELECT id FROM posts WHERE deleted = 0 AND id IN ({placeholders})",
            candidate_ids,
        )
        existing_ids = {row[0] for row in cursor.fetchall()}
    finally:
        cursor.close()

    for group in groups:
        group["candidate_ids"] = sorted(set(group["posts"]) | (
            {group["filename_id"]} if group["filename_id"] in existing_ids else set()
        ))


def replace_filename_post_id(filename, post_id):
    """Replace only the final numeric ID before the extension."""
    return re.sub(r"-(\d+)(\.[^.]+)$", f"-{post_id}\\2", filename)


def apply_review_decisions(conn, decisions, dry_run=False):
    """Transfer each reviewed file to its selected post and clear losing links."""
    stats = {"applied": 0, "skipped": 0, "failed": 0}
    for group, selected_id in decisions:
        if selected_id is None:
            stats["skipped"] += 1
            continue

        old_path = group["file_path"]
        old_filename = os.path.basename(old_path)
        new_filename = replace_filename_post_id(old_filename, selected_id)
        new_path = os.path.join(os.path.dirname(old_path), new_filename)

        if dry_run:
            print(f"[DRY RUN] {old_filename}: assign to post {selected_id} -> {new_filename}")
            stats["applied"] += 1
            continue

        try:
            if new_path != old_path:
                if os.path.exists(new_path):
                    raise FileExistsError(f"Target file already exists: {new_path}")
                os.rename(old_path, new_path)

            cursor = conn.cursor()
            try:
                cursor.execute(
                    "UPDATE posts SET file_path = NULL, file_name = NULL "
                    "WHERE file_path = %s AND id <> %s",
                    (normalize_file_path(old_path), selected_id),
                )
                cursor.execute(
                    "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                    (normalize_file_path(new_path), new_filename, selected_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                if new_path != old_path:
                    os.rename(new_path, old_path)
                raise
            finally:
                cursor.close()
            stats["applied"] += 1
        except Exception as error:
            print(f"Failed to apply post {selected_id} for {old_path}: {error}", file=sys.stderr)
            stats["failed"] += 1
    return stats


class MismatchReviewUI:
    def __init__(self, master, groups):
        self.master = master
        self.groups = groups
        self.index = 0
        self.decisions = []
        self.selected = tk.IntVar(value=-1)
        master.title("Review Filename Post ID Mismatches")
        master.geometry("900x500")

        self.progress = ttk.Label(master, style="Heading.TLabel")
        self.progress.pack(pady=(12, 4))
        self.details = ttk.Label(master, justify="left", anchor="w")
        self.details.pack(fill="x", padx=18, pady=6)
        content = ttk.Frame(master)
        content.pack(fill="both", expand=True, padx=18, pady=8)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(0, weight=1)

        preview_frame = ttk.LabelFrame(content, text="Current file")
        preview_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.preview = ttk.Label(preview_frame, anchor="center", justify="center")
        self.preview.pack(fill="both", expand=True, padx=8, pady=8)

        self.options = ttk.LabelFrame(content, text="Possible owners")
        self.options.grid(row=0, column=1, sticky="nsew")
        self.copy_status = ttk.Label(self.options, text="Select an ID to copy it.", wraplength=260)
        self.copy_status.pack(anchor="w", padx=10, pady=(8, 4))
        buttons = ttk.Frame(master)
        buttons.pack(fill="x", padx=18, pady=(0, 14))
        ttk.Button(buttons, text="Skip", command=self.skip).pack(side="left")
        ttk.Button(buttons, text="Copy Selected ID", command=self.copy_selected_id).pack(side="left", padx=8)
        ttk.Button(buttons, text="Apply Selection", command=self.apply).pack(side="right")
        master.protocol("WM_DELETE_WINDOW", self.cancel)
        self.show_group()

    def show_group(self):
        for child in self.options.winfo_children():
            child.destroy()
        if self.index >= len(self.groups):
            self.master.destroy()
            return
        group = self.groups[self.index]
        self.progress.config(text=f"Mismatch group {self.index + 1} of {len(self.groups)}")
        self.details.config(text=(
            f"File: {group['file_path']}\n"
            f"ID in filename: {group['filename_id']}\n"
            "Choose the post that owns this file:"
        ))
        self.show_preview(group["file_path"])
        self.selected.set(-1)
        filename_id = group["filename_id"]
        for post_id in group.get("candidate_ids", group["posts"]):
            label = f"Post {post_id}"
            if post_id == filename_id:
                label += " (ID currently in filename)"
            ttk.Radiobutton(
                self.options,
                text=label,
                value=post_id,
                variable=self.selected,
            ).pack(anchor="w", pady=4)

    def show_preview(self, file_path):
        self.preview_photo = None
        if Image is None or ImageTk is None:
            self.preview.config(image="", text="Pillow is not installed.\nPreview unavailable.")
            return
        try:
            with Image.open(file_path) as image:
                preview_image = image.convert("RGB")
                preview_image.thumbnail((480, 320))
                self.preview_photo = ImageTk.PhotoImage(preview_image)
            self.preview.config(image=self.preview_photo, text="")
        except Exception as error:
            self.preview.config(image="", text=f"Preview unavailable:\n{error}")

    def copy_selected_id(self):
        selected_id = self.selected.get()
        if selected_id == -1:
            self.copy_status.config(text="Select an ID to copy it.")
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(str(selected_id))
        self.master.update()
        self.copy_status.config(text=f"Copied post ID {selected_id}.")

    def record(self, selected_id):
        self.decisions.append((self.groups[self.index], selected_id))
        self.index += 1
        self.show_group()

    def apply(self):
        selected_id = self.selected.get()
        if selected_id == -1:
            messagebox.showwarning("Choose a post", "Select the post that owns this file, or use Skip.")
            return
        self.record(selected_id)

    def skip(self):
        self.record(None)

    def cancel(self):
        self.decisions = []
        self.master.destroy()

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate and fix post ID mismatches in filenames",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan for issues without fixing
  python scripts/database/validate_post_ids_in_filenames.py --scan-only

  # Scan and show what would be fixed (no changes)
  python scripts/database/validate_post_ids_in_filenames.py --fix --dry-run

  # Actually fix all mismatches
  python scripts/database/validate_post_ids_in_filenames.py --fix

    # Review each mismatch group in a UI, previewing the selected changes
    python scripts/database/validate_post_ids_in_filenames.py --review --dry-run

    # Review and apply the selected ownership fixes
    python scripts/database/validate_post_ids_in_filenames.py --review

  # Test with only first 100 posts
  python scripts/database/validate_post_ids_in_filenames.py --scan-only --batch-limit 100
        """
    )

    parser.add_argument(
        "--scan-only",
        action="store_true",
        default=False,
        help="Only scan and report issues (default if no --fix)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Run the artist rename pass while skipping files with mismatched post IDs"
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Open a UI to choose the owning post ID for each mismatch group"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=None,
        help="Limit to first N posts (for testing)"
    )

    args = parser.parse_args(argv)

    # Default to scan-only if neither specified
    if not args.fix:
        args.scan_only = True

    # Load configuration first
    config = load_config()

    # Setup logging
    log_dir = config.get("log", {}).get("directory", str(archive_paths.LOG_DIR))
    setup_logging(log_dir)

    # Connect to database
    conn = connect_to_mysql(config.get("mysql", {}))
    if not conn:
        print("ERROR: Could not connect to database", file=sys.stderr)
        return 1

    try:
        if args.review:
            print("\nScanning for mismatches...")
            results = scan_for_post_id_mismatches(conn, batch_limit=args.batch_limit)
            groups = group_mismatches(results["mismatched"])
            if not groups:
                print("No mismatches found.")
                return 0

            fetch_review_candidates(conn, groups)
            root = tk.Tk()
            theme.apply(root)
            review_ui = MismatchReviewUI(root, groups)
            root.mainloop()

            if not review_ui.decisions:
                print("Review canceled; no changes made.")
                return 0

            stats = apply_review_decisions(conn, review_ui.decisions, dry_run=args.dry_run)
            print(
                f"Review complete: {stats['applied']} applied, "
                f"{stats['skipped']} skipped, {stats['failed']} failed."
            )
            return 0 if stats["failed"] == 0 else 1

        if args.scan_only:
            print("\n" + "=" * 70)
            print("SCANNING FOR POST ID MISMATCHES IN FILENAMES")
            print("=" * 70 + "\n")

            results = scan_for_post_id_mismatches(conn, batch_limit=args.batch_limit)

            print(f"\nScan Complete: {results['checked']} posts checked")
            print(f"  [OK] Valid:       {results['valid']}")
            print(f"  [!!] Mismatched:  {len(results['mismatched'])}")
            print(f"  [!!] Unreadable:  {len(results['unreadable'])}")

            if results['mismatched']:
                print("\n" + "-" * 70)
                print("POSTS WITH ID MISMATCHES (need fixing):")
                print("-" * 70)
                for item in results['mismatched']:
                    print(f"\n  Post {item['post_id']}:")
                    print(f"    Filename has ID: {item['extracted_id']}")
                    print(f"    File: {item['filename']}")
                    print(f"    Path: {item['file_path']}")
                print()

            if results['unreadable']:
                print("\n" + "-" * 70)
                print("POSTS WITH UNREADABLE IDs (may have wrong format):")
                print("-" * 70)
                for item in results['unreadable']:
                    print(f"\n  Post {item['post_id']}:")
                    print(f"    File: {item['filename']}")
                    print(f"    Path: {item['file_path']}")
                print()

            if not results['mismatched'] and not results['unreadable']:
                print("\n[OK] All post IDs in filenames are valid!\n")

            return 0

        elif args.fix:
            print("\n" + "=" * 70)
            print("FIXING POST ID MISMATCHES")
            print("=" * 70)
            print(f"DRY RUN: {args.dry_run}\n")

            # First scan to show what will be fixed
            print("Scanning for mismatches...")
            results = scan_for_post_id_mismatches(conn, batch_limit=args.batch_limit)

            if not results['mismatched'] and not results['unreadable']:
                print("\n✓ No mismatches found!\n")
                return 0

            print(f"\nFound {len(results['mismatched'])} mismatches and {len(results['unreadable'])} unreadable posts")
            print("\nNow running artist name check (mismatched post IDs will be skipped)...")
            print("-" * 70 + "\n")

            # Use the main function which now validates post IDs during rename
            stats = check_and_update_file_artist_names(
                conn,
                batch_limit=args.batch_limit,
                dry_run=args.dry_run
            )

            print("\n" + "=" * 70)
            print("FIX COMPLETE")
            print("=" * 70)
            print(f"  Checked:               {stats['checked']}")
            print(f"  Renamed:               {stats['renamed']}")
            print(f"  Post ID Skipped:       {stats['post_id_mismatch_skipped']}")
            print(f"  Failed:                {stats['failed']}")
            print(f"  Skipped (no file):     {stats['skipped_no_file']}")
            print(f"  Skipped (no change):   {stats['skipped_no_change']}")
            print(f"  ID extraction failed:  {stats['post_id_extraction_failed']}")
            print()

            if args.dry_run:
                print("NOTE: This was a DRY RUN. No files were changed.")
                print("Run again without --dry-run to apply the fixes.\n")

            return 0

    except KeyboardInterrupt:
        print("\n\nInterrupted by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    sys.exit(main())
