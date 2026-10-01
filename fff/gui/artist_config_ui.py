# fff: implemented in media sorter/scripts/artist_config_ui.py.
# fff: edits config/sort_artists.yaml (raw read, it is saved back); __main__ became parse_args + main.
"""
Artist Configuration UI

A Tkinter GUI for editing the artists.yaml configuration file.
Allows adding, editing, deleting, sorting, and reordering artists without
manually editing YAML.

Usage:
    python scripts/artist_config_ui.py
    python main.py artist-config
"""

import os
import sys  # noqa: F401  (fff: only the removed sys.path hack used it)
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from copy import deepcopy
from typing import Optional

import yaml

from fff.sorter import paths as sorter_paths

logger = logging.getLogger(__name__)

# fff: config/sort_artists.yaml (was configs/artists.yaml, decision 3). This editor writes the
# file back, so it is read RAW (_load_yaml), never through sorter_paths.load_yaml_for_env.
DEFAULT_CONFIG_PATH = sorter_paths.SORT_ARTISTS


def _load_yaml(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    return data


def _save_yaml(path: Path, data: dict):
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _artist_to_editor_record(canonical_name: str, value) -> dict:
    if isinstance(value, dict):
        return {
            'canonical_name': canonical_name,
            'aliases': [str(alias) for alias in value.get('aliases', [])],
            'target_directory': str(value.get('target_directory') or ''),
            'enabled': bool(value.get('enabled', True)),
            'legacy_format': False,
        }

    aliases = value if isinstance(value, list) else []
    return {
        'canonical_name': canonical_name,
        'aliases': [str(alias) for alias in aliases],
        'target_directory': '',
        'enabled': True,
        'legacy_format': True,
    }


def _editor_record_to_yaml_value(record: dict):
    aliases = [str(alias).strip() for alias in record.get('aliases', []) if str(alias).strip()]
    target_directory = str(record.get('target_directory') or '').strip()
    enabled = bool(record.get('enabled', True))
    legacy_format = bool(record.get('legacy_format', False))

    if legacy_format and enabled and not target_directory:
        return aliases

    value = {
        'aliases': aliases,
        'enabled': enabled,
    }
    if target_directory:
        value['target_directory'] = target_directory
    return value


def _alias_summary(record: dict) -> str:
    aliases = record.get('aliases') or []
    return ', '.join(str(alias) for alias in aliases) if aliases else '(no aliases)'


def _format_label(record: dict) -> str:
    return 'Legacy' if record.get('legacy_format') else 'Advanced'


class ArtistEditorDialog(tk.Toplevel):
    """Modal dialog for adding or editing a single artist entry."""

    def __init__(self, parent, artist: Optional[dict] = None,
                 existing_names: Optional[list] = None, title: str = 'Edit Artist'):
        super().__init__(parent)
        self.title(title)
        self.resizable(True, True)
        self.result: Optional[dict] = None
        self._existing_names = {name.lower() for name in (existing_names or [])}
        self._original_name = ''

        self._artist = deepcopy(artist) if artist else {
            'canonical_name': '',
            'aliases': [],
            'target_directory': '',
            'enabled': True,
            'legacy_format': True,
        }
        self._original_name = str(self._artist.get('canonical_name') or '')

        self._build_ui()
        self._populate_ui()
        self._update_format_state()

        self.transient(parent)
        self.grab_set()
        self.geometry('700x560')
        self.minsize(560, 460)

        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = px + (pw - self.winfo_width()) // 2
        y = py + (ph - self.winfo_height()) // 2
        self.geometry(f'+{x}+{y}')

        self.wait_window()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        form = ttk.Frame(self, padding=10)
        form.grid(row=0, column=0, sticky='ew')
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text='Canonical Name').grid(row=0, column=0, sticky='w', padx=(0, 8), pady=(0, 6))
        self._var_name = tk.StringVar()
        ttk.Entry(form, textvariable=self._var_name).grid(row=0, column=1, sticky='ew', pady=(0, 6))

        options = ttk.LabelFrame(self, text='Artist Options', padding=8)
        options.grid(row=1, column=0, sticky='ew', padx=10, pady=(0, 8))

        self._var_enabled = tk.BooleanVar()
        self._var_legacy = tk.BooleanVar()
        ttk.Checkbutton(options, text='Enabled', variable=self._var_enabled).grid(
            row=0, column=0, sticky='w', padx=(0, 20))
        ttk.Checkbutton(options, text='Use legacy/simple format when possible',
                        variable=self._var_legacy,
                        command=self._update_format_state).grid(row=0, column=1, sticky='w')

        alias_frame = ttk.LabelFrame(self, text='Aliases', padding=8)
        alias_frame.grid(row=2, column=0, sticky='nsew', padx=10, pady=(0, 8))
        alias_frame.columnconfigure(0, weight=1)
        alias_frame.rowconfigure(0, weight=1)

        self._aliases_lb = tk.Listbox(alias_frame, height=10, selectmode='extended', font=('TkDefaultFont', 9))
        self._aliases_lb.grid(row=0, column=0, sticky='nsew')

        alias_scroll = ttk.Scrollbar(alias_frame, orient='vertical', command=self._aliases_lb.yview)
        self._aliases_lb.configure(yscrollcommand=alias_scroll.set)
        alias_scroll.grid(row=0, column=1, sticky='ns')

        alias_controls = ttk.Frame(alias_frame)
        alias_controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        alias_controls.columnconfigure(0, weight=1)

        self._alias_entry = ttk.Entry(alias_controls)
        self._alias_entry.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self._alias_entry.bind('<Return>', lambda e: self._add_alias())

        ttk.Button(alias_controls, text='Add', command=self._add_alias).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(alias_controls, text='Remove', command=self._remove_selected_aliases).grid(row=0, column=2)

        target_frame = ttk.LabelFrame(self, text='Custom Target Directory', padding=8)
        target_frame.grid(row=3, column=0, sticky='ew', padx=10, pady=(0, 8))
        target_frame.columnconfigure(0, weight=1)

        self._var_target = tk.StringVar()
        self._target_entry = ttk.Entry(target_frame, textvariable=self._var_target)
        self._target_entry.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        self._browse_btn = ttk.Button(target_frame, text='Browse...', command=self._browse_dir)
        self._browse_btn.grid(row=0, column=1)

        self._format_hint = ttk.Label(
            target_frame,
            text='Legacy format saves only aliases. Advanced format saves aliases plus enabled/target settings.',
            foreground='gray'
        )
        self._format_hint.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=4, column=0, sticky='ew', padx=10, pady=(0, 10))
        btn_frame.columnconfigure(0, weight=1)

        ttk.Button(btn_frame, text='OK', command=self._on_ok).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(btn_frame, text='Cancel', command=self.destroy).grid(row=0, column=2)

    def _populate_ui(self):
        self._var_name.set(self._artist.get('canonical_name', ''))
        self._var_enabled.set(bool(self._artist.get('enabled', True)))
        self._var_legacy.set(bool(self._artist.get('legacy_format', False)))
        self._var_target.set(self._artist.get('target_directory', ''))
        for alias in self._artist.get('aliases') or []:
            self._aliases_lb.insert(tk.END, str(alias))

    def _add_alias(self):
        raw = self._alias_entry.get().strip()
        if not raw:
            return
        existing = {str(alias).lower() for alias in self._aliases_lb.get(0, tk.END)}
        for alias in (part.strip() for part in raw.split(',') if part.strip()):
            if alias.lower() not in existing:
                self._aliases_lb.insert(tk.END, alias)
                existing.add(alias.lower())
        self._alias_entry.delete(0, tk.END)

    def _remove_selected_aliases(self):
        for idx in reversed(self._aliases_lb.curselection()):
            self._aliases_lb.delete(idx)

    def _browse_dir(self):
        current = self._var_target.get().strip()
        initial = current if os.path.isdir(current) else '/'
        chosen = filedialog.askdirectory(initialdir=initial, title='Select Artist Target Directory')
        if chosen:
            self._var_target.set(chosen.replace('/', os.sep))
            self._update_format_state(force_advanced=True)

    def _update_format_state(self, force_advanced: bool = False):
        if force_advanced:
            self._var_legacy.set(False)

        use_legacy = self._var_legacy.get()
        state = 'disabled' if use_legacy else 'normal'
        self._target_entry.configure(state=state)
        self._browse_btn.configure(state=state)
        if use_legacy:
            self._format_hint.configure(text='Legacy format stores only aliases. Target directory is disabled.')
        else:
            self._format_hint.configure(
                text='Advanced format stores aliases, enabled state, and an optional custom target directory.'
            )

    def _on_ok(self):
        canonical_name = self._var_name.get().strip()
        aliases = [str(alias).strip() for alias in self._aliases_lb.get(0, tk.END) if str(alias).strip()]
        target = self._var_target.get().strip() if not self._var_legacy.get() else ''

        if not canonical_name:
            messagebox.showwarning('Validation', 'Canonical name is required.', parent=self)
            return

        lowered = canonical_name.lower()
        if lowered in self._existing_names and lowered != self._original_name.lower():
            messagebox.showwarning('Validation', 'An artist with that canonical name already exists.', parent=self)
            return

        if not aliases:
            messagebox.showwarning('Validation', 'At least one alias is required.', parent=self)
            return

        self.result = {
            'canonical_name': canonical_name,
            'aliases': aliases,
            'target_directory': target,
            'enabled': self._var_enabled.get(),
            'legacy_format': self._var_legacy.get(),
        }
        self.destroy()


