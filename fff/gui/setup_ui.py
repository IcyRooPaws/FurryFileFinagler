"""Guided first-run configuration and environment checks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from fff.common import paths


def _read_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return data


def _merge_defaults(defaults: dict, current: dict) -> dict:
    merged = dict(defaults)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_defaults(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_documents(config_path: str | Path | None = None) -> tuple[Path, Path, dict, dict]:
    """Load existing files, falling back to the committed examples on first run."""
    config_path = Path(config_path) if config_path else paths.CONFIG_DIR / "config.yaml"
    secrets_path = config_path.with_name("secrets.yaml")
    config_example = paths.CONFIG_DIR / "config.example.yaml"
    secrets_example = paths.CONFIG_DIR / "secrets.example.yaml"
    config = _merge_defaults(_read_yaml(config_example), _read_yaml(config_path)) if config_path.exists() \
        else _read_yaml(config_example)
    secrets = _merge_defaults(_read_yaml(secrets_example), _read_yaml(secrets_path)) if secrets_path.exists() \
        else _read_yaml(secrets_example)
    return config_path, secrets_path, config, secrets


def _flatten(data: dict, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    fields = []
    for key, value in data.items():
        path = prefix + (str(key),)
        if isinstance(value, dict) and value:
            fields.extend(_flatten(value, path))
        else:
            fields.append((path, value))
    return fields


def _set_path(data: dict, path: tuple[str, ...], value: Any) -> None:
    section = data
    for key in path[:-1]:
        section = section.setdefault(key, {})
    section[path[-1]] = value


def _display_value(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip()
    return "" if value is None else str(value)


def _parse_value(raw: str, original: Any) -> Any:
    if original is None:
        return raw if raw else None
    if isinstance(original, bool):
        value = raw if isinstance(raw, bool) else yaml.safe_load(raw)
        if not isinstance(value, bool):
            raise ValueError("Enter true or false")
        return value
    if isinstance(original, int) and not isinstance(original, bool):
        return int(raw)
    if isinstance(original, float):
        return float(raw)
    if isinstance(original, (list, dict)):
        value = yaml.safe_load(raw)
        if not isinstance(value, type(original)):
            raise ValueError(f"Expected a YAML {type(original).__name__}")
        return value
    return raw


def _collect_documents(documents: dict, editors: dict) -> dict:
    result = yaml.safe_load(yaml.safe_dump(documents, sort_keys=False)) or {}
    for path, editor in editors.items():
        if editor[0] == "text":
            raw = editor[1].get("1.0", "end-1c")
        elif editor[0] == "bool":
            raw = editor[1].get()
        else:
            raw = editor[1].get()
        _set_path(result, path, _parse_value(raw, editor[2]))
    return result


def save_documents(config_path: Path, secrets_path: Path, config: dict, secrets: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")
    secrets_path.write_text(yaml.safe_dump(secrets, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main(config_path: str | Path | None = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:
        config_path, secrets_path, config, secrets = load_documents(config_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"Could not load setup configuration: {exc}")
        return 2

    root = tk.Tk()
    root.title("FurryFileFinagler Setup")
    root.geometry("1100x780")
    root.minsize(820, 600)
    palette = {"page": "#F1F6F6", "surface": "#FFFFFF", "ink": "#18333A",
               "muted": "#61777C", "accent": "#087F8C", "accent_hover": "#066A75",
               "line": "#D6E2E3", "ok": "#18794E", "warning": "#A45B08", "error": "#B42318"}
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=palette["page"])
    style.configure("TFrame", background=palette["page"])
    style.configure("TLabel", background=palette["page"], foreground=palette["ink"],
                    font=("Segoe UI", 10))
    style.configure("Muted.TLabel", foreground=palette["muted"], font=("Segoe UI", 9))
    style.configure("PageTitle.TLabel", font=("Segoe UI Semibold", 21), foreground=palette["ink"])
    style.configure("TNotebook", background=palette["page"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 9), font=("Segoe UI Semibold", 10))
    style.map("TNotebook.Tab", foreground=[("selected", palette["accent"])])
    style.configure("Primary.TButton", background=palette["accent"], foreground="#FFFFFF",
                    padding=(14, 9), font=("Segoe UI Semibold", 10), borderwidth=0)
    style.map("Primary.TButton", background=[("active", palette["accent_hover"])])
    style.configure("Secondary.TButton", padding=(12, 8), font=("Segoe UI", 10))
    style.configure("TLabelframe", background=palette["surface"], bordercolor=palette["line"],
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=palette["surface"], foreground=palette["ink"],
                    font=("Segoe UI Semibold", 10))

    ttk.Label(root, text="FurryFileFinagler Setup", style="PageTitle.TLabel").pack(
        anchor="w", padx=18, pady=(16, 2))
    ttk.Label(root, text="Configure connections, folders, and credentials, then run first-time checks.",
              style="Muted.TLabel").pack(
        anchor="w", padx=18, pady=(0, 12))

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=18)
    config_editors: dict[tuple[str, ...], tuple[str, Any, Any]] = {}
    secrets_editors: dict[tuple[str, ...], tuple[str, Any, Any]] = {}
    secret_entries = []
    show_secrets = tk.BooleanVar(value=False)

    def update_secret_visibility() -> None:
        mask = "" if show_secrets.get() else "*"
        for entry in secret_entries:
            entry.configure(show=mask)

    def add_document(title: str, document: dict, section_names: list[str], editors: dict,
                     secret: bool = False) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text=title)
        canvas = tk.Canvas(tab, highlightthickness=0, background=palette["page"])
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=18)
        content.bind("<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        content.columnconfigure(1, weight=1)

        row = 0
        if secret:
            ttk.Checkbutton(content, text="Show credential values", variable=show_secrets,
                            command=update_secret_visibility).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(0, 10))
            row += 1
        for section_name in section_names:
            if section_name not in document:
                continue
            section = document[section_name]
            ttk.Label(content, text=section_name.replace("_", " ").title(),
                      font=("Segoe UI Semibold", 11), foreground=palette["accent"]).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(12, 4))
            row += 1
            for path, value in _flatten({section_name: section}):
                key = path[-1].lower()
                field_label = path[-1].replace("_", " ").capitalize()
                ttk.Label(content, text=field_label).grid(
                    row=row, column=0, sticky="nw", padx=(0, 14), pady=6)
                ttk.Label(content, text=".".join(path), style="Muted.TLabel").grid(
                    row=row + 1, column=0, sticky="nw", padx=(0, 14), pady=(0, 6))
                if isinstance(value, bool):
                    variable = tk.BooleanVar(value=value)
                    ttk.Checkbutton(content, text="Enabled", variable=variable).grid(
                        row=row, column=1, sticky="w", pady=5)
                    editors[path] = ("bool", variable, value)
                elif isinstance(value, (list, dict)):
                    widget = tk.Text(content, height=min(max(len(_display_value(value).splitlines()), 2), 7),
                                     wrap="word", relief="solid", borderwidth=1, padx=8, pady=6,
                                     background=palette["surface"], foreground=palette["ink"],
                                     font=("Cascadia Mono", 9))
                    widget.insert("1.0", _display_value(value))
                    widget.grid(row=row, column=1, rowspan=2, sticky="ew", pady=5)
                    editors[path] = ("text", widget, value)
                else:
                    sensitive = secret and key in {"password", "api_key", "login"}
                    widget = ttk.Entry(content, show="*" if sensitive and not show_secrets.get() else "")
                    widget.insert(0, _display_value(value))
                    widget.grid(row=row, column=1, rowspan=2, sticky="ew", pady=5)
                    editors[path] = ("entry", widget, value)
                    if sensitive:
                        secret_entries.append(widget)
                    if key.endswith(("directory", "folder")) or key == "directory":
                        def browse_folder(target=widget):
                            selected = filedialog.askdirectory(parent=root)
                            if selected:
                                target.delete(0, "end")
                                target.insert(0, selected)
                        ttk.Button(content, text="Browse…", style="Secondary.TButton",
                                   command=browse_folder).grid(row=row, column=2, rowspan=2,
                                                              padx=(8, 0), pady=5)
                row += 2

    add_document("Connections", config, ["environment", "test_paths_root", "database", "databases", "e621"],
                 config_editors)
    add_document("Source Search", config, ["fluffle"], config_editors)
    add_document("Archive", config, ["archive"], config_editors)
    add_document("Library Sorting", config, ["sorter"], config_editors)
    add_document("Credentials", secrets, list(secrets), secrets_editors, secret=True)
    extra_sections = [key for key in config if key not in {
        "environment", "test_paths_root", "database", "databases", "e621", "fluffle", "archive", "sorter"}]
    if extra_sections:
        add_document("More Settings", config, extra_sections, config_editors)

    results = tk.Text(root, height=9, state="disabled", wrap="word")
    results.pack(fill="x", padx=18, pady=(10, 4))
    include_db = tk.BooleanVar(value=False)
    ttk.Checkbutton(root, text="Include read-only database connectivity checks", variable=include_db).pack(
        anchor="w", padx=18)

    def report(lines: list[str]) -> None:
        results.configure(state="normal")
        results.delete("1.0", "end")
        results.insert("end", "\n".join(lines))
        results.configure(state="disabled")

    def collect_and_save() -> tuple[dict, dict] | None:
        try:
            saved_config = _collect_documents(config, config_editors)
            saved_secrets = _collect_documents(secrets, secrets_editors)
            save_documents(config_path, secrets_path, saved_config, saved_secrets)
            from fff.common import config as fff_config
            fff_config.load(config_path, secrets_path, reload=True)
            return saved_config, saved_secrets
        except (OSError, ValueError, yaml.YAMLError) as exc:
            messagebox.showerror("Setup", str(exc), parent=root)
            return None

    def save() -> None:
        if collect_and_save() is not None:
            report([f"Saved {config_path}", f"Saved {secrets_path}"])

    def run_checks() -> None:
        loaded = collect_and_save()
        if loaded is None:
            return
        from fff.cli import doctor
        from fff.common import config as fff_config

        cfg, secret_values = loaded
        cfg = fff_config.deep_merge(cfg, secret_values)
        problems = 0
        lines = ["Python packages"]
        for name, status in doctor.check_modules():
            lines.append(f"  {status}: {name}")
            problems += status.startswith(doctor.MISSING)
        lines.append("\nConfigured folders")
        seen = set()
        for label, folder in doctor.configured_directories(cfg):
            if (label, folder) in seen:
                continue
            seen.add((label, folder))
            exists = Path(folder).exists()
            lines.append(f"  {'OK' if exists else 'MISSING'}: {folder} [{label}]")
            problems += not exists
        if include_db.get():
            lines.append("\nDatabases")
            for module, name, status in doctor.check_databases(cfg):
                lines.append(f"  {module}: {name}: {status}")
                problems += not status.startswith(doctor.OK)
            view = doctor.check_all_files_view(cfg)
            lines.append(f"  all_files view: {view}")
            problems += view != doctor.OK
        lines.append(f"\n{problems} problem(s) found." if problems else "\nAll checks passed.")
        report(lines)

    buttons = ttk.Frame(root, padding=(18, 6, 18, 16))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Save settings", command=save).pack(side="left")
    ttk.Button(buttons, text="Save and run checks", command=run_checks).pack(side="left", padx=8)
    ttk.Button(buttons, text="Close", command=root.destroy).pack(side="right")
    root.mainloop()
    return 0