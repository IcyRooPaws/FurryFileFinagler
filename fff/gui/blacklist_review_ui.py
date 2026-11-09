#!/usr/bin/env python3
# fff: run as 'fff gui blacklist-review'; main(argv=None) ignores argv (the original had no options).
# fff: file deletes go to the recycle bin (send2trash) instead of os.remove: both the critical
# fff: auto-delete and the manual Delete button use the recycle bin.
# fff: the AI folder comes from config archive.ai_cleanup (default E:\Image Sorter Working Folder\AI).
"""
Blacklist Review UI

Interactive GUI to review posts with blacklisted tags.

Two types of blacklists:
1. blacklist.txt - Posts for manual review (shown in UI)
2. critical_blacklist.txt - Posts automatically deleted without review

blacklist_ignore.txt - blacklist tags that don't count as hits in the review (a post still
shows if another blacklisted tag matches).

Kept posts tracking:
- Posts marked as "Keep" are saved to kept_posts.txt
- These posts won't appear in future reviews
- Going back removes them from the kept list

Shows each post's image and blacklisted tags, allowing you to:
- Go back to previous post (P)
- Keep the post (K) - saves to kept_posts.txt
- Delete the file and remove from e621 favorites (D)

Default keybinds: P for previous, K to keep, D to delete, Q to quit.
Customize keybindings in keybinds.yaml
"""

import os
import logging
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageSequence
import requests
from io import BytesIO
import threading
import yaml
from pathlib import Path
import shutil
from send2trash import send2trash

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, record_ai_flagged_artists
from fff.archive.fa_unfavorite import unfavorite_fa_sources
from fff.archive.logging_setup import setup_logging
from fff.archive.api import api_get
from fff.gui import host as tool_host, theme

# Try to import cv2 for video support
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logging.warning("opencv-python not installed - video preview will be limited")

# Try to import pygame for audio (use pygame-ce package for Python 3.14+ compatibility)
try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False
    logging.warning("pygame-ce not installed - video audio will not be available")


def load_keybindings(keybind_file="keybinds.yaml"):
    """Load keybindings from YAML file."""
    default_keybinds = {
        "previous": ["p"],
        "keep": ["k"],
        "delete": ["d"],
        "quit": ["q"]
    }

    if not os.path.exists(keybind_file):
        logging.info(f"Keybind file not found: {keybind_file}, using defaults")
        return default_keybinds

    try:
        with open(keybind_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if config and 'keybinds' in config:
            keybinds = config['keybinds']
            # Validate that all required actions have at least one key
            for action in default_keybinds:
                if action not in keybinds or not keybinds[action]:
                    logging.warning(f"Missing or empty keybind for '{action}', using default")
                    keybinds[action] = default_keybinds[action]

            logging.info(f"Loaded custom keybindings from {keybind_file}")
            return keybinds
        else:
            logging.warning(f"Invalid keybind file format, using defaults")
            return default_keybinds

    except Exception as e:
        logging.error(f"Error reading keybind file: {e}, using defaults")
        return default_keybinds


class BlacklistReviewUI:
    def __init__(
        self,
        master,
        posts,
        conn,
        e621_config,
        keybinds=None,
        *,
        window_title="Blacklist Review UI",
        context_label_text="Blacklisted Tags:",
        context_label_color=theme.PALETTE["error"],
        kept_file="kept_posts.txt",
        tag_actions=False
    ):
        self.master = master
        self.posts = posts
        self.conn = conn
        self.e621_config = e621_config
        self.current_index = 0
        self.deleted_count = 0
        self.kept_count = 0
        self.kept_file = kept_file
        self.tag_actions = tag_actions
        self.busy = False  # a tag action's rescan is running (ignore keep/delete/previous)

        # Load keybindings (use provided or default)
        self.keybinds = keybinds if keybinds else {
            "previous": ["p"],
            "keep": ["k"],
            "delete": ["d"],
            "quit": ["q"]
        }

        # Navigation history to support going back
        self.navigation_history = []  # Stack of previous indices

        # GIF animation support
        self.gif_frames = []
        self.gif_frame_index = 0
        self.gif_animation_id = None

        # Video support
        self.is_video = False
        self.video_cap = None
        self.video_playing = False
        self.video_thread = None
        self.current_video_path = None
        self.video_has_audio = False
        self.mute_audio = True  # Default to muted

        master.title(window_title)
        master.geometry("1200x900")

        # Configure grid weights
        master.grid_rowconfigure(1, weight=1)
        master.grid_columnconfigure(0, weight=1)

        # Top info panel
        self.info_frame = ttk.Frame(master, padding="10")
        self.info_frame.grid(row=0, column=0, sticky="ew")

        self.progress_label = ttk.Label(self.info_frame, text="", font=theme.font(12))
        self.progress_label.pack()

        # Main content frame
        self.content_frame = ttk.Frame(master, padding="10")
        self.content_frame.grid(row=1, column=0, sticky="nsew")
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        # Image canvas with scrollbar
        self.canvas_frame = ttk.Frame(self.content_frame)
        self.canvas_frame.grid(row=0, column=0, sticky="nsew")
        self.canvas_frame.grid_rowconfigure(0, weight=1)
        self.canvas_frame.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.canvas_frame, bg=theme.PALETTE["media"])
        self.scrollbar_y = ttk.Scrollbar(self.canvas_frame, orient="vertical", command=self.canvas.yview)
        self.scrollbar_x = ttk.Scrollbar(self.canvas_frame, orient="horizontal", command=self.canvas.xview)

        self.canvas.configure(yscrollcommand=self.scrollbar_y.set, xscrollcommand=self.scrollbar_x.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_y.grid(row=0, column=1, sticky="ns")
        self.scrollbar_x.grid(row=1, column=0, sticky="ew")

        self.image_id = None

        # Info panel (right side)
        self.info_panel = ttk.Frame(self.content_frame, padding="10")
        self.info_panel.grid(row=0, column=1, sticky="nsew")

        # Post info
        ttk.Label(self.info_panel, text="Post Information", font=theme.font(14, "bold")).pack(pady=(0, 10))

        self.post_id_label = ttk.Label(self.info_panel, text="", font=theme.font(11))
        self.post_id_label.pack(anchor="w", pady=2)

        self.file_name_label = ttk.Label(self.info_panel, text="", font=theme.font(10), wraplength=300)
        self.file_name_label.pack(anchor="w", pady=2)

        self.rating_label = ttk.Label(self.info_panel, text="", font=theme.font(10))
        self.rating_label.pack(anchor="w", pady=2)

        ttk.Separator(self.info_panel, orient="horizontal").pack(fill="x", pady=10)

        # Context label (tags/artists/other)
        self.context_label = ttk.Label(
            self.info_panel,
            text=context_label_text,
            font=theme.font(12, "bold"),
            foreground=context_label_color
        )
        self.context_label.pack(pady=(0, 5))

        self.tags_text = tk.Text(self.info_panel, height=15, width=35, wrap="word", font=theme.font(10),
                                 padx=6, pady=4)
        self.tags_text.pack(fill="both", expand=True, pady=(0, 10))

        # Tag actions (blacklist review only): change a list file, then rescan
        if self.tag_actions:
            tag_frame = ttk.Frame(self.info_panel)
            tag_frame.pack(fill="x", pady=(0, 10))
            ttk.Label(tag_frame, text="Tag:", font=theme.font(10)).grid(row=0, column=0, sticky="w")
            self.tag_choice = ttk.Combobox(tag_frame, state="readonly", width=28)
            self.tag_choice.grid(row=0, column=1, sticky="ew", padx=(5, 0))
            tag_frame.grid_columnconfigure(1, weight=1)
            self.unblacklist_button = ttk.Button(
                tag_frame, text="Remove from blacklist & rescan", command=self.remove_tag_from_blacklist)
            self.unblacklist_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            self.critical_button = ttk.Button(
                tag_frame, text="Move to critical blacklist & rescan", command=self.move_tag_to_critical)
            self.critical_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
            self.ignore_button = ttk.Button(
                tag_frame, text="Ignore this tag & rescan", command=self.ignore_tag)
            self.ignore_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        # E621 link
        self.link_label = ttk.Label(self.info_panel, text="", font=theme.font(9), foreground=theme.PALETTE["accent"], cursor="hand2")
        self.link_label.pack(pady=5)
        self.link_label.bind("<Button-1>", self.open_e621_link)

        # Button frame
        self.button_frame = ttk.Frame(master, padding="10")
        self.button_frame.grid(row=2, column=0, sticky="ew")

        # Skip videos checkbox
        self.skip_videos_var = tk.BooleanVar(value=False)
        self.skip_videos_check = ttk.Checkbutton(
            self.button_frame,
            text="Skip Videos",
            variable=self.skip_videos_var,
            command=self.on_skip_videos_changed
        )
        self.skip_videos_check.pack(side="left", padx=5)

        # Video controls frame
        self.video_controls_frame = ttk.Frame(self.button_frame)
        self.video_controls_frame.pack(side="left", padx=15)

        # Play/Pause button
        self.play_button = ttk.Button(self.video_controls_frame, text="> Play", command=self.toggle_video_playback, width=12, state="disabled")
        self.play_button.grid(row=0, column=0, padx=2)

        # Mute checkbox
        self.mute_var = tk.BooleanVar(value=True)
        self.mute_check = ttk.Checkbutton(
            self.video_controls_frame,
            text="Mute",
            variable=self.mute_var,
            command=self.on_mute_changed
        )
        self.mute_check.grid(row=0, column=1, padx=2)

        # Audio indicator
        self.audio_label = ttk.Label(self.video_controls_frame, text="", font=theme.font(9))
        self.audio_label.grid(row=0, column=2, padx=5)

        # Navigation and action buttons (with custom keybind labels)
        prev_keys = "/".join([k.upper() for k in self.keybinds.get("previous", ["P"])])
        keep_keys = "/".join([k.upper() for k in self.keybinds.get("keep", ["K"])])
        delete_keys = "/".join([k.upper() for k in self.keybinds.get("delete", ["D"])])
        quit_keys = "/".join([k.upper() for k in self.keybinds.get("quit", ["Q"])])

        self.prev_button = ttk.Button(self.button_frame, text=f"<- Previous ({prev_keys})", command=self.previous_post, width=20)
        self.prev_button.pack(side="left", padx=5)

        self.keep_button = ttk.Button(self.button_frame, text=f"Keep ({keep_keys})", command=self.keep_post, width=20)
        self.keep_button.pack(side="left", padx=5)

        self.delete_button = ttk.Button(self.button_frame, text=f"Delete & Unfavorite ({delete_keys})", command=self.delete_post, width=25)
        self.delete_button.pack(side="left", padx=5)

        self.quit_button = ttk.Button(self.button_frame, text=f"Quit ({quit_keys})", command=self.quit_app, width=15)
        self.quit_button.pack(side="right", padx=5)

        # Statistics label
        self.stats_label = ttk.Label(self.button_frame, text="", font=theme.font(10))
        self.stats_label.pack(side="right", padx=20)

        # Keyboard bindings (bind all custom keys)
        self._setup_keybindings()
        master.protocol("WM_DELETE_WINDOW", self.quit_app)

        # Load first post
        self.load_post()

    def _setup_keybindings(self):
        """Setup keyboard bindings from configuration."""
        # Special keys that need exact capitalization for tkinter
        special_keys = ['Left', 'Right', 'Up', 'Down', 'Escape', 'Delete', 'Home', 'End',
                       'Prior', 'Next', 'Return', 'Tab', 'BackSpace', 'Insert',
                       'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12']

        def bind_key(key, action_func):
            """Bind a key to an action, handling special keys properly."""
            # Check if it's a special key (case-insensitive check)
            is_special = any(key.lower() == sk.lower() for sk in special_keys)

            if is_special:
                # For special keys, use the proper capitalization
                proper_key = next((sk for sk in special_keys if sk.lower() == key.lower()), key)
                self.master.bind(f'<{proper_key}>', lambda e, f=action_func: f())
            else:
                # For regular keys, bind both upper and lower case
                self.master.bind(f'<{key.lower()}>', lambda e, f=action_func: f())
                self.master.bind(f'<{key.upper()}>', lambda e, f=action_func: f())

        # Bind all keys for each action
        for key in self.keybinds.get("previous", []):
            bind_key(key, self.previous_post)

        for key in self.keybinds.get("keep", []):
            bind_key(key, self.keep_post)

        for key in self.keybinds.get("delete", []):
            bind_key(key, self.delete_post)

        for key in self.keybinds.get("quit", []):
            bind_key(key, self.quit_app)

        logging.info(f"Keybindings configured: {self.keybinds}")

    def on_skip_videos_changed(self):
        """Handle skip videos checkbox change."""
        if self.skip_videos_var.get():
            # If we just enabled skip videos and current post is a video, skip to next non-video
            self.skip_to_next_non_video()

    def is_current_post_video(self):
        """Check if current post is a video."""
        if self.current_index >= len(self.posts):
            return False
        post = self.posts[self.current_index]
        file_path = post.get('file_path')
        file_url = post.get('file_url')

        # Check file path
        if file_path and self.is_video_file(file_path):
            return True
        # Check URL
        if file_url and self.is_video_file(file_url):
            return True
        return False

    def skip_to_next_non_video(self):
        """Skip to the next non-video post."""
        start_index = self.current_index
        while self.current_index < len(self.posts):
            if not self.is_current_post_video():
                self.load_post()
                return
            self.current_index += 1

        # If we reach here, no more non-video posts
        if self.current_index >= len(self.posts):
            self.show_completion()

    def load_post(self):
        """Load current post's image and information."""
        if self.current_index >= len(self.posts):
            self.show_completion()
            return

        post = self.posts[self.current_index]

        # Update progress
        self.progress_label.config(text=f"Post {self.current_index + 1} of {len(self.posts)}")

        # Update post info
        self.post_id_label.config(text=f"Post ID: {post['id']}")
        self.file_name_label.config(text=f"File: {post['file_name'] or 'N/A'}")
        self.rating_label.config(text=f"Rating: {post['rating'] or 'N/A'}")

        # Update blacklisted tags or artists (for artist-review mode)
        self.tags_text.delete(1.0, tk.END)
        tags_display = None
        # Prefer explicit blacklisted_tags field, fall back to artists or a generic representation
        if post.get('blacklisted_tags'):
            tags_display = post.get('blacklisted_tags')
        elif post.get('artists'):
            tags_display = post.get('artists')
        else:
            # Try joining any list-like fields for display
            for key in ['tags', 'artist', 'artists_list']:
                val = post.get(key)
                if isinstance(val, (list, tuple)) and val:
                    tags_display = ', '.join(val)
                    break
        if tags_display is None:
            tags_display = ''
        self.tags_text.insert(1.0, tags_display)

        if self.tag_actions:
            choices = [t.strip() for t in (post.get('blacklisted_tags') or '').split(',') if t.strip()]
            self.tag_choice.config(values=choices)
            self.tag_choice.set(choices[0] if choices else '')
            state = "normal" if choices else "disabled"
            self.unblacklist_button.config(state=state)
            self.critical_button.config(state=state)
            self.ignore_button.config(state=state)

        # Update e621 link
        self.link_label.config(text=f"View on e621: https://e621.net/posts/{post['id']}")
        self.current_e621_link = f"https://e621.net/posts/{post['id']}"

        # Update statistics
        self.stats_label.config(text=f"Kept: {self.kept_count} | Deleted: {self.deleted_count}")

        # Enable/disable Previous button based on navigation history
        if self.navigation_history:
            self.prev_button.config(state="normal")
        else:
            self.prev_button.config(state="disabled")

        # Load image
        self.load_image(post)

    def stop_gif_animation(self):
        """Stop any running GIF animation."""
        if self.gif_animation_id:
            self.master.after_cancel(self.gif_animation_id)
            self.gif_animation_id = None
        self.gif_frames = []
        self.gif_frame_index = 0
        self.is_video = False

    def load_image(self, post):
        """Load and display the image."""
        # Stop any existing animation or video
        self.stop_gif_animation()
        self.stop_video()

        try:
            file_path = post.get('file_path')

            # Try to load from local file first
            if file_path and os.path.exists(file_path):
                self.display_image_from_file(file_path)
            else:
                # Load from URL
                file_url = post.get('file_url')
                if file_url:
                    self.display_image_from_url(file_url)
                else:
                    self.show_no_image()
        except Exception as e:
            logging.error(f"Error loading image for post {post['id']}: {e}")
            self.show_no_image()

    def is_video_file(self, file_path):
        """Check if file is a video."""
        video_extensions = ['.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v', '.wmv', '.flv', '.3gp', '.ogv']
        return any(file_path.lower().endswith(ext) for ext in video_extensions)

    def display_image_from_file(self, file_path):
        """Display image/gif/video from local file."""
        try:
            # Check if it's a video file
            if self.is_video_file(file_path):
                self.display_video_thumbnail(file_path)
            else:
                img = Image.open(file_path)
                # Check if it's an animated GIF
                if hasattr(img, 'is_animated') and img.is_animated:
                    self.display_animated_gif(img)
                else:
                    self.display_image(img)
        except Exception as e:
            logging.error(f"Error loading image from file: {e}")
            self.show_no_image()

    def display_video_thumbnail(self, file_path):
        """Display first frame of video as thumbnail."""
        try:
            if not HAS_CV2:
                # Show placeholder for video
                self.is_video = True
                self.canvas.delete("all")
                canvas_width = self.canvas.winfo_width() or 800
                canvas_height = self.canvas.winfo_height() or 600
                self.canvas.create_text(
                    canvas_width // 2,
                    canvas_height // 2,
                    text=f"VIDEO FILE\n\n{os.path.basename(file_path)}\n\nInstall opencv-python to preview",
                    font=theme.font(14),
                    fill="white",
                    justify="center"
                )
                return

            # Extract first frame using cv2
            cap = cv2.VideoCapture(file_path)
            ret, frame = cap.read()
            cap.release()

            if ret:
                # Convert BGR to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                self.is_video = True

                # Store video path for playback
                self.current_video_path = file_path

                # Check for audio
                self.video_has_audio = self.check_video_has_audio(file_path)

                # Update audio indicator
                if self.video_has_audio:
                    if HAS_PYGAME:
                        self.audio_label.config(text="[AUDIO] Has Audio", foreground=theme.PALETTE["ok"])
                    else:
                        self.audio_label.config(text="[AUDIO] Has Audio (pygame required)", foreground=theme.PALETTE["warning"])
                else:
                    self.audio_label.config(text="[MUTE] No Audio", foreground=theme.PALETTE["muted"])

                # Enable play button
                self.play_button.config(state="normal")

                self.display_image(img, show_video_indicator=True)
            else:
                raise Exception("Could not read video frame")
        except Exception as e:
            logging.error(f"Error loading video thumbnail: {e}")
            self.is_video = True
            self.canvas.delete("all")
            canvas_width = self.canvas.winfo_width() or 800
            canvas_height = self.canvas.winfo_height() or 600
            self.canvas.create_text(
                canvas_width // 2,
                canvas_height // 2,
                text=f"VIDEO FILE\n\n{os.path.basename(file_path)}\n\n(Preview unavailable)",
                font=theme.font(14),
                fill="white",
                justify="center"
            )

    def display_animated_gif(self, img):
        """Display animated GIF."""
        try:
            # Extract all frames
            self.gif_frames = []
            for frame in ImageSequence.Iterator(img):
                # Convert frame to RGB and copy it
                frame_copy = frame.convert('RGBA')
                self.gif_frames.append(frame_copy)

            if self.gif_frames:
                # Start animation
                self.gif_frame_index = 0
                self.animate_gif()
        except Exception as e:
            logging.error(f"Error preparing animated GIF: {e}")
            # Fall back to displaying first frame
            self.display_image(img)

    def animate_gif(self):
        """Animate GIF by cycling through frames."""
        if not self.gif_frames:
            return

        try:
            frame = self.gif_frames[self.gif_frame_index]
            self.display_image(frame)

            # Move to next frame
            self.gif_frame_index = (self.gif_frame_index + 1) % len(self.gif_frames)

            # Schedule next frame (100ms delay, adjust as needed)
            self.gif_animation_id = self.master.after(100, self.animate_gif)
        except Exception as e:
            logging.error(f"Error animating GIF: {e}")
            self.gif_animation_id = None

    def display_image_from_url(self, url):
        """Display image/gif/video from URL."""
        try:
            # Check if it's a video URL
            if self.is_video_file(url):
                # For videos from URL, just show a message
                self.is_video = True
                self.canvas.delete("all")
                canvas_width = self.canvas.winfo_width() or 800
                canvas_height = self.canvas.winfo_height() or 600
                self.canvas.create_text(
                    canvas_width // 2,
                    canvas_height // 2,
                    text=f"VIDEO FILE\n\nURL: {url[:60]}...\n\n(Preview from URL not supported)",
                    font=theme.font(12),
                    fill="white",
                    justify="center"
                )
                return

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Check content type first
            content_type = response.headers.get('content-type', '').lower()
            content_length = len(response.content)

            # Log the response details for debugging
            logging.info(f"URL response - Content-Type: {content_type}, Length: {content_length} bytes")

            # Try to identify the image
            try:
                img = Image.open(BytesIO(response.content))

                # Check if it's an animated GIF
                if hasattr(img, 'is_animated') and img.is_animated:
                    self.display_animated_gif(img)
                else:
                    self.display_image(img)
            except Exception as img_error:
                # Could not identify image - might be a video or unsupported format
                logging.error(f"Cannot identify image from URL: {img_error}")
                logging.error(f"Content-Type: {content_type}, First 200 bytes: {response.content[:200]}")

                # Check content type
                if 'video' in content_type:
                    self.is_video = True
                    self.canvas.delete("all")
                    canvas_width = self.canvas.winfo_width() or 800
                    canvas_height = self.canvas.winfo_height() or 600
                    self.canvas.create_text(
                        canvas_width // 2,
                        canvas_height // 2,
                        text=f"VIDEO FILE\n\n(Content-Type: {content_type})\n\n(Preview from URL not supported)",
                        font=theme.font(12),
                        fill="white",
                        justify="center"
                    )
                elif 'html' in content_type or 'text' in content_type:
                    # Got HTML/text instead of media - likely an error page
                    self.canvas.delete("all")
                    canvas_width = self.canvas.winfo_width() or 800
                    canvas_height = self.canvas.winfo_height() or 600
                    self.canvas.create_text(
                        canvas_width // 2,
                        canvas_height // 2,
                        text=f"ERROR: Received {content_type}\ninstead of image/video\n\nURL may be invalid or content deleted",
                        font=theme.font(12),
                        fill="red",
                        justify="center"
                    )
                else:
                    # Unknown format - show details
                    self.canvas.delete("all")
                    canvas_width = self.canvas.winfo_width() or 800
                    canvas_height = self.canvas.winfo_height() or 600
                    self.canvas.create_text(
                        canvas_width // 2,
                        canvas_height // 2,
                        text=f"UNSUPPORTED FORMAT\n\nContent-Type: {content_type}\nSize: {content_length} bytes\n\nCannot display this file",
                        font=theme.font(12),
                        fill="orange",
                        justify="center"
                    )

        except Exception as e:
            logging.error(f"Error loading image from URL: {e}")
            self.show_no_image()

    def display_image(self, img, show_video_indicator=False):
        """Display PIL image on canvas."""
        try:
            # Get canvas dimensions
            self.canvas.update_idletasks()
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()

            # Validate canvas dimensions (1x1 = not laid out yet, e.g. the first post)
            if canvas_width <= 1:
                canvas_width = 800
            if canvas_height <= 1:
                canvas_height = 600

            # Validate image dimensions
            img_width, img_height = img.size
            if img_width <= 0 or img_height <= 0:
                logging.error(f"Invalid image dimensions: {img_width}x{img_height}")
                self.show_no_image()
                return

            # Resize image to fit canvas while maintaining aspect ratio
            scale = min(canvas_width / img_width, canvas_height / img_height, 1.0)

            if scale < 1.0:
                new_width = max(1, int(img_width * scale))
                new_height = max(1, int(img_height * scale))
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Convert to PhotoImage
            self.photo = ImageTk.PhotoImage(img)

            # Update canvas
            self.canvas.delete("all")
            self.canvas.create_image(canvas_width // 2, canvas_height // 2, image=self.photo, anchor="center")

            # Add video indicator if this is a video thumbnail
            if show_video_indicator:
                # Add a play button overlay
                overlay_size = 60
                x_center = canvas_width // 2
                y_center = canvas_height // 2
                self.canvas.create_oval(
                    x_center - overlay_size, y_center - overlay_size,
                    x_center + overlay_size, y_center + overlay_size,
                    fill="black", outline="white", width=3, stipple="gray50"
                )
                # Draw play triangle
                self.canvas.create_polygon(
                    x_center - 20, y_center - 30,
                    x_center - 20, y_center + 30,
                    x_center + 30, y_center,
                    fill="white", outline="white"
                )
                # Add text with a rectangle background
                text_x = canvas_width // 2
                text_y = canvas_height - 30
                text_item = self.canvas.create_text(
                    text_x, text_y,
                    text="VIDEO PREVIEW (First Frame)",
                    font=theme.font(12, "bold"),
                    fill="white"
                )
                # Create background rectangle for text
                bbox = self.canvas.bbox(text_item)
                if bbox:
                    self.canvas.create_rectangle(
                        bbox[0] - 5, bbox[1] - 2,
                        bbox[2] + 5, bbox[3] + 2,
                        fill="black", outline=""
                    )
                    # Recreate text on top of rectangle
                    self.canvas.create_text(
                        text_x, text_y,
                        text="VIDEO PREVIEW (First Frame)",
                        font=theme.font(12, "bold"),
                        fill="white"
                    )

            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception as e:
            logging.error(f"Error displaying image: {e}")
            self.show_no_image()

    def show_no_image(self):
        """Show 'No image available' message."""
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text="No image available",
            font=theme.font(16),
            fill="white"
        )

    def open_e621_link(self, event):
        """Open e621 link in browser."""
        import webbrowser
        webbrowser.open(self.current_e621_link)

    def keep_post(self):
        """Keep the post and move to next."""
        if self.busy or self.current_index >= len(self.posts):
            return
        post_id = self.posts[self.current_index]['id']
        self.kept_count += 1
        logging.info(f"Kept post {post_id}")

        # Save to kept posts file so it won't show up again
        save_kept_post(post_id, self.kept_file)

        # Add current index to history before moving forward
        self.navigation_history.append(self.current_index)

        self.current_index += 1

        # If skip videos is enabled, skip to next non-video
        if self.skip_videos_var.get():
            self.skip_to_next_non_video()
        else:
            self.load_post()

    def previous_post(self):
        """Go back to the previous post."""
        if self.busy:
            return
        if not self.navigation_history:
            logging.info("No previous posts to go back to")
            messagebox.showinfo("No Previous Post", "You are at the beginning of the review.")
            return

        # Pop the last index from history
        previous_index = self.navigation_history.pop()

        # If we kept the previous post, decrement kept count and remove from kept list
        if previous_index < self.current_index:
            if self.kept_count > 0:
                self.kept_count -= 1
                # Remove from kept posts file since we're going back to review it
                post_id = self.posts[previous_index]['id']
                remove_kept_post(post_id, self.kept_file)

        # Move back to that index
        self.current_index = previous_index
        logging.info(f"Going back to post {self.posts[self.current_index]['id']}")

        self.load_post()

    def delete_post(self):
        """Delete file and unfavorite from e621."""
        if self.busy or self.current_index >= len(self.posts):
            return
        post = self.posts[self.current_index]
        post_id = post['id']

        # Confirm deletion
        result = messagebox.askyesno(
            "Confirm Deletion",
            f"Delete post {post_id} and remove from e621 favorites?\n\n"
            f"This will:\n"
            f"- Delete the file from disk (if exists)\n"
            f"- Remove from e621 favorites\n"
            f"- Remove from local database"
        )

        if not result:
            return

        try:
            # Delete file if it exists
            file_path = post.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    send2trash(file_path)
                    logging.info(f"Deleted file (sent to recycle bin): {file_path}")
                except Exception as e:
                    logging.error(f"Failed to delete file {file_path}: {e}")

            # Unfavorite on e621
            try:
                self.unfavorite_on_e621(post_id)
            except Exception as e:
                logging.error(f"Failed to unfavorite post {post_id} on e621: {e}")
                messagebox.showwarning("Warning", f"Failed to unfavorite on e621: {e}\nPost will still be deleted locally.")

            # Unfavorite its FA source while source_links still exist
            unfavorite_fa_sources(self.conn, [post_id], self.e621_config)

            # Delete from database
            self.delete_from_database(post_id)

            self.deleted_count += 1
            logging.info(f"Deleted post {post_id}")

            # Remove from list
            self.posts.pop(self.current_index)

            # Adjust navigation history: decrement all indices that are greater than current_index
            self.navigation_history = [idx if idx < self.current_index else idx - 1
                                       for idx in self.navigation_history]

            # If skip videos is enabled, skip to next non-video
            if self.skip_videos_var.get():
                self.skip_to_next_non_video()
            else:
                self.load_post()

        except Exception as e:
            logging.error(f"Error deleting post {post_id}: {e}")
            messagebox.showerror("Error", f"Failed to delete post: {e}")

    def unfavorite_on_e621(self, post_id):
        """Remove post from e621 favorites."""
        url = f"https://e621.net/favorites/{post_id}.json"
        auth = (self.e621_config.get("login"), self.e621_config.get("api_key"))

        response = requests.delete(url, auth=auth, headers={
            "User-Agent": self.e621_config.get("user_agent", "e621 archive/1.0")
        })

        # Accept both 200 (OK) and 204 (No Content) as success
        if response.status_code in [200, 204]:
            logging.info(f"Successfully unfavorited post {post_id} on e621")
        # 404 or 422 means post was already unfavorited or doesn't exist - that's OK
        elif response.status_code in [404, 422]:
            logging.info(f"Post {post_id} was already unfavorited or doesn't exist on e621")
        else:
            raise Exception(f"HTTP {response.status_code}: {response.text}")

    def delete_from_database(self, post_id):
        """Delete post and related data from database."""
        cursor = self.conn.cursor()
        try:
            # Delete related data
            cursor.execute("DELETE FROM source_links WHERE post_id = %s", (post_id,))
            cursor.execute("DELETE FROM posts_artists WHERE post_id = %s", (post_id,))
            cursor.execute("DELETE FROM posts_tags WHERE post_id = %s", (post_id,))
            cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
            self.conn.commit()
            logging.info(f"Deleted post {post_id} from database")
        except Exception as e:
            self.conn.rollback()
            raise e
        finally:
            cursor.close()

    def _selected_tag(self):
        if self.busy or self.current_index >= len(self.posts):
            return None
        tag = self.tag_choice.get().strip()
        return tag or None

    def remove_tag_from_blacklist(self):
        """Take the selected tag off blacklist.txt, then rescan the review list."""
        tag = self._selected_tag()
        if not tag:
            return
        if not messagebox.askyesno(
                "Remove From Blacklist",
                f"Remove '{tag}' from the blacklist and rescan?\n\n"
                f"Posts flagged only by this tag will drop out of the review.",
                parent=self.master):
            return
        try:
            removed = remove_tag_from_list_file(archive_paths.BLACKLIST, tag)
        except OSError as e:
            messagebox.showerror("Error", f"Could not update the blacklist: {e}", parent=self.master)
            return
        if not removed:
            logging.warning("Tag %r was not found in %s", tag, archive_paths.BLACKLIST)
        logging.info("Removed tag %r from the blacklist", tag)
        self._rescan(f"Removed '{tag}' from the blacklist. Rescanning…", lambda: 0)

    def ignore_tag(self):
        """Add the selected tag to blacklist_ignore.txt, then rescan the review list."""
        tag = self._selected_tag()
        if not tag:
            return
        if not messagebox.askyesno(
                "Ignore Tag",
                f"Ignore '{tag}' in the blacklist review?\n\n"
                f"Posts flagged only by this tag stop coming up. Posts that also have another "
                f"blacklisted tag still come up. blacklist.txt is left as it is.\n\n"
                f"Undo by removing the tag from config/lists/blacklist_ignore.txt.",
                parent=self.master):
            return
        try:
            add_tag_to_list_file(archive_paths.BLACKLIST_IGNORE, tag)
        except OSError as e:
            messagebox.showerror("Error", f"Could not update the ignore list: {e}", parent=self.master)
            return
        logging.info("Added tag %r to the blacklist ignore list", tag)
        self._rescan(f"Ignoring posts tagged '{tag}'. Rescanning…", lambda: 0)

    def move_tag_to_critical(self):
        """Move the selected tag to critical_blacklist.txt, auto-delete its posts, then rescan."""
        tag = self._selected_tag()
        if not tag:
            return
        try:
            matching = len(fetch_blacklisted_posts(self.conn, [tag]))
        except Exception as e:
            messagebox.showerror("Error", f"Could not count posts tagged '{tag}': {e}", parent=self.master)
            return
        if not messagebox.askyesno(
                "Move To Critical Blacklist",
                f"Move '{tag}' to the critical blacklist and rescan?\n\n"
                f"{matching} post(s) in the archive carry this tag (including any you kept). "
                f"Each will be deleted now, without review:\n"
                f"- File sent to the recycle bin\n"
                f"- Removed from e621 favorites (and its FA source unfavorited)\n"
                f"- Removed from the local database\n\n"
                f"Future reviews will auto-delete this tag too.",
                icon="warning", parent=self.master):
            return
        try:
            add_tag_to_list_file(archive_paths.CRITICAL_BLACKLIST, tag)
            remove_tag_from_list_file(archive_paths.BLACKLIST, tag)
        except OSError as e:
            messagebox.showerror("Error", f"Could not update the blacklist files: {e}", parent=self.master)
            return
        logging.info("Moved tag %r to the critical blacklist", tag)
        conn, e621_config = self.conn, self.e621_config
        self._rescan(f"Moved '{tag}' to the critical blacklist. Deleting its posts and rescanning…",
                     lambda: auto_delete_critical_posts(conn, e621_config, [tag]))

    def _rescan(self, status, before):
        """Run ``before()`` (returns a deleted count) then re-collect the review posts, off the Tk thread."""
        self.stop_video()
        self.busy = True
        for button in (self.prev_button, self.keep_button, self.delete_button,
                       self.unblacklist_button, self.critical_button, self.ignore_button):
            button.config(state="disabled")
        self.progress_label.config(text=status)
        conn, kept_file = self.conn, self.kept_file

        def work():
            deleted = before()
            return deleted, collect_review_posts(conn, kept_file)

        def done(result):
            deleted, posts = result
            self.busy = False
            self.deleted_count += deleted
            self.posts = posts
            self.current_index = 0  # everything left is unreviewed (kept posts are filtered out)
            self.navigation_history = []
            self.keep_button.config(state="normal")
            self.delete_button.config(state="normal")
            logging.info("Rescan complete: %d post(s) to review, %d deleted", len(posts), deleted)
            if self.skip_videos_var.get():
                self.skip_to_next_non_video()
            else:
                self.load_post()

        def failed(exc):
            self.busy = False
            logging.error("Rescan failed: %s", exc)
            messagebox.showerror("Rescan Failed", f"The list files were updated, but the rescan failed:\n\n{exc}",
                                 parent=self.master)
            self.keep_button.config(state="normal")
            self.delete_button.config(state="normal")
            self.load_post()

        tool_host.run_in_background(self.master, work, done, failed)

    def show_completion(self):
        """Show completion message."""
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2,
            self.canvas.winfo_height() // 2,
            text=f"Review Complete!\n\n"
                 f"Kept: {self.kept_count}\n"
                 f"Deleted: {self.deleted_count}",
            font=theme.font(20, "bold"),
            fill="white",
            justify="center"
        )

        self.tags_text.delete(1.0, tk.END)
        self.tags_text.insert(1.0, "Review complete!")

        self.keep_button.config(state="disabled")
        self.delete_button.config(state="disabled")
        self.progress_label.config(text=f"{len(self.posts)} post(s) reviewed")
        if self.tag_actions:
            self.tag_choice.config(values=[])
            self.tag_choice.set('')
            self.unblacklist_button.config(state="disabled")
            self.critical_button.config(state="disabled")
            self.ignore_button.config(state="disabled")

    def check_video_has_audio(self, video_path):
        """Check if video file has audio track."""
        if not HAS_PYGAME:
            return False

        try:
            cap = cv2.VideoCapture(video_path)
            # Unfortunately, OpenCV doesn't provide direct audio track detection
            # We'll try to load with pygame mixer as a fallback
            # For a more robust solution, ffprobe would be better
            cap.release()

            # Try to initialize audio with the video
            try:
                pygame.mixer.music.load(video_path)
                return True
            except pygame.error:
                return False
        except Exception as e:
            logging.error(f"Error checking video audio: {e}")
            return False

    def toggle_video_playback(self):
        """Toggle video playback (play/pause)."""
        if not self.is_video or not self.current_video_path:
            return

        if self.video_playing:
            self.pause_video()
        else:
            self.play_video()

    def play_video(self):
        """Start playing the video."""
        if self.video_playing or not self.current_video_path:
            return

        self.video_playing = True
        self.play_button.config(text="[PAUSE] Pause")

        # Start video playback in separate thread
        self.video_thread = threading.Thread(target=self._video_playback_loop, daemon=True)
        self.video_thread.start()

        # Start audio if not muted and video has audio
        if self.video_has_audio and not self.mute_var.get() and HAS_PYGAME:
            try:
                pygame.mixer.music.load(self.current_video_path)
                pygame.mixer.music.play()
            except Exception as e:
                logging.error(f"Error playing video audio: {e}")

    def pause_video(self):
        """Pause video playback."""
        self.video_playing = False
        self.play_button.config(text="> Play")

        # Stop audio
        if HAS_PYGAME:
            try:
                pygame.mixer.music.stop()
            except:
                pass

    def stop_video(self):
        """Stop video playback completely."""
        self.video_playing = False
        self.play_button.config(text="> Play", state="disabled")

        # Stop audio
        if HAS_PYGAME:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except:
                pass

        # Wait for thread to finish
        if self.video_thread and self.video_thread.is_alive():
            self.video_thread.join(timeout=1.0)

        # Close video capture
        if self.video_cap:
            self.video_cap.release()
            self.video_cap = None

        self.current_video_path = None
        self.video_has_audio = False
        self.audio_label.config(text="")

    def _video_playback_loop(self):
        """Internal method to play video frames in a loop."""
        try:
            cap = cv2.VideoCapture(self.current_video_path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_delay = 1.0 / fps

            while self.video_playing:
                ret, frame = cap.read()

                if not ret:
                    # End of video, loop back to start
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

                    # Restart audio if not muted
                    if self.video_has_audio and not self.mute_var.get() and HAS_PYGAME:
                        try:
                            pygame.mixer.music.play()
                        except:
                            pass
                    continue

                # Convert BGR to RGB
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)

                # Resize to fit canvas
                canvas_width = self.canvas.winfo_width() or 800
                canvas_height = self.canvas.winfo_height() or 600

                img_width, img_height = img.size
                scale = min(canvas_width / img_width, canvas_height / img_height)
                new_width = max(1, int(img_width * scale))
                new_height = max(1, int(img_height * scale))

                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Update canvas
                photo = ImageTk.PhotoImage(img)
                self.canvas.delete("all")
                self.canvas.create_image(canvas_width // 2, canvas_height // 2, image=photo, anchor="center")

                # Keep reference to prevent garbage collection
                self.current_photo = photo

                # Wait for next frame
                time.sleep(frame_delay)

            cap.release()

        except Exception as e:
            logging.error(f"Error in video playback: {e}")
            self.video_playing = False
            self.play_button.config(text="> Play")

    def on_mute_changed(self):
        """Handle mute checkbox change."""
        if not self.video_has_audio or not HAS_PYGAME:
            return

        if self.mute_var.get():
            # Mute
            try:
                pygame.mixer.music.set_volume(0.0)
            except:
                pass
        else:
            # Unmute
            try:
                pygame.mixer.music.set_volume(1.0)
            except:
                pass

    def quit_app(self):
        """Quit the application."""
        # Stop any playing video
        self.stop_video()

        if self.current_index < len(self.posts):
            result = messagebox.askyesno(
                "Quit",
                f"You have reviewed {self.current_index} of {len(self.posts)} posts.\n\n"
                f"Kept: {self.kept_count}\n"
                f"Deleted: {self.deleted_count}\n\n"
                f"Are you sure you want to quit?"
            )
            if not result:
                return

        logging.info(f"Session complete - Kept: {self.kept_count}, Deleted: {self.deleted_count}")
        self.master.quit()


def fetch_blacklisted_posts(conn, blacklist_tags):
    """Fetch posts with blacklisted tags from database."""
    cursor = conn.cursor()

    # Build query with placeholders
    placeholders = ', '.join(['%s'] * len(blacklist_tags))
    query = f"""
        SELECT DISTINCT p.id, p.file_name, p.file_path, p.file_url, p.rating,
               GROUP_CONCAT(DISTINCT t.name SEPARATOR ', ') as blacklisted_tags
        FROM posts p
        LEFT JOIN posts_tags pt ON pt.post_id = p.id
        LEFT JOIN tags t ON t.tag_id = pt.tag_id
        WHERE t.name IN ({placeholders})
        AND p.deleted = 0
        GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating
        ORDER BY p.id
    """

    cursor.execute(query, blacklist_tags)
    columns = [desc[0] for desc in cursor.description]
    posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()

    return posts


def filter_posts_with_accessible_images(posts):
    """Filter out posts that don't have accessible images (either local file or URL)."""
    filtered_posts = []
    skipped_count = 0

    for post in posts:
        file_path = post.get('file_path')
        file_url = post.get('file_url')

        # Check if we have a local file that exists
        has_local_file = file_path and os.path.exists(file_path)

        # Check if we have a URL
        has_url = file_url and file_url.strip() != ''

        if has_local_file or has_url:
            filtered_posts.append(post)
        else:
            skipped_count += 1
            logging.info(f"Skipping post {post['id']} - no accessible image (file_path: {file_path}, has_url: {bool(has_url)})")

    if skipped_count > 0:
        logging.info(f"Filtered out {skipped_count} posts without accessible images")

    return filtered_posts


def load_kept_posts(kept_file="kept_posts.txt"):
    """Load list of post IDs that have been marked as kept."""
    kept_posts = set()

    if not os.path.exists(kept_file):
        logging.info(f"Kept posts file not found: {kept_file}, starting fresh")
        return kept_posts

    try:
        with open(kept_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                # Try to parse as integer post ID
                try:
                    post_id = int(line)
                    kept_posts.add(post_id)
                except ValueError:
                    logging.warning(f"Invalid post ID in kept_posts.txt: {line}")

        logging.info(f"Loaded {len(kept_posts)} kept post IDs from {kept_file}")
        return kept_posts

    except Exception as e:
        logging.error(f"Error reading kept posts file: {e}")
        return kept_posts


def save_kept_post(post_id, kept_file="kept_posts.txt"):
    """Append a post ID to the kept posts file."""
    try:
        with open(kept_file, 'a', encoding='utf-8') as f:
            f.write(f"{post_id}\n")
        logging.info(f"Saved post {post_id} to kept posts file")
    except Exception as e:
        logging.error(f"Error saving kept post {post_id}: {e}")


def remove_kept_post(post_id, kept_file="kept_posts.txt"):
    """Remove a post ID from the kept posts file."""
    if not os.path.exists(kept_file):
        return

    try:
        # Read all lines
        with open(kept_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Write back all lines except the one with this post_id
        with open(kept_file, 'w', encoding='utf-8') as f:
            for line in lines:
                stripped = line.strip()
                # Keep comments and empty lines, remove matching post_id
                if stripped.startswith('#') or not stripped:
                    f.write(line)
                elif stripped != str(post_id):
                    f.write(line)

        logging.info(f"Removed post {post_id} from kept posts file")
    except Exception as e:
        logging.error(f"Error removing kept post {post_id}: {e}")


def filter_kept_posts(posts, kept_posts):
    """Filter out posts that have already been marked as kept."""
    if not kept_posts:
        return posts

    filtered_posts = [post for post in posts if post['id'] not in kept_posts]
    filtered_count = len(posts) - len(filtered_posts)

    if filtered_count > 0:
        logging.info(f"Filtered out {filtered_count} previously kept posts")

    return filtered_posts


def load_blacklist_tags(blacklist_file="blacklist.txt"):
    """Load blacklist tags from a text file."""
    blacklist_tags = []

    if not os.path.exists(blacklist_file):
        logging.error(f"Blacklist file not found: {blacklist_file}")
        messagebox.showerror("Error", f"Blacklist file not found: {blacklist_file}\n\nPlease create a blacklist.txt file with one tag per line.")
        return None

    try:
        with open(blacklist_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue

                # Handle multiple tags on one line (comma or space separated)
                if ',' in line:
                    tags = [tag.strip() for tag in line.split(',') if tag.strip()]
                    blacklist_tags.extend(tags)
                else:
                    blacklist_tags.append(line)

        # Remove duplicates while preserving order
        seen = set()
        unique_tags = []
        for tag in blacklist_tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)

        logging.info(f"Loaded {len(unique_tags)} blacklist tags from {blacklist_file}")
        return unique_tags

    except Exception as e:
        logging.error(f"Error reading blacklist file: {e}")
        messagebox.showerror("Error", f"Failed to read blacklist file: {e}")
        return None


def _read_list_lines(path):
    path = Path(path)
    if not path.exists():
        return []
    return path.read_text(encoding='utf-8').splitlines()


def remove_tag_from_list_file(path, tag):
    """Remove ``tag`` from a tag list file (one per line, or comma-separated); keeps comments.

    Returns True if the tag was found.
    """
    lines = _read_list_lines(path)
    found = False
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            out.append(line)
            continue
        if ',' in stripped:
            tags = [t.strip() for t in stripped.split(',') if t.strip()]
            kept = [t for t in tags if t != tag]
            if len(kept) != len(tags):
                found = True
                if kept:
                    out.append(', '.join(kept))
                continue
            out.append(line)
        elif stripped == tag:
            found = True
        else:
            out.append(line)
    if found:
        Path(path).write_text('\n'.join(out) + ('\n' if out else ''), encoding='utf-8')
    return found


def add_tag_to_list_file(path, tag):
    """Append ``tag`` to a tag list file unless it is already listed. Returns True if added."""
    path = Path(path)
    lines = _read_list_lines(path)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            if tag in (t.strip() for t in stripped.split(',')):
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines + [tag]) + '\n', encoding='utf-8')
    return True


def collect_review_posts(conn, kept_file):
    """Posts carrying blacklist.txt tags that have an image and weren't kept (no UI calls)."""
    blacklist_tags = load_blacklist_tags(str(archive_paths.BLACKLIST)) if archive_paths.BLACKLIST.exists() else []
    if not blacklist_tags:
        logging.info("Blacklist is empty - nothing to review")
        return []
    # Ignored tags don't count as hits; a post still shows if another blacklisted tag matches
    ignore_tags = set((load_blacklist_tags(str(archive_paths.BLACKLIST_IGNORE))
                       if archive_paths.BLACKLIST_IGNORE.exists() else None) or [])
    blacklist_tags = [t for t in blacklist_tags if t not in ignore_tags]
    if not blacklist_tags:
        logging.info("Every blacklist tag is ignored - nothing to review")
        return []
    logging.info(f"Fetching posts with {len(blacklist_tags)} blacklisted tags "
                 f"({len(ignore_tags)} tag(s) ignored via blacklist_ignore.txt)...")
    posts = fetch_blacklisted_posts(conn, blacklist_tags)
    logging.info(f"Found {len(posts)} posts with blacklisted tags")
    posts = filter_posts_with_accessible_images(posts)
    return filter_kept_posts(posts, load_kept_posts(kept_file))


def auto_delete_critical_posts(conn, e621_config, critical_tags):
    """Automatically delete posts with critical blacklist tags."""
    logging.info(f"Processing critical blacklist tags: {critical_tags}")

    # Fetch posts with critical tags
    posts = fetch_blacklisted_posts(conn, critical_tags)

    if not posts:
        logging.info("No posts found with critical blacklist tags")
        return 0

    logging.info(f"Found {len(posts)} posts with critical blacklist tags - auto-deleting...")

    # Unfavorite their FA sources while source_links still exist
    unfavorite_fa_sources(conn, [post['id'] for post in posts], e621_config)

    deleted_count = 0
    failed_count = 0

    for post in posts:
        post_id = post['id']
        try:
            # Delete file if it exists
            file_path = post.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    send2trash(file_path)
                    logging.info(f"Deleted file (sent to recycle bin): {file_path}")
                except Exception as e:
                    logging.error(f"Failed to delete file {file_path}: {e}")

            # Unfavorite on e621
            try:
                url = f"https://e621.net/favorites/{post_id}.json"
                auth = (e621_config.get("login"), e621_config.get("api_key"))

                response = requests.delete(url, auth=auth, headers={
                    "User-Agent": e621_config.get("user_agent", "e621 archive/1.0")
                })

                if response.status_code in [200, 204, 404, 422]:
                    logging.info(f"Unfavorited post {post_id} on e621")
                else:
                    logging.warning(f"Unexpected response unfavoriting post {post_id}: HTTP {response.status_code}")
            except Exception as e:
                logging.error(f"Failed to unfavorite post {post_id} on e621: {e}")

            # Delete from database
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM source_links WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_artists WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_tags WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
                conn.commit()
                logging.info(f"Auto-deleted post {post_id} from database (critical blacklist: {post['blacklisted_tags']})")
                deleted_count += 1
            except Exception as e:
                conn.rollback()
                logging.error(f"Failed to delete post {post_id} from database: {e}")
                failed_count += 1
            finally:
                cursor.close()

        except Exception as e:
            logging.error(f"Error auto-deleting post {post_id}: {e}")
            failed_count += 1

    logging.info(f"Critical blacklist processing complete - Deleted: {deleted_count}, Failed: {failed_count}")
    return deleted_count


def fetch_ai_tagged_posts_with_artists(conn, ai_tags):
    """Fetch posts with AI-related tags or AI artist names, including their artist names."""
    cursor = conn.cursor()

    placeholders = ', '.join(['%s'] * len(ai_tags))
    query = f"""
        SELECT DISTINCT p.id, p.file_name, p.file_path, p.file_url, p.rating,
               GROUP_CONCAT(DISTINCT t.name ORDER BY t.name SEPARATOR ', ') AS matched_tags,
               GROUP_CONCAT(DISTINCT a.name ORDER BY a.name SEPARATOR ', ') AS artist_names
        FROM posts p
        LEFT JOIN posts_tags pt ON pt.post_id = p.id
        LEFT JOIN tags t ON t.tag_id = pt.tag_id
        LEFT JOIN posts_artists pa ON pa.post_id = p.id
        LEFT JOIN artists a ON a.artist_id = pa.artist_id
        WHERE (t.name IN ({placeholders}) OR a.name IN ({placeholders}))
        AND p.deleted = 0
        GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating
        ORDER BY p.id
    """

    cursor.execute(query, ai_tags + ai_tags)
    columns = [desc[0] for desc in cursor.description]
    posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()

    return posts


def _artist_from_filename(file_name):
    """Parse the artist portion from a file_name like '{artist}-{rating}-{id}.ext'."""
    if not file_name:
        return None
    base = os.path.splitext(file_name)[0]  # strip extension
    parts = base.rsplit('-', 2)             # split off rating + post_id from right
    if len(parts) == 3 and parts[0] and parts[0] != 'unknown':
        return parts[0]
    return None


def log_ai_artists(conn, posts):
    """Record artist names from AI-tagged posts in ai_flagged_artists."""
    artists_to_log = set()
    for post in posts:
        raw = post.get('artist_names') or ''
        db_artists = {name.strip() for name in raw.split(', ') if name.strip()}
        if db_artists:
            artists_to_log.update(db_artists)
        else:
            # Fallback: parse artist from file_name when posts_artists links are missing
            fallback = _artist_from_filename(post.get('file_name'))
            if fallback:
                artists_to_log.add(fallback)

    if not artists_to_log:
        logging.info("No artist names to log for AI-tagged posts")
        return

    record_ai_flagged_artists(conn, artists_to_log)
    logging.info("Flagged %d AI artist(s): %s", len(artists_to_log), ', '.join(sorted(artists_to_log)))


def auto_delete_ai_tagged_posts(conn, e621_config, ai_tags):
    """Unfavorite and delete posts matching AI tags, logging their artists."""
    logging.info("Processing AI tags: %s", ai_tags)

    posts = fetch_ai_tagged_posts_with_artists(conn, ai_tags)

    if not posts:
        logging.info("No posts found with AI tags")
        return 0

    logging.info("Found %d posts with AI tags - processing...", len(posts))

    # Log artists and unfavorite their FA sources before deleting
    log_ai_artists(conn, posts)
    unfavorite_fa_sources(conn, [post['id'] for post in posts], e621_config)

    deleted_count = 0
    failed_count = 0

    for post in posts:
        post_id = post['id']
        try:
            # Move file to AI folder
            file_path = post.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    ai_dest_dir = archive_paths.ai_tag_directory()  # fff: config archive.ai_cleanup
                    os.makedirs(ai_dest_dir, exist_ok=True)
                    dest = os.path.join(ai_dest_dir, os.path.basename(file_path))
                    shutil.move(file_path, dest)
                    logging.info("Moved file to AI folder: %s -> %s", file_path, dest)
                except Exception as e:
                    logging.error("Failed to move file %s: %s", file_path, e)

            # Unfavorite on e621
            try:
                url = f"https://e621.net/favorites/{post_id}.json"
                auth = (e621_config.get("login"), e621_config.get("api_key"))
                response = requests.delete(url, auth=auth, headers={
                    "User-Agent": e621_config.get("user_agent", "e621 archive/1.0")
                })
                if response.status_code in [200, 204, 404, 422]:
                    logging.info("Unfavorited post %s on e621", post_id)
                else:
                    logging.warning("Unexpected response unfavoriting post %s: HTTP %s", post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to unfavorite post %s on e621: %s", post_id, e)

            # Downvote on e621
            try:
                vote_url = f"https://e621.net/posts/{post_id}/votes.json"
                response = requests.post(vote_url, auth=auth, headers={
                    "User-Agent": e621_config.get("user_agent", "e621 archive/1.0")
                }, data={"score": -1, "no_unvote": "false"})
                if response.status_code in [200, 201]:
                    logging.info("Downvoted post %s on e621", post_id)
                else:
                    logging.warning("Unexpected response downvoting post %s: HTTP %s", post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to downvote post %s on e621: %s", post_id, e)

            # Delete from database
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM source_links WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_artists WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_tags WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts WHERE id = %s", (post_id,))
                conn.commit()
                logging.info("Deleted post %s from database (AI tags: %s)", post_id, post.get('matched_tags'))
                deleted_count += 1
            except Exception as e:
                conn.rollback()
                logging.error("Failed to delete post %s from database: %s", post_id, e)
                failed_count += 1
            finally:
                cursor.close()

        except Exception as e:
            logging.error("Error processing AI-tagged post %s: %s", post_id, e)
            failed_count += 1

    logging.info("AI tag processing complete - Deleted: %d, Failed: %d", deleted_count, failed_count)
    return deleted_count


class ReviewSetupError(Exception):
    """Preparing a review failed in a way the user should see (no DB, no blacklist, ...)."""


def load_review_keybindings():
    keybinds_path = archive_paths.KEYBINDS
    if keybinds_path.exists():
        return load_keybindings(str(keybinds_path))
    # Fall back to root directory for backwards compatibility
    return load_keybindings("keybinds.yaml")


def prepare_review() -> dict:
    """Connect, run the critical/AI auto-deletes and collect the posts to review.

    Runs off the Tk thread (no widgets or message boxes here). Returns the connection, the
    posts and the summary counts; the caller owns (and must close) ``conn``.
    """
    setup_logging(str(archive_paths.LOG_DIR))
    logging.info("Starting Blacklist Review UI")

    config = load_config()
    mysql_config = config.get("mysql", {})
    e621_config = config.get("e621", {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        raise ReviewSetupError("Failed to connect to database")
    try:
        # Load critical blacklist tags and auto-delete
        critical_blacklist_tags = load_blacklist_tags(str(archive_paths.CRITICAL_BLACKLIST))
        auto_deleted_count = 0
        if critical_blacklist_tags:
            logging.info(f"Found {len(critical_blacklist_tags)} critical blacklist tags")
            auto_deleted_count = auto_delete_critical_posts(conn, e621_config, critical_blacklist_tags)
        else:
            logging.info("No critical blacklist tags found (critical_blacklist.txt not found or empty)")

        # Load AI tags and auto-delete, logging artists
        ai_tags = load_blacklist_tags(str(archive_paths.AI_TAGS))
        ai_deleted_count = 0
        if ai_tags:
            logging.info("Found %d AI tags", len(ai_tags))
            ai_deleted_count = auto_delete_ai_tagged_posts(conn, e621_config, ai_tags)
        else:
            logging.info("No AI tags found (ai_tags.txt not found or empty)")

        # Load regular blacklist tags from file
        blacklist_tags = load_blacklist_tags(str(archive_paths.BLACKLIST))
        if not blacklist_tags:
            logging.error("No blacklist tags loaded")
            raise ReviewSetupError("No blacklist tags found in config/blacklist.txt")

        # Posts with blacklisted tags, minus ignored-tag posts, image-less posts and kept posts
        posts = collect_review_posts(conn, str(archive_paths.KEPT_POSTS))
        kept_posts = load_kept_posts(str(archive_paths.KEPT_POSTS))
    except BaseException:
        conn.close()
        raise

    logging.info(f"Showing {len(posts)} posts with accessible images")
    if auto_deleted_count > 0:
        logging.info(f"Note: {auto_deleted_count} posts were auto-deleted from critical blacklist before UI")
    return {"conn": conn, "e621_config": e621_config, "posts": posts, "kept_posts": kept_posts,
            "auto_deleted": auto_deleted_count, "ai_deleted": ai_deleted_count,
            "keybinds": load_review_keybindings()}


def build(host):
    """Prepare in the background, then show the review in ``host`` (ToolHost or Tk root)."""
    loading = tool_host.message_panel(
        host, "Preparing blacklist review",
        "Connecting to the archive, removing critical/AI-tagged posts and collecting posts to review…",
        busy=True)

    def failed(exc):
        loading.destroy()
        logging.error("Blacklist review could not start: %s", exc)
        tool_host.message_panel(host, "Blacklist review could not start", str(exc), tone="warning",
                                actions=(("Close", lambda: tool_host.close(host)),))

    def ready(prepared):
        loading.destroy()
        conn = prepared["conn"]
        tool_host.on_destroy(host, conn.close)
        posts = prepared["posts"]
        if not posts:
            message = "No posts found with blacklisted tags and accessible images!"
            if prepared["auto_deleted"] > 0:
                message += f"\n\n{prepared['auto_deleted']} posts were auto-deleted from critical blacklist."
            if prepared["kept_posts"]:
                message += f"\n\n{len(prepared['kept_posts'])} previously kept posts were filtered out."
            tool_host.message_panel(host, "No posts to review", message,
                                    actions=(("Close", lambda: tool_host.close(host)),))
            return
        BlacklistReviewUI(host, posts, conn, prepared["e621_config"], prepared["keybinds"],
                          kept_file=str(archive_paths.KEPT_POSTS), tag_actions=True)
        if prepared["auto_deleted"] > 0:
            messagebox.showinfo(
                "Critical Blacklist Processed",
                f"{prepared['auto_deleted']} posts with critical blacklist tags were automatically deleted.\n\n"
                f"Now showing {len(posts)} posts for manual review.", parent=host)

    tool_host.run_in_background(host, prepare_review, ready, failed)


def main(argv=None):  # fff: argv is ignored (no options); lets the CLI call it uniformly
    root = tk.Tk()
    theme.apply(root)
    root.title("Blacklist Review UI")
    root.geometry("1200x900")
    build(root)
    root.mainloop()
    try:
        root.destroy()  # quit_app only stops the loop; this closes the connection (on_destroy)
    except tk.TclError:
        pass
    logging.info("Application closed")


if __name__ == "__main__":
    main()