class ArtistConfigApp:
    """Main window for the artist configuration editor."""

    TREE_COLS = ('enabled', 'name', 'format', 'aliases', 'target')
    TREE_HEADINGS = ('On', 'Canonical Name', 'Format', 'Aliases', 'Target Directory')
    TREE_WIDTHS = (40, 180, 80, 360, 320)

    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.config_path = config_path
        self._raw_data: dict = {}
        self._artists: list = []
        self._dirty = False

        root.title(f'Artist Configuration - {config_path.name}')
        root.geometry('1180x720')
        root.minsize(860, 520)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_ui()
        self._load()

        root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=(6, 4))
        toolbar.grid(row=0, column=0, sticky='ew')

        ttk.Label(toolbar, text='Artist Configuration', font=('TkDefaultFont', 12, 'bold')).pack(
            side='left', padx=(0, 20))
        ttk.Button(toolbar, text='Save', command=self._save).pack(side='left', padx=(0, 4))
        ttk.Button(toolbar, text='Reload', command=self._reload).pack(side='left', padx=(0, 4))
        ttk.Button(toolbar, text='Sort A-Z', command=self._sort_artists).pack(side='left', padx=(0, 4))

        self._dirty_label = ttk.Label(toolbar, text='', foreground='red')
        self._dirty_label.pack(side='left', padx=(8, 0))

        body = ttk.LabelFrame(self.root, text='Artists', padding=8)
        body.grid(row=1, column=0, sticky='nsew', padx=8, pady=(2, 4))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        tree_frame = ttk.Frame(body)
        tree_frame.grid(row=0, column=0, sticky='nsew')
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(tree_frame, columns=self.TREE_COLS, show='headings', selectmode='browse')
        for col, heading, width in zip(self.TREE_COLS, self.TREE_HEADINGS, self.TREE_WIDTHS):
            self._tree.heading(col, text=heading)
            self._tree.column(col, width=width, minwidth=40,
                              stretch=(col in ('name', 'aliases', 'target')))

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')

        self._tree.bind('<Double-1>', lambda e: self._edit_artist())
        self._tree.tag_configure('disabled', foreground='grey')

        btn_frame = ttk.Frame(body)
        btn_frame.grid(row=0, column=1, sticky='n', padx=(8, 0))

        actions = [
            ('Add Artist', self._add_artist),
            ('Edit Artist', self._edit_artist),
            ('Duplicate', self._duplicate_artist),
            (None, None),
            ('Toggle On/Off', self._toggle_artist),
            (None, None),
            ('Move Up', self._move_up),
            ('Move Down', self._move_down),
            (None, None),
            ('Delete Artist', self._delete_artist),
        ]
        for label, cmd in actions:
            if label is None:
                ttk.Separator(btn_frame, orient='horizontal').pack(fill='x', pady=4)
            else:
                ttk.Button(btn_frame, text=label, command=cmd, width=14).pack(fill='x', pady=2)

        self._status_var = tk.StringVar(value='Ready')
        ttk.Label(self.root, textvariable=self._status_var, anchor='w', relief='sunken').grid(
            row=2, column=0, sticky='ew')

    def _load(self):
        try:
            self._raw_data = _load_yaml(self.config_path)
        except FileNotFoundError:
            self._raw_data = {}
        except Exception as e:
            messagebox.showerror('Load Error', f'Failed to load config:\n{e}')
            self._raw_data = {}

        self._artists = [
            _artist_to_editor_record(canonical_name, value)
            for canonical_name, value in self._raw_data.items()
        ]
        self._refresh_tree()
        self._set_dirty(False)
        self._status(f'Loaded {len(self._artists)} artists from {self.config_path}')

    def _reload(self):
        if self._dirty:
            if not messagebox.askyesno('Unsaved Changes', 'Discard unsaved changes and reload from file?'):
                return
        self._load()

    def _save(self):
        serialized = {}
        for artist in self._artists:
            serialized[artist['canonical_name']] = _editor_record_to_yaml_value(artist)

        try:
            _save_yaml(self.config_path, serialized)
            self._raw_data = serialized
            self._set_dirty(False)
            self._status(f'Saved {len(self._artists)} artists to {self.config_path}')
        except Exception as e:
            messagebox.showerror('Save Error', f'Failed to save config:\n{e}')

    def _refresh_tree(self, select_index: Optional[int] = None):
        self._tree.delete(*self._tree.get_children())
        for i, artist in enumerate(self._artists):
            enabled = artist.get('enabled', True)
            values = (
                'Y' if enabled else 'N',
                artist.get('canonical_name', ''),
                _format_label(artist),
                _alias_summary(artist),
                artist.get('target_directory', ''),
            )
            self._tree.insert('', 'end', iid=str(i), values=values,
                              tags=() if enabled else ('disabled',))

        if select_index is not None and self._artists:
            idx = max(0, min(select_index, len(self._artists) - 1))
            self._tree.selection_set(str(idx))
            self._tree.see(str(idx))

    def _selected_index(self) -> Optional[int]:
        selection = self._tree.selection()
        return int(selection[0]) if selection else None

    def _mark_dirty(self):
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._dirty_label.config(text='● Unsaved changes' if dirty else '')

    def _status(self, message: str):
        self._status_var.set(message)

    def _existing_names(self, exclude_index: Optional[int] = None) -> list:
        names = []
        for idx, artist in enumerate(self._artists):
            if idx == exclude_index:
                continue
            names.append(str(artist.get('canonical_name', '')))
        return names

    def _add_artist(self):
        dlg = ArtistEditorDialog(self.root, artist=None, existing_names=self._existing_names(), title='Add Artist')
        if dlg.result:
            self._artists.append(dlg.result)
            self._refresh_tree(select_index=len(self._artists) - 1)
            self._mark_dirty()
            self._status(f"Added artist '{dlg.result['canonical_name']}'")

    def _edit_artist(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo('No Selection', 'Please select an artist to edit.')
            return
        dlg = ArtistEditorDialog(
            self.root,
            artist=self._artists[idx],
            existing_names=self._existing_names(exclude_index=idx),
            title='Edit Artist'
        )
        if dlg.result:
            self._artists[idx] = dlg.result
            self._refresh_tree(select_index=idx)
            self._mark_dirty()
            self._status(f"Updated artist '{dlg.result['canonical_name']}'")

    def _duplicate_artist(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo('No Selection', 'Please select an artist to duplicate.')
            return

        source = deepcopy(self._artists[idx])
        base_name = str(source.get('canonical_name') or 'Artist')
        candidate = f'{base_name} Copy'
        existing = {name.lower() for name in self._existing_names()}
        suffix = 2
        while candidate.lower() in existing:
            candidate = f'{base_name} Copy {suffix}'
            suffix += 1
        source['canonical_name'] = candidate

        insert_at = idx + 1
        self._artists.insert(insert_at, source)
        self._refresh_tree(select_index=insert_at)
        self._mark_dirty()
        self._status(f"Duplicated artist '{base_name}'")

    def _toggle_artist(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo('No Selection', 'Please select an artist to toggle.')
            return
        self._artists[idx]['enabled'] = not self._artists[idx].get('enabled', True)
        if not self._artists[idx]['enabled']:
            self._artists[idx]['legacy_format'] = False
        self._refresh_tree(select_index=idx)
        self._mark_dirty()
        state = 'enabled' if self._artists[idx]['enabled'] else 'disabled'
        self._status(f"Artist '{self._artists[idx]['canonical_name']}' {state}")

    def _move_up(self):
        idx = self._selected_index()
        if idx is None or idx == 0:
            return
        self._artists[idx - 1], self._artists[idx] = self._artists[idx], self._artists[idx - 1]
        self._refresh_tree(select_index=idx - 1)
        self._mark_dirty()

    def _move_down(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self._artists) - 1:
            return
        self._artists[idx + 1], self._artists[idx] = self._artists[idx], self._artists[idx + 1]
        self._refresh_tree(select_index=idx + 1)
        self._mark_dirty()

    def _sort_artists(self):
        self._artists.sort(key=lambda artist: str(artist.get('canonical_name', '')).lower())
        self._refresh_tree(select_index=0)
        self._mark_dirty()
        self._status('Sorted artists alphabetically')

    def _delete_artist(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo('No Selection', 'Please select an artist to delete.')
            return
        artist = self._artists[idx]
        if not messagebox.askyesno(
            'Delete Artist',
            f"Delete artist '{artist.get('canonical_name', '')}'?\n\n{_alias_summary(artist)}"
        ):
            return
        self._artists.pop(idx)
        self._refresh_tree(select_index=idx)
        self._mark_dirty()
        self._status('Deleted artist entry')

    def _on_close(self):
        if self._dirty:
            answer = messagebox.askyesnocancel(
                'Unsaved Changes',
                'You have unsaved changes.\nSave before closing?'
            )
            if answer is None:
                return
            if answer:
                self._save()
        self.root.destroy()


def launch(config_path: Optional[Path] = None):
    """Launch the artist configuration UI."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    root = tk.Tk()
    try:
        root.tk.call('tk', 'scaling', 1.0)
    except Exception:
        pass

    ArtistConfigApp(root, Path(config_path))
    root.mainloop()


def parse_args(argv=None):  # fff: the original parsed args inside the __main__ block
    import argparse

    parser = argparse.ArgumentParser(description='Artist Configuration UI')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG_PATH), help='Path to artists.yaml')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    launch(config_path=Path(args.config))


if __name__ == '__main__':
    main()
