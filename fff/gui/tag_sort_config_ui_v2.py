# fff: edits config/tag_sorting.yaml (was configs/), read raw because it is saved back; config.yaml
# fff: is the unified config's sorter view; the e621 schema comes from the environment.
# fff: 'Apply Post Dates Now' reroutes the rule's folders in the test environment (_rule_for_env).
# fff: the __main__ block became parse_args(argv) + main(argv).
"""
Tag Sort Configuration UI — v2

A rebuilt Tkinter GUI for editing tag_sorting.yaml.
Uses a collapsible file-tree view (main rules → sub-rules → sub-sub-rules)
with a persistent right-side editor panel and full drag-and-drop support.

Usage:
    python scripts/tag_sort_config_ui_v2.py [--config PATH]
    python main.py tag-sort-config          (after Phase 6 wires this in)

Build phases:
    Phase 1 — Data layer  (this file, scaffold)
    Phase 2 — Tree rendering
    Phase 3 — Tree interaction
    Phase 4 — Edit panel
    Phase 5 — Drag and drop
    Phase 6 — Full app wiring & migration
"""

from __future__ import annotations

import os
import sys  # noqa: F401  (fff: only the removed sys.path hack used it)
import logging
import threading
import uuid
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import filedialog
from pathlib import Path
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional, List

import yaml

from fff.common import config as fff_config
from fff.gui import theme
from fff.sorter import paths as sorter_paths
from fff.sorter.config import DEFAULT_CONFIG_FILE

# ---------------------------------------------------------------------------
# Path / logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# fff: config/tag_sorting.yaml. This editor writes the file back, so it is always read RAW
# (_load_yaml), never through sorter_paths.load_yaml_for_env (test-mode rerouting).
DEFAULT_CONFIG_PATH = sorter_paths.TAG_SORTING

# ---------------------------------------------------------------------------
# Theme / colour constants  (fff: mapped onto the shared fff.gui.theme palette)
# ---------------------------------------------------------------------------

_P = theme.PALETTE
BG_COLOR       = _P['page']
BG_SURFACE     = _P['surface']
BG_HEADER      = _P['surface']
BG_SIDEBAR     = _P['page']
BG_PANEL       = _P['page']      # right edit-panel background
TEXT_COLOR     = _P['ink']
TEXT_DIM       = _P['muted']
TEXT_FAINT     = _P['faint']
TEXT_BRIGHT    = _P['ink']
ON_ACCENT      = '#FFFFFF'       # text on an accent-coloured fill
ACCENT         = _P['accent']
ACCENT_GREEN   = _P['ok']
ACCENT_RED     = _P['error']
ACCENT_YELLOW  = _P['warning']
ACCENT_PEACH   = _P['orange']
ACCENT_MAUVE   = _P['violet']
BORDER_COLOR   = _P['line']
ROW_SELECTED   = _P['selected']
ROW_HOVER      = _P['hover']
DROP_INTO_BG   = _P['ok_soft']   # "drop INTO" highlight
MIRROR_BG      = _P['ok_soft']
SCROLLBAR_BG   = _P['surface_alt']
SCROLLBAR_FG   = _P['line_strong']

# Rule type → (accent colour, badge bg, badge fg text)
TYPE_COLORS = {
    'AND':    (_P['info'], _P['info_soft'], _P['info']),
    'OR':     (_P['orange'], _P['orange_soft'], _P['orange']),
    'AND+OR': (_P['violet'], _P['violet_soft'], _P['violet']),
    'Legacy': (_P['warning'], _P['warning_soft'], _P['warning']),
    'POOL':   (_P['ok'], _P['ok_soft'], _P['ok']),
    '?':      (_P['muted'], _P['surface_alt'], _P['muted']),
}
ACCENT_TEAL = _P['ok']

# Tree layout constants
ROW_HEIGHT    = 34    # px per tree row
INDENT_PX     = 24   # px of extra indent per depth level
TRIANGLE_W    = 16   # px reserved for expand/collapse triangle
BADGE_PAD_X   = 6    # horizontal padding inside type badge

# ---------------------------------------------------------------------------
# ttk styling  (Notebook tabs + scrollbars in the edit panel and dialogs are
# pinned to the panel colours above so they sit flush with the tk widgets).
# ---------------------------------------------------------------------------

_panel_ttk_style_ready = False


def _apply_panel_ttk_style() -> None:
    """Configure the shared ttk.Style with the edit panel's Notebook/Scrollbar styles.

    Idempotent and safe to call from every widget that uses a 'Panel.*' style
    (RuleTreePanel, RuleEditPanel, _DemoteDialog) — whichever is constructed
    first performs the real setup, later calls are no-ops.
    """
    global _panel_ttk_style_ready
    if _panel_ttk_style_ready:
        return
    s = ttk.Style()
    try:
        s.theme_use('clam')
    except Exception:
        pass

    s.configure('Panel.TNotebook',
                background=BG_PANEL, borderwidth=0, tabmargins=[0, 0, 0, 0],
                bordercolor=BG_PANEL, darkcolor=BG_PANEL, lightcolor=BG_PANEL)
    s.configure('Panel.TNotebook.Tab',
                background=SCROLLBAR_BG, foreground=TEXT_DIM,
                padding=[12, 5], font=(theme.UI_FONT, 10), borderwidth=0,
                bordercolor=BG_PANEL, darkcolor=SCROLLBAR_BG, lightcolor=SCROLLBAR_BG)
    s.map('Panel.TNotebook.Tab',
          background=[('selected', BG_PANEL), ('active', ROW_HOVER)],
          foreground=[('selected', ACCENT), ('active', TEXT_COLOR)],
          bordercolor=[('selected', BG_PANEL)],
          lightcolor=[('selected', BG_PANEL)],
          darkcolor=[('selected', BG_PANEL)])

    s.configure('Panel.Vertical.TScrollbar',
                background=SCROLLBAR_FG, troughcolor=BG_COLOR,
                bordercolor=BG_COLOR, arrowcolor=TEXT_DIM,
                relief='flat', borderwidth=0, arrowsize=12)
    s.map('Panel.Vertical.TScrollbar',
          background=[('active', ACCENT), ('pressed', ACCENT)],
          arrowcolor=[('active', ON_ACCENT), ('pressed', ON_ACCENT)])

    _panel_ttk_style_ready = True

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


def _save_yaml(path: Path, data: dict) -> None:
    with open(path, 'w', encoding='utf-8') as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

# ---------------------------------------------------------------------------
# File-count helpers  (used to show "how many files live in this folder")
# ---------------------------------------------------------------------------

# fff: the existing code's configs/config.yaml; this value makes Config() and _load_main_config
# use the sorter view of the unified config (read-only here, so rerouting is fine).
DEFAULT_MAIN_CONFIG_PATH = DEFAULT_CONFIG_FILE


def _load_main_config(path: Path = DEFAULT_MAIN_CONFIG_PATH) -> dict:
    """Load the project's main config.yaml (best-effort; returns {} on any error)."""
    try:
        if str(path) == DEFAULT_CONFIG_FILE:
            return fff_config.legacy_view("sorter")  # fff: the unified config's sorter view
        return _load_yaml(path)
    except Exception:
        return {}


def get_global_base_dirs(tag_sorting_data: dict, main_config: Optional[dict] = None) -> dict:
    """Compute the pictures/videos/gifs base directories used for folder_name
    (split-mode) rules that have no hard-set ``target_directory``.

    Computes base directories using the sorter configuration:
    1. An explicit ``base_directories`` block in tag_sorting.yaml, if set.
    2. Otherwise derived from config.yaml's ``destination_directories`` +
       tag_sorting.yaml's ``tag_sort_subdirectory``.

    Also returns sfw_pictures/sfw_videos/sfw_gifs, which receive files rated
    'safe' (same priority, derived from the sfw_* destination directories).
    """
    bases = tag_sorting_data.get('base_directories', {}) or {}
    if main_config is None:
        main_config = _load_main_config()
    subdir = tag_sorting_data.get('tag_sort_subdirectory', '') or ''
    dest = main_config.get('destination_directories', {}) or {}

    def _join(base: str) -> str:
        if not base:
            return ''
        return os.path.join(base, subdir) if subdir else base

    if any(bases.get(k, '') for k in ('pictures', 'videos', 'gifs')):
        nsfw = {
            'pictures': bases.get('pictures', ''),
            'videos': bases.get('videos', ''),
            'gifs': bases.get('gifs', ''),
        }
    else:
        nsfw = {
            'pictures': _join(dest.get('nsfw_image', '')),
            'videos': _join(dest.get('nsfw_video', '')),
            'gifs': _join(dest.get('nsfw_gif', '')),
        }
    sfw = {
        'sfw_pictures': bases.get('sfw_pictures') or _join(dest.get('sfw_image', '')),
        'sfw_videos': bases.get('sfw_videos') or _join(dest.get('sfw_video', '')),
        'sfw_gifs': bases.get('sfw_gifs') or _join(dest.get('sfw_gif', '')),
    }
    return {**nsfw, **sfw}


def resolve_e621_db_config(tag_sorting_data: dict, main_config: Optional[dict] = None) -> dict:
    """Resolve e621 database connection settings:
    tag_sorting.yaml's ``e621_database`` block overrides config.yaml's ``mysql``
    block, field-by-field, with ``e621_posts_db`` as the default database name.
    """
    e621_db = tag_sorting_data.get('e621_database') or {}
    if main_config is None:
        main_config = _load_main_config()
    mysql_cfg = main_config.get('mysql', {}) or {}
    return {
        'host': e621_db.get('host') or mysql_cfg.get('host') or 'localhost',
        'port': int(e621_db.get('port') or mysql_cfg.get('port') or 3306),
        'user': e621_db.get('user') or mysql_cfg.get('user'),
        'password': e621_db.get('password') or mysql_cfg.get('password'),
        # fff: the environment decides the schema (prod or test_), as in fff.sorter.config
        'database': sorter_paths.db_names()['archive'],
    }


def fetch_pool_names(pool_ids: list, db_config: dict) -> dict:
    """Query the e621 database's ``pools`` table for pool_id -> name.

    Best-effort: raises on connection/query failure so the caller can surface
    the error, but never partially fails silently — either it returns whatever
    rows matched, or it raises.
    """
    from fff.common import db as fff_db

    ids = [int(p) for p in pool_ids if str(p).strip()]
    if not ids:
        return {}
    conn = fff_db.connect_with(
        db_config,
        database=db_config.get('database') or sorter_paths.db_names()['archive'],
        charset='utf8mb4', use_unicode=True,
    )
    try:
        cur = conn.cursor()
        placeholders = ', '.join(['%s'] * len(ids))
        cur.execute(f'SELECT pool_id, name FROM pools WHERE pool_id IN ({placeholders})', ids)
        return {pid: name for pid, name in cur.fetchall()}
    finally:
        conn.close()


def _rule_for_env(rule: dict) -> dict:
    """fff: a rule from the raw editor data, with its absolute folders rerouted under the
    test root in the test environment (what sorting code gets from load_yaml_for_env)."""
    try:
        cfg = fff_config.load()
    except fff_config.ConfigError:
        return rule
    return fff_config.reroute_paths(rule, cfg) if fff_config.is_test(cfg) else rule


def _split_mode_directories(rule: dict, base_dirs: dict) -> list:
    """For a depth-0 rule with no ``target_directory``, return the concrete
    per-media-type directories (pictures/videos/gifs) it could route files
    into, using ``folder_name`` + the resolved global base directories."""
    folder_name = (rule.get('folder_name') or '').strip()
    if not folder_name or not base_dirs:
        return []
    append_label = bool(rule.get('append_media_type_label', False))
    labels = {'pictures': 'Pics', 'videos': 'Vids', 'gifs': 'Gifs'}
    dirs = []
    for media_type, base in base_dirs.items():
        if not base:
            continue
        effective = folder_name
        if append_label:
            from pathlib import PurePosixPath
            parts = PurePosixPath(folder_name.replace('\\', '/')).parts
            label_key = media_type[len('sfw_'):] if media_type.startswith('sfw_') else media_type
            suffix = labels.get(label_key, label_key.capitalize())
            if parts:
                effective = os.path.join(*[f'{p} - {suffix}' for p in parts])
        dirs.append(os.path.join(base, effective))
    return dirs


def resolve_node_directories(node: Optional['RuleNode'], base_dirs: Optional[dict] = None) -> list:
    """
    Resolve the on-disk destination directory/directories for a rule node.

    Depth-0 rules use ``target_directory`` directly when hard-set; otherwise,
    if ``folder_name`` is set, the directories are auto-constructed from the
    project's config.yaml destination_directories (via ``base_dirs``), which
    can yield up to 3 paths (pictures/videos/gifs). Sub-rules append their
    ``sub_directory`` onto every one of their parent's resolved directories.
    Returns an empty list if nothing is resolvable.
    """
    if node is None:
        return []
    if node.depth == 0:
        target = (node.rule.get('target_directory') or '').strip()
        if target:
            return [target]
        return _split_mode_directories(node.rule, base_dirs or {})
    parent_dirs = resolve_node_directories(node.parent, base_dirs)
    sub_dir = (node.rule.get('sub_directory') or '').strip()
    if not parent_dirs or not sub_dir:
        return []
    return [os.path.join(p, sub_dir) for p in parent_dirs]


def _normalize_dir_key(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _count_key_for_dirs(dirs: list) -> Optional[str]:
    """Cache key for a node's resolved directory/directories (order-independent)."""
    valid = [d for d in dirs if d]
    if not valid:
        return None
    if len(valid) == 1:
        return _normalize_dir_key(valid[0])
    return '||'.join(sorted(_normalize_dir_key(d) for d in valid))


def _count_files_in_dir(path: str) -> Optional[int]:
    """Recursively count files under ``path``. Returns None if not a valid directory."""
    if not path or not os.path.isdir(path):
        return None
    total = 0
    try:
        for _root, _dirs, files in os.walk(path):
            total += len(files)
    except Exception:
        return None
    return total

# ---------------------------------------------------------------------------
# Sync Master helpers  (mirror a set of top-level rules under a "master" rule,
# e.g. mirroring every top-level tag category under a "Ferals" master with a
# "Feral " prefix, so both stay in sync without hand-copying tag blocks).
# ---------------------------------------------------------------------------

def ensure_rule_ids(rules: list) -> bool:
    """Recursively assign a stable ``rule_id`` to every rule/sub-rule missing one.

    Returns True if any ids were newly assigned (caller may want to mark dirty).
    """
    changed = False
    for rule in rules:
        if not rule.get('rule_id'):
            rule['rule_id'] = str(uuid.uuid4())
            changed = True
        sub = rule.get('sub_rules') or []
        if sub:
            changed = ensure_rule_ids(sub) or changed
    return changed


def find_rule_by_id(rules: list, rule_id: str) -> Optional[dict]:
    """Recursively search ``rules`` (and nested sub_rules) for a rule with this rule_id."""
    for rule in rules:
        if rule.get('rule_id') == rule_id:
            return rule
        found = find_rule_by_id(rule.get('sub_rules') or [], rule_id)
        if found is not None:
            return found
    return None


def _rule_own_tags(rule: dict) -> list:
    """The tag list that 'identifies' a rule, used both for display and for
    injecting exclusions into rules it mirrors."""
    return list(rule.get('needed_tags') or rule.get('can_have_tags') or rule.get('tags') or [])


def _mirror_name_for(source_rule: dict) -> str:
    """Best-effort human name for a source rule, used to build mirror sub_directory names."""
    name = (source_rule.get('folder_name') or '').strip()
    if name:
        return name
    target = (source_rule.get('target_directory') or '').strip()
    if target:
        return os.path.basename(target.rstrip('/\\')) or target
    sub_dir = (source_rule.get('sub_directory') or '').strip()
    return sub_dir or 'Unnamed'


def _apply_sync_prefix(name: str, prefix: str, position: str) -> str:
    prefix = (prefix or '').strip()
    if not prefix:
        return name
    return f'{name} {prefix}' if position == 'after' else f'{prefix} {name}'


def build_mirror_rule(source_rule: dict, prefix: str, position: str,
                       strip_tags: Optional[list] = None) -> dict:
    """Build a fresh mirrored sub-rule dict from ``source_rule``, recursing into
    its own nested ``sub_rules`` so the entire subtree is mirrored (each level
    gets the same prefix applied to its sub_directory name).

    ``strip_tags`` (the master's own identifying tags, e.g. ``feral``) and
    exclusions injected by another sync master are removed from the copied
    ``exclude_tags``. These exclusions only control which top-level source
    rules claim files; they must not leak into a mirror belonging to a group
    that is meant to handle those files first.
    """
    strip_tags = set(strip_tags or [])
    sync_injected = set(source_rule.get('sync_injected_excludes') or [])
    exclude_tags = [
        t for t in (source_rule.get('exclude_tags') or [])
        if t not in strip_tags and t not in sync_injected
    ]
    mirror = {
        'needed_tags': deepcopy(source_rule.get('needed_tags') or []),
        'can_have_tags': deepcopy(source_rule.get('can_have_tags') or []),
        'exclude_tags': exclude_tags,
        'tags': deepcopy(source_rule.get('tags') or []),
        'pool_ids': deepcopy(source_rule.get('pool_ids') or []),
        'sub_directory': _apply_sync_prefix(_mirror_name_for(source_rule), prefix, position),
        'enabled': bool(source_rule.get('enabled', True)),
        'mirror_of': source_rule.get('rule_id'),
        'mirror_generated': True,
        'rule_id': str(uuid.uuid4()),
    }
    children = [build_mirror_rule(c, prefix, position, strip_tags)
                for c in (source_rule.get('sub_rules') or [])]
    if children:
        mirror['sub_rules'] = children
    return mirror


def _mirror_content_matches(existing: dict, fresh: dict) -> bool:
    """Compare two mirror dicts ignoring identity bookkeeping fields (recursively,
    since nested mirrored sub_rules get a fresh rule_id/mirror_of every rebuild)."""
    def _strip(d: dict) -> dict:
        d = deepcopy(d)
        d.pop('rule_id', None)
        d.pop('mirror_of', None)
        d.pop('mirror_generated', None)
        if d.get('sub_rules'):
            d['sub_rules'] = [_strip(s) for s in d['sub_rules']]
        return d
    return _strip(existing) == _strip(fresh)


def _deduplicate_manual_rules(rules: list) -> list:
    """Keep the first occurrence of each manually-authored rule ID."""
    seen_ids = set()
    unique = []
    for rule in rules:
        rule_id = rule.get('rule_id')
        if rule_id and rule_id in seen_ids:
            continue
        if rule_id:
            seen_ids.add(rule_id)
        unique.append(rule)
    return unique


def _normalize_sync_children(master: dict) -> bool:
    """Deduplicate manual children and keep them before generated mirrors."""
    children = master.get('sub_rules') or []
    manual = _deduplicate_manual_rules([r for r in children if not r.get('mirror_generated')])
    nested_manual_ids = set()

    def collect_nested_ids(rule: dict) -> None:
        for child in rule.get('sub_rules') or []:
            if not child.get('mirror_generated') and child.get('rule_id'):
                nested_manual_ids.add(child['rule_id'])
            collect_nested_ids(child)

    for rule in manual:
        collect_nested_ids(rule)
    manual = [rule for rule in manual if rule.get('rule_id') not in nested_manual_ids]
    mirrors = [r for r in children if r.get('mirror_generated')]
    normalized = manual + mirrors
    if normalized == children:
        return False
    master['sub_rules'] = normalized
    return True


def get_sync_candidates(master: dict, top_level_rules: list) -> list:
    """Top-level rules eligible to be mirrored under ``master``."""
    mid = master.get('rule_id')
    excluded_ids = {str(rule_id) for rule_id in (master.get('sync_exclude_rule_ids') or [])}
    out = []
    for r in top_level_rules:
        if r is master or r.get('rule_id') == mid:
            continue
        if r.get('sync_master'):
            continue  # masters never mirror other masters
        if r.get('sort_group') == 'special':
            continue  # Special Sorting rules are never mirrored
        if r.get('sort_group') == 'pool':
            continue  # Pool Sorting rules are never mirrored
        if r.get('exclude_from_sync'):
            continue
        if str(r.get('rule_id')) in excluded_ids:
            continue
        if not r.get('enabled', True):
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Sort-group tabs: Pool Sorting / Special Sorting / Group Sorting / Main Sorting
# ---------------------------------------------------------------------------

TAB_ORDER = {'pool': 0, 'special': 1, 'group': 2, 'main': 3}

TAB_LABELS = {
    'pool': 'Pool Sorting',
    'special': 'Special Sorting',
    'group': 'Group Sorting',
    'main': 'Main Sorting',
}


def rule_tab_category(rule: dict) -> str:
    """Which of the 4 top-level tabs a top-level rule belongs to."""
    if rule.get('sync_master'):
        return 'group'
    if rule.get('sort_group') == 'pool':
        return 'pool'
    if rule.get('sort_group') == 'special':
        return 'special'
    return 'main'


def node_tab_category(node: 'RuleNode') -> str:
    """Tab category for any node — sub-rules inherit their top-level ancestor's tab."""
    top = node
    while top.parent is not None:
        top = top.parent
    return rule_tab_category(top.rule)


def enforce_tab_order(rules: list) -> None:
    """Stable-partition top-level rules into [special..., group..., main...] in place.

    Stable sort preserves relative order within each block, so this only fixes
    cross-block ordering violations (e.g. after toggling sync_master/sort_group)
    without disturbing manual reordering the user already did within a tab.
    """
    rules.sort(key=lambda r: TAB_ORDER.get(rule_tab_category(r), 3))


def run_sync(master: dict, top_level_rules: list, apply: bool = True) -> dict:
    """Compute (and optionally apply) the sync plan for ``master``.

    With ``apply=False`` nothing is mutated — useful for out-of-sync detection
    and for building a confirmation-dialog preview before committing.
    """
    prefix = master.get('sync_prefix') or ''
    position = master.get('sync_prefix_position') or 'before'
    candidates = get_sync_candidates(master, top_level_rules)
    candidate_ids = {c.get('rule_id') for c in candidates}
    own_tags = _rule_own_tags(master)

    existing_subs = master.get('sub_rules') or []
    existing_mirrors = {s.get('mirror_of'): s for s in existing_subs if s.get('mirror_generated')}
    manual_subs = _deduplicate_manual_rules(
        [s for s in existing_subs if not s.get('mirror_generated')])

    added, updated, removed = [], [], []
    new_mirrors = []
    for c in candidates:
        cid = c.get('rule_id')
        existing = existing_mirrors.get(cid)
        mirror = build_mirror_rule(c, prefix, position, strip_tags=own_tags)
        if existing:
            # Preserve user-local overrides that aren't sync-critical.
            mirror['rule_id'] = existing['rule_id']
            mirror['enabled'] = existing.get('enabled', mirror['enabled'])
            mirror['sub_directory'] = existing.get('sub_directory') or mirror['sub_directory']
            if not _mirror_content_matches(existing, mirror):
                updated.append(c)
        else:
            added.append(c)
        new_mirrors.append(mirror)

    for cid, m in existing_mirrors.items():
        if cid not in candidate_ids:
            removed.append(m)

    idx_master = top_level_rules.index(master)
    cand_indices = [i for i, r in enumerate(top_level_rules) if r.get('rule_id') in candidate_ids]
    reorder_needed = bool(cand_indices) and idx_master > min(cand_indices)

    injected, uninjected = [], []
    for r in top_level_rules:
        rid = r.get('rule_id')
        prev_injected = set(r.get('sync_injected_excludes') or [])
        if rid in candidate_ids:
            if set(own_tags) - prev_injected:
                injected.append(r)
        elif prev_injected:
            uninjected.append(r)

    if apply:
        # Keep group-specific rules first; generated mirrors follow them.
        master['sub_rules'] = manual_subs + new_mirrors
        if reorder_needed:
            top_level_rules.remove(master)
            new_idx = min(i for i, r in enumerate(top_level_rules) if r.get('rule_id') in candidate_ids)
            top_level_rules.insert(new_idx, master)
        for r in injected:
            prev = set(r.get('sync_injected_excludes') or [])
            need = set(own_tags) - prev
            r['exclude_tags'] = list(dict.fromkeys((r.get('exclude_tags') or []) + list(need)))
            r['sync_injected_excludes'] = sorted(prev | need)
        for r in uninjected:
            prev = set(r.get('sync_injected_excludes') or [])
            r['exclude_tags'] = [t for t in (r.get('exclude_tags') or []) if t not in prev]
            r['sync_injected_excludes'] = []

    return {
        'added': added, 'updated': updated, 'removed': removed,
        'reorder_needed': reorder_needed,
        'injected': injected, 'uninjected': uninjected,
    }


def is_master_out_of_sync(master: dict, top_level_rules: list) -> bool:
    plan = run_sync(master, top_level_rules, apply=False)
    return bool(plan['added'] or plan['updated'] or plan['removed']
                or plan['reorder_needed'] or plan['injected'] or plan['uninjected'])

# ---------------------------------------------------------------------------
# Rule introspection helpers  (identical logic to v1)
# ---------------------------------------------------------------------------

def _rule_tag_summary(rule: dict) -> str:
    """Return a short human-readable summary of tags."""
    parts: list[str] = []
    needed   = rule.get('needed_tags')   or []
    can_have = rule.get('can_have_tags') or []
    legacy   = rule.get('tags')          or []
    pool_ids = rule.get('pool_ids')      or []
    if needed:
        parts.append('ALL: ' + ', '.join(str(t) for t in needed))
    if can_have:
        parts.append('ANY: ' + ', '.join(str(t) for t in can_have))
    if legacy and not needed and not can_have:
        parts.append(', '.join(str(t) for t in legacy))
    if pool_ids:
        pool_names = rule.get('pool_names') or {}
        labels = [str(pool_names.get(str(p)) or pool_names.get(p) or p) for p in pool_ids]
        parts.append('POOLS: ' + ', '.join(labels))
    return ' | '.join(parts) if parts else '(no tags)'


def _rule_type_label(rule: dict) -> str:
    needed   = rule.get('needed_tags')   or []
    can_have = rule.get('can_have_tags') or []
    legacy   = rule.get('tags')          or []
    pool_ids = rule.get('pool_ids')      or []
    if needed and can_have:
        return 'AND+OR'
    if needed:
        return 'AND'
    if can_have:
        return 'OR'
    if legacy:
        return 'Legacy'
    if pool_ids:
        return 'POOL'
    return '?'


def _short_path(path: str, max_len: int = 38) -> str:
    if len(path) <= max_len:
        return path
    parts = path.replace('\\', '/').split('/')
    if len(parts) > 4:
        return parts[0] + '/.../' + '/'.join(parts[-2:])
    return '...' + path[-(max_len - 3):]


def _truncate(text: str, max_len: int = 50) -> str:
    return text if len(text) <= max_len else text[:max_len - 1] + '…'


def _rule_display_name(rule: dict) -> str:
    """One-line display label for a rule (used in drag ghost, status bar, etc.)."""
    summary = _truncate(_rule_tag_summary(rule), 48)
    dest = rule.get('target_directory') or rule.get('folder_name') or ''
    if dest:
        return f'{summary}  →  {_short_path(dest, 28)}'
    return summary


def _subrule_display_name(rule: dict) -> str:
    """One-line display for a sub-rule row (shows sub_directory instead of target)."""
    sub_dir  = rule.get('sub_directory', '') or ''
    summary  = _truncate(_rule_tag_summary(rule), 44)
    return f'{sub_dir}/  {summary}' if sub_dir else summary

# ---------------------------------------------------------------------------
# RuleNode — the fundamental unit of the tree
# ---------------------------------------------------------------------------

@dataclass
class RuleNode:
    """
    Wraps a rule dict at a specific tree position.

    ``rule``              live reference into the nested data structure.
    ``depth``             0 = top-level main rule, 1 = sub-rule, 2 = sub-sub-rule …
    ``parent``            parent RuleNode, or None for top-level nodes.
    ``index_in_parent``   position inside parent.rule['sub_rules']
                          (or inside the top-level rules list for depth-0 nodes).
    ``collapsed``         whether this node's children are hidden in the tree.
    ``has_children``      True if rule['sub_rules'] is non-empty.
    """
    rule:             dict
    depth:            int
    parent:           Optional['RuleNode']
    index_in_parent:  int
    collapsed:        bool = False
    has_children:     bool = field(init=False)

    def __post_init__(self) -> None:
        self.has_children = bool(self.rule.get('sub_rules'))

    def refresh_has_children(self) -> None:
        self.has_children = bool(self.rule.get('sub_rules'))

    # Convenience: return the list that directly contains this node's rule
    def _sibling_list(self, top_level_rules: list) -> list:
        if self.parent is None:
            return top_level_rules
        return self.parent.rule.setdefault('sub_rules', [])

    def display_name(self) -> str:
        if self.depth == 0:
            return _rule_display_name(self.rule)
        return _subrule_display_name(self.rule)

# ---------------------------------------------------------------------------
# Flat-list builder
# ---------------------------------------------------------------------------

def build_flat_list(
    rules: list,
    depth: int = 0,
    parent: Optional[RuleNode] = None,
    _collapse_state: Optional[dict] = None,
) -> list[RuleNode]:
    """
    Walk ``rules`` (and each rule's ``sub_rules`` recursively) to produce a
    flat list of RuleNode objects in display order.

    ``_collapse_state`` maps ``id(rule_dict)`` → bool so that collapsed state
    survives a full rebuild (e.g. after a drag-drop reorder).  Pass the dict
    returned by ``extract_collapse_state`` before the operation.
    """
    flat: list[RuleNode] = []
    if _collapse_state is None:
        _collapse_state = {}

    for i, rule in enumerate(rules):
        collapsed = _collapse_state.get(id(rule), False)
        node = RuleNode(
            rule=rule,
            depth=depth,
            parent=parent,
            index_in_parent=i,
            collapsed=collapsed,
        )
        flat.append(node)
        children = rule.get('sub_rules') or []
        if children and not collapsed:
            flat.extend(
                build_flat_list(children, depth + 1, node, _collapse_state)
            )
    return flat


def extract_collapse_state(flat: list[RuleNode]) -> dict:
    """
    Snapshot the current collapsed state keyed by rule dict id.
    Call this before rebuilding the flat list to preserve expand/collapse.
    """
    return {id(node.rule): node.collapsed for node in flat}

# ---------------------------------------------------------------------------
# Write-back: flat list → nested rules
# ---------------------------------------------------------------------------

def write_back(flat: list[RuleNode], top_level_rules: list) -> None:
    """
    Reconstruct the nested ``sub_rules`` structure from the flat node list.

    This rewrites ``top_level_rules`` in-place and regenerates every
    rule's ``sub_rules`` list based on the current parent relationships
    tracked in each ``RuleNode``.

    Strategy: clear the top-level list and each rule's sub_rules, then
    re-insert rules in flat-list order honouring depth/parent references.
    """
    # Clear existing structure
    top_level_rules.clear()
    for node in flat:
        # Only clear sub_rules if the node is NOT collapsed — collapsed nodes'
        # children are absent from flat and must be preserved as-is in the dict.
        if not node.collapsed:
            node.rule.pop('sub_rules', None)  # will be rebuilt below

    # Re-insert in order
    for node in flat:
        if node.parent is None:
            top_level_rules.append(node.rule)
        else:
            parent_subs = node.parent.rule.setdefault('sub_rules', [])
            parent_subs.append(node.rule)

# ---------------------------------------------------------------------------
# Tree mutation helpers
# ---------------------------------------------------------------------------

def _visible_flat(flat: list[RuleNode]) -> list[RuleNode]:
    """Return only the nodes that should be rendered (respects collapsed)."""
    # build_flat_list already excludes collapsed children, so this is just
    # a passthrough — but kept as a hook for future filter logic.
    return flat


def insert_node_before(
    flat: list[RuleNode],
    target_idx: int,
    new_rule: dict,
    top_level_rules: list,
) -> int:
    """
    Insert ``new_rule`` before ``flat[target_idx]`` at the same depth.
    Updates ``flat`` in-place and calls ``write_back``.
    Returns the index of the newly inserted node in flat.
    """
    target = flat[target_idx]
    depth  = target.depth
    parent = target.parent

    new_node = RuleNode(
        rule=new_rule,
        depth=depth,
        parent=parent,
        index_in_parent=target.index_in_parent,
        collapsed=False,
    )
    flat.insert(target_idx, new_node)
    _renumber_siblings_from(flat, target_idx, top_level_rules)
    write_back(flat, top_level_rules)
    return target_idx


def insert_node_after(
    flat: list[RuleNode],
    target_idx: int,
    new_rule: dict,
    top_level_rules: list,
) -> int:
    """
    Insert ``new_rule`` immediately after ``flat[target_idx]``'s subtree
    at the same depth.  Returns the index of the new node.
    """
    target = flat[target_idx]
    # Find the end of this node's subtree
    insert_at = _subtree_end(flat, target_idx) + 1

    new_node = RuleNode(
        rule=new_rule,
        depth=target.depth,
        parent=target.parent,
        index_in_parent=0,  # will be fixed by write_back
        collapsed=False,
    )
    flat.insert(insert_at, new_node)
    write_back(flat, top_level_rules)
    return insert_at


def insert_node_into(
    flat: list[RuleNode],
    target_idx: int,
    new_rule: dict,
    top_level_rules: list,
) -> int:
    """
    Insert ``new_rule`` as the *last* sub-rule of ``flat[target_idx]``.
    Expands the target node if it was collapsed.
    Returns the index of the new node.
    """
    target = flat[target_idx]
    target.collapsed = False
    target.refresh_has_children()

    subtree_end = _subtree_end(flat, target_idx)
    insert_at   = subtree_end + 1

    new_node = RuleNode(
        rule=new_rule,
        depth=target.depth + 1,
        parent=target,
        index_in_parent=0,
        collapsed=False,
    )
    flat.insert(insert_at, new_node)
    write_back(flat, top_level_rules)
    target.refresh_has_children()
    return insert_at


def remove_node(
    flat: list[RuleNode],
    node_idx: int,
    top_level_rules: list,
) -> list[RuleNode]:
    """
    Remove ``flat[node_idx]`` and its entire subtree from ``flat``.
    Calls ``write_back``.  Returns the removed nodes (subtree copy).
    """
    end    = _subtree_end(flat, node_idx)
    removed = flat[node_idx : end + 1]
    del flat[node_idx : end + 1]
    write_back(flat, top_level_rules)
    return removed


def move_node(
    flat: list[RuleNode],
    src_idx: int,
    dst_idx: int,
    zone: str,           # 'before' | 'after' | 'into'
    top_level_rules: list,
    copy: bool = False,
) -> int:
    """
    Move (or copy) the subtree rooted at ``flat[src_idx]`` to a new position
    defined by ``dst_idx`` + ``zone``.

    Returns the new index of the (first node of the) moved/copied subtree.

    ``zone`` values:
        'before'  — insert before dst at same depth
        'after'   — insert after dst's subtree at same depth
        'into'    — append as last child of dst
    """
    if src_idx == dst_idx:
        return src_idx

    # Guard: cannot drop onto own descendant
    src_end = _subtree_end(flat, src_idx)
    if src_idx < dst_idx <= src_end:
        return src_idx  # illegal — drop inside own subtree

    subtree_rules = [deepcopy(n.rule) for n in flat[src_idx : src_end + 1]]
    src_node      = flat[src_idx]
    src_depth     = src_node.depth
    dst_node      = flat[dst_idx]

    if not copy:
        # Remove source first (adjusts dst_idx if it was after src)
        if dst_idx > src_end:
            # dst is after the source block — removal will shift dst index
            shift = src_end - src_idx + 1
        else:
            shift = 0
        del flat[src_idx : src_end + 1]
        adj_dst = dst_idx - shift
    else:
        adj_dst = dst_idx
        shift   = 0

    # Determine target depth for moved nodes
    if zone == 'into':
        target_parent_node = flat[adj_dst]
        target_depth       = target_parent_node.depth + 1
        target_parent      = target_parent_node
    elif zone == 'before':
        target_parent_node = flat[adj_dst]
        target_depth       = target_parent_node.depth
        target_parent      = target_parent_node.parent
    else:  # 'after'
        target_parent_node = flat[adj_dst]
        target_depth       = target_parent_node.depth
        target_parent      = target_parent_node.parent

    depth_delta = target_depth - src_depth

    # Rebuild RuleNode objects with updated depth/parent
    def _make_nodes(rules_list, depth, parent):
        nodes = []
        for i, r in enumerate(rules_list):
            n = RuleNode(rule=r, depth=depth, parent=parent, index_in_parent=i)
            nodes.append(n)
            children = r.get('sub_rules') or []
            if children:
                nodes.extend(_make_nodes(children, depth + 1, n))
        return nodes

    new_nodes = _make_nodes(subtree_rules, target_depth, target_parent)
    # Strip sub_rules from rule dicts — write_back will rebuild from node tree
    for nn in new_nodes:
        nn.rule.pop('sub_rules', None)

    # Find insertion index
    if zone == 'before':
        insert_at = adj_dst
    elif zone == 'after':
        insert_at = _subtree_end(flat, adj_dst) + 1
    else:  # 'into'
        target_parent_node.collapsed = False
        insert_at = _subtree_end(flat, adj_dst) + 1

    for offset, nn in enumerate(new_nodes):
        flat.insert(insert_at + offset, nn)

    write_back(flat, top_level_rules)

    # Refresh has_children flags for affected parents
    if not copy and src_node.parent:
        src_node.parent.refresh_has_children()
    if target_parent:
        target_parent.refresh_has_children()

    return insert_at


def copy_subtree(rule: dict) -> dict:
    """Return a deep copy of a rule dict (including all nested sub_rules)."""
    return deepcopy(rule)

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _subtree_end(flat: list[RuleNode], idx: int) -> int:
    """
    Return the index of the last node belonging to the subtree rooted at
    ``flat[idx]`` (i.e., the node itself plus all deeper nodes that follow).
    """
    root_depth = flat[idx].depth
    end = idx
    while end + 1 < len(flat) and flat[end + 1].depth > root_depth:
        end += 1
    return end


def _renumber_siblings_from(
    flat: list[RuleNode],
    start_idx: int,
    top_level_rules: list,
) -> None:
    """
    After an insertion, update ``index_in_parent`` for sibling nodes so they
    stay consistent.  (write_back makes this redundant for serialisation but
    useful for in-memory state.)
    """
    if start_idx >= len(flat):
        return
    depth = flat[start_idx].depth
    parent = flat[start_idx].parent
    counter = 0
    for node in flat:
        if node.depth == depth and node.parent is parent:
            node.index_in_parent = counter
            counter += 1

# ---------------------------------------------------------------------------
# Phase 2 — RuleTreePanel
# ---------------------------------------------------------------------------

class RuleTreePanel(tk.Frame):
    """
    Left panel: a scrollable Canvas that renders the rule hierarchy as a
    collapsible file-tree.

    Public interface
    ----------------
    refresh(flat, selected_idx=None)   — rebuild all drawn rows
    set_filter(text)                   — apply text filter and refresh
    set_selected(flat_idx)             — change highlighted row (no callback)
    y_to_display_idx(canvas_y)         — hit-test: canvas y → display index
    display_idx_to_flat_idx(disp_idx)  — display index → flat list index

    Callbacks (assign after construction)
    -------------------------------------
    on_select  : callable(flat_idx)   — row clicked
    on_toggle  : callable(flat_idx)   — triangle clicked (expand/collapse)
    """

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG_COLOR, **kw)
        _apply_panel_ttk_style()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            self, bg=BG_COLOR, highlightthickness=0, borderwidth=0)
        self._vscroll = ttk.Scrollbar(
            self, orient='vertical', command=self._canvas.yview,
            style='Panel.Vertical.TScrollbar')
        self._canvas.configure(yscrollcommand=self._vscroll.set)
        self._canvas.grid(row=0, column=0, sticky='nsew')
        self._vscroll.grid(row=0, column=1, sticky='ns')

        self._canvas.bind('<Configure>', self._on_canvas_configure)
        self._canvas.bind('<Enter>',
                          lambda e: self._canvas.bind_all('<MouseWheel>', self._on_mousewheel))
        self._canvas.bind('<Leave>',
                          lambda e: self._canvas.unbind_all('<MouseWheel>'))

        # State
        self._flat:         list[RuleNode]                  = []
        self._visible:      list[tuple[int, RuleNode]]      = []  # (flat_idx, node)
        self._filter_text:  str                             = ''
        self._category_filter: Optional[str]                = None  # 'special'|'group'|'main'|None
        self._selected_idx: Optional[int]                   = None  # flat index
        self._canvas_w:     int                             = 600
        self._file_counts:  dict                            = {}  # normalized dir -> count
        self._out_of_sync_ids: set                          = set()  # rule_id set (masters)
        self._base_dirs:    dict                            = {}  # pictures/videos/gifs -> path

        # Callbacks
        self.on_select:  Optional[object] = None  # (flat_idx) -> None
        self.on_toggle:  Optional[object] = None  # (flat_idx) -> None
        self.on_activate:Optional[object] = None  # (flat_idx) -> None  (double-click / Enter)
        self.on_delete:  Optional[object] = None  # (flat_idx) -> None
        self.on_context: Optional[object] = None  # (flat_idx, x, y) -> None  (right-click)
        self.on_drop:    Optional[object] = None  # (src, dst, zone, copy) -> None

        # Phase 5 — DnD state
        self._drag_src_flat: Optional[int]         = None
        self._drag_active:   bool                  = False
        self._drag_ghost:    Optional[tk.Toplevel] = None
        self._drag_start_x:  int                   = 0
        self._drag_start_y:  int                   = 0
        self._drop_dst_flat: Optional[int]         = None
        self._drop_zone:     Optional[str]         = None  # 'before'|'into'|'after'

        # Bind interaction events
        self._canvas.bind('<ButtonPress-1>',   self._on_press)
        self._canvas.bind('<B1-Motion>',        self._on_drag_motion)
        self._canvas.bind('<ButtonRelease-1>',  self._on_drag_release)
        self._canvas.bind('<Double-Button-1>',  self._on_double_click)
        self._canvas.bind('<Button-3>',         self._on_right_click)
        self._canvas.bind('<FocusIn>',  lambda e: None)  # accept focus
        self._canvas.bind('<FocusOut>', lambda e: None)
        self._canvas.bind('<KeyPress>',         self._on_key)
        self._canvas.bind('<Escape>',           lambda e: self._cancel_drag())
        # Make canvas focusable so keyboard events reach it
        self._canvas.config(takefocus=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, flat: list[RuleNode], selected_idx: Optional[int] = None):
        """Rebuild all rendered rows from the flat list."""
        self._flat = flat
        if selected_idx is not None:
            self._selected_idx = selected_idx
        self._rebuild_visible()
        self._draw_all()

    def set_filter(self, text: str):
        self._filter_text = text.strip().lower()
        self._rebuild_visible()
        self._draw_all()

    def set_category(self, category: Optional[str]) -> None:
        """Restrict visible rows to a single tab category ('special'/'group'/'main'),
        or pass None to show everything."""
        self._category_filter = category
        self._selected_idx = None
        self._rebuild_visible()
        self._draw_all()

    def set_file_counts(self, file_counts: dict) -> None:
        """Update the normalized-dir → file-count cache and redraw."""
        self._file_counts = file_counts
        self._draw_all()

    def set_out_of_sync_ids(self, ids: set) -> None:
        """Update the set of master rule_ids that are out of sync and redraw."""
        self._out_of_sync_ids = ids
        self._draw_all()

    def set_base_dirs(self, base_dirs: dict) -> None:
        """Update the pictures/videos/gifs base directories used for split-mode rules."""
        self._base_dirs = base_dirs
        self._draw_all()

    def set_selected(self, flat_idx: Optional[int]):
        self._selected_idx = flat_idx
        self._draw_all()

    def y_to_display_idx(self, canvas_y: int) -> Optional[int]:
        """Convert a canvas y-coordinate to a display index (into self._visible)."""
        idx = int(canvas_y // ROW_HEIGHT)
        if 0 <= idx < len(self._visible):
            return idx
        return None

    def display_idx_to_flat_idx(self, disp_idx: int) -> Optional[int]:
        if 0 <= disp_idx < len(self._visible):
            return self._visible[disp_idx][0]
        return None

    def get_visible(self) -> list[tuple[int, RuleNode]]:
        return self._visible

    # ------------------------------------------------------------------
    # Phase 3 — Mouse interaction
    # ------------------------------------------------------------------

    def _canvas_y_to_adjusted(self, event_y: int) -> int:
        """Convert window-relative y to canvas-document y (accounts for scroll)."""
        return int(event_y + self._canvas.canvasy(0))

    def _hit_flat_idx(self, event_y: int) -> Optional[int]:
        """Return the flat index of the row at the given window-y, or None."""
        doc_y    = self._canvas_y_to_adjusted(event_y)
        disp_idx = self.y_to_display_idx(doc_y)
        if disp_idx is None:
            return None
        return self.display_idx_to_flat_idx(disp_idx)

    def _is_triangle_hit(self, node: RuleNode, event_x: int) -> bool:
        """True if the click x falls within the triangle hitbox for this node."""
        if not node.has_children:
            return False
        tri_x = 6 + node.depth * INDENT_PX
        return tri_x <= event_x <= tri_x + TRIANGLE_W + 4

    def _on_press(self, event):
        """ButtonPress-1: select / toggle immediately AND record drag start."""
        self._canvas.focus_set()
        self._drag_start_x  = event.x
        self._drag_start_y  = event.y
        self._drag_active   = False
        flat_idx = self._hit_flat_idx(event.y)
        self._drag_src_flat = flat_idx
        if flat_idx is None:
            return
        node = self._flat[flat_idx]
        if self._is_triangle_hit(node, event.x):
            node.collapsed = not node.collapsed
            node.refresh_has_children()
            self._drag_src_flat = None   # triangle clicks don't drag
            if self.on_toggle:
                self.on_toggle(flat_idx)
        else:
            self._selected_idx = flat_idx
            self._draw_all()
            if self.on_select:
                self.on_select(flat_idx)

    def _on_double_click(self, event):
        flat_idx = self._hit_flat_idx(event.y)
        if flat_idx is None:
            return
        node = self._flat[flat_idx]
        if self._is_triangle_hit(node, event.x):
            return  # triangle already handled by single-click
        self._selected_idx = flat_idx
        self._draw_all()
        if self.on_activate:
            self.on_activate(flat_idx)

    def _on_right_click(self, event):
        flat_idx = self._hit_flat_idx(event.y)
        if flat_idx is None:
            return
        self._selected_idx = flat_idx
        self._draw_all()
        if self.on_select:
            self.on_select(flat_idx)
        if self.on_context:
            self.on_context(flat_idx, event.x_root, event.y_root)

    # ------------------------------------------------------------------
    # Phase 3 — Keyboard navigation
    # ------------------------------------------------------------------

    def _on_key(self, event):
        key = event.keysym
        ctrl = (event.state & 0x4) != 0

        if key in ('Up', 'Down'):
            self._move_selection(1 if key == 'Down' else -1)
        elif key == 'space' or key == 'space':
            self._toggle_selected()
        elif key == 'Return':
            if self._selected_idx is not None and self.on_activate:
                self.on_activate(self._selected_idx)
        elif key == 'Delete':
            if self._selected_idx is not None and self.on_delete:
                self.on_delete(self._selected_idx)
        elif ctrl and key == 's':
            pass  # handled at app level via root bind
        elif ctrl and key == 'n':
            pass  # handled at app level
        elif ctrl and key == 'd':
            pass  # handled at app level

    def _move_selection(self, delta: int):
        if not self._visible:
            return
        if self._selected_idx is None:
            new_flat = self._visible[0][0] if delta > 0 else self._visible[-1][0]
        else:
            # Find current display position
            cur_disp = next(
                (di for di, (fi, _) in enumerate(self._visible) if fi == self._selected_idx),
                None)
            if cur_disp is None:
                new_flat = self._visible[0][0]
            else:
                new_disp = max(0, min(len(self._visible) - 1, cur_disp + delta))
                new_flat = self._visible[new_disp][0]

        self._selected_idx = new_flat
        self._draw_all()
        self._scroll_to_selected()
        if self.on_select:
            self.on_select(new_flat)

    def _toggle_selected(self):
        if self._selected_idx is None:
            return
        node = self._flat[self._selected_idx]
        if not node.has_children:
            return
        node.collapsed = not node.collapsed
        if self.on_toggle:
            self.on_toggle(self._selected_idx)

    def _scroll_to_selected(self):
        """Ensure the selected row is visible."""
        if self._selected_idx is None:
            return
        disp_idx = next(
            (di for di, (fi, _) in enumerate(self._visible) if fi == self._selected_idx),
            None)
        if disp_idx is None:
            return
        total_h = len(self._visible) * ROW_HEIGHT
        if total_h == 0:
            return
        row_top    = disp_idx * ROW_HEIGHT
        row_bottom = row_top + ROW_HEIGHT
        view_top   = self._canvas.canvasy(0)
        view_h     = self._canvas.winfo_height()
        view_bottom = view_top + view_h
        if row_top < view_top:
            self._canvas.yview_moveto(row_top / total_h)
        elif row_bottom > view_bottom:
            self._canvas.yview_moveto((row_bottom - view_h) / total_h)

    # ------------------------------------------------------------------
    # Phase 5 — Drag and drop
    # ------------------------------------------------------------------

    def _on_drag_motion(self, event):
        """B1-Motion: start drag once threshold exceeded, then update ghost + indicator."""
        if self._drag_src_flat is None:
            return
        dx = abs(event.x - self._drag_start_x)
        dy = abs(event.y - self._drag_start_y)

        if not self._drag_active:
            if dx < 6 and dy < 6:
                return  # below drag threshold
            self._drag_active = True
            self._create_ghost(event)

        # Move ghost
        if self._drag_ghost:
            try:
                self._drag_ghost.geometry(f'+{event.x_root + 14}+{event.y_root + 4}')
            except Exception:
                pass

        # Compute drop target + zone
        doc_y    = self._canvas_y_to_adjusted(event.y)
        disp_idx = int(doc_y // ROW_HEIGHT)
        if 0 <= disp_idx < len(self._visible):
            dst_flat = self._visible[disp_idx][0]
            row_top  = disp_idx * ROW_HEIGHT
            frac     = (doc_y - row_top) / ROW_HEIGHT
            if frac < 0.25:
                zone = 'before'
            elif frac > 0.75:
                zone = 'after'
            else:
                zone = 'into'
            # Guard: cannot drop into own subtree
            src_end = _subtree_end(self._flat, self._drag_src_flat)
            if self._drag_src_flat <= dst_flat <= src_end:
                dst_flat = None
                zone     = None
        else:
            dst_flat = None
            zone     = None

        self._drop_dst_flat = dst_flat
        self._drop_zone     = zone
        self._draw_drop_indicator()

    def _on_drag_release(self, event):
        """ButtonRelease-1: complete drop (click was already handled on press)."""
        if not self._drag_active:
            self._drag_src_flat = None
            return

        self._destroy_ghost()
        self._canvas.delete('drop_indicator')

        src  = self._drag_src_flat
        dst  = self._drop_dst_flat
        zone = self._drop_zone
        copy = bool(event.state & 0x4)  # Ctrl held = copy mode

        self._drag_active   = False
        self._drag_src_flat = None
        self._drop_dst_flat = None
        self._drop_zone     = None

        if dst is not None and zone is not None and src != dst:
            if self.on_drop:
                self.on_drop(src, dst, zone, copy)

    def _cancel_drag(self):
        """Escape: abort drag in progress."""
        if self._drag_active:
            self._destroy_ghost()
            self._canvas.delete('drop_indicator')
            self._drag_active   = False
            self._drag_src_flat = None
            self._drop_dst_flat = None
            self._drop_zone     = None
            self._draw_all()

    def _create_ghost(self, event):
        """Create a semi-transparent drag-ghost window near the cursor."""
        self._destroy_ghost()
        if self._drag_src_flat is None or self._drag_src_flat >= len(self._flat):
            return
        node  = self._flat[self._drag_src_flat]
        ghost = tk.Toplevel(self.winfo_toplevel())
        ghost.overrideredirect(True)
        try:
            ghost.attributes('-alpha', 0.80)
            ghost.attributes('-topmost', True)
        except Exception:
            pass
        ghost.configure(bg=ROW_SELECTED)
        tk.Label(
            ghost,
            text='  ' + _truncate(node.display_name(), 52) + '  ',
            bg=ROW_SELECTED, fg=TEXT_BRIGHT,
            font=(theme.MONO_FONT, 10), padx=6, pady=4).pack()
        ghost.geometry(f'+{event.x_root + 14}+{event.y_root + 4}')
        ghost.lift()
        self._drag_ghost = ghost

    def _destroy_ghost(self):
        if self._drag_ghost:
            try:
                self._drag_ghost.destroy()
            except Exception:
                pass
            self._drag_ghost = None

    def _draw_drop_indicator(self):
        """Overlay a drop indicator line or highlight rectangle on the canvas."""
        self._canvas.delete('drop_indicator')
        dst  = self._drop_dst_flat
        zone = self._drop_zone
        if dst is None or zone is None:
            return
        disp_idx = next(
            (di for di, (fi, _) in enumerate(self._visible) if fi == dst), None)
        if disp_idx is None:
            return
        w = self._canvas_w
        if zone == 'before':
            y = disp_idx * ROW_HEIGHT
            self._canvas.create_line(
                0, y, w, y, fill=ACCENT, width=2, tags='drop_indicator')
            self._canvas.create_polygon(
                0, y - 5, 0, y + 5, 9, y,
                fill=ACCENT, outline='', tags='drop_indicator')
        elif zone == 'after':
            y = (disp_idx + 1) * ROW_HEIGHT
            self._canvas.create_line(
                0, y, w, y, fill=ACCENT, width=2, tags='drop_indicator')
            self._canvas.create_polygon(
                0, y - 5, 0, y + 5, 9, y,
                fill=ACCENT, outline='', tags='drop_indicator')
        else:  # 'into'
            y_top = disp_idx * ROW_HEIGHT
            self._canvas.create_rectangle(
                2, y_top + 1, w - 2, y_top + ROW_HEIGHT - 1,
                fill='', outline=ACCENT_GREEN, width=2,
                tags='drop_indicator')

    # ------------------------------------------------------------------
    # Internal: filter + render
    # ------------------------------------------------------------------

    def _rebuild_visible(self):
        """Compute self._visible from self._flat, applying the category + text filters."""
        ft = self._filter_text
        cat = self._category_filter
        self._visible = []
        for i, node in enumerate(self._flat):
            if cat is not None and node_tab_category(node) != cat:
                continue
            if ft:
                rule = node.rule
                summary = _rule_tag_summary(rule).lower()
                dest = (rule.get('target_directory') or
                        rule.get('folder_name') or
                        rule.get('sub_directory') or '').lower()
                if ft not in summary and ft not in dest:
                    continue
            self._visible.append((i, node))

    def _draw_all(self):
        """Clear canvas and redraw every visible row."""
        self._canvas.delete('all')
        w = self._canvas_w
        if not self._visible:
            msg = 'No rules match filter' if self._filter_text else "No rules here yet — click '+ New Rule' above"
            self._canvas.create_text(
                w // 2, 40, text=msg,
                fill=TEXT_DIM, font=(theme.UI_FONT, 11), anchor='center')
            self._canvas.configure(scrollregion=(0, 0, w, 80))
            return

        total_h = len(self._visible) * ROW_HEIGHT + 4
        last_child_map = self._precompute_last_child()

        for disp_idx, (flat_idx, node) in enumerate(self._visible):
            y           = disp_idx * ROW_HEIGHT
            is_selected = (flat_idx == self._selected_idx)
            self._draw_row(disp_idx, flat_idx, node, y, w, is_selected, last_child_map)

        self._canvas.configure(scrollregion=(0, 0, w, total_h))

    # ------------------------------------------------------------------
    # Tree-line helpers
    # ------------------------------------------------------------------

    def _precompute_last_child(self) -> dict:
        """Map flat_idx → bool: True if node is the last visible child of its parent."""
        result: dict[int, bool] = {}
        for pos, (flat_idx, node) in enumerate(self._visible):
            is_last = True
            for pos2 in range(pos + 1, len(self._visible)):
                _, n2 = self._visible[pos2]
                if n2.depth < node.depth:
                    break
                if n2.depth == node.depth and n2.parent is node.parent:
                    is_last = False
                    break
            result[flat_idx] = is_last
        return result

    def _ancestor_has_more_visible(self, node: RuleNode, ancestor_depth: int) -> bool:
        """
        True if the node's ancestor at `ancestor_depth` is *not* the last visible
        child at its level — meaning a vertical continuation line should be drawn
        at that column.
        """
        # Walk up to the ancestor at the requested depth
        anc: Optional[RuleNode] = node
        while anc is not None and anc.depth > ancestor_depth:
            anc = anc.parent
        if anc is None or anc.depth != ancestor_depth:
            return False

        # Check whether that ancestor has any visible sibling after it
        found = False
        for _, n in self._visible:
            if not found:
                if n is anc:
                    found = True
                continue
            if n.depth < anc.depth:
                return False
            if n.depth == anc.depth and n.parent is anc.parent:
                return True
        return False

    # ------------------------------------------------------------------
    # Row drawing
    # ------------------------------------------------------------------

    def _draw_row(self, disp_idx: int, flat_idx: int, node: RuleNode,
                  y: int, w: int, is_selected: bool, last_child: dict):
        tag_row = f'row_{flat_idx}'
        tag_tri = f'tri_{flat_idx}'
        enabled = node.rule.get('enabled', True)
        depth   = node.depth
        mid_y   = y + ROW_HEIGHT // 2

        # ── Background ───────────────────────────────────────────────────
        if is_selected:
            bg = ROW_SELECTED
        elif disp_idx % 2 == 0:
            bg = BG_COLOR
        else:
            bg = BG_SURFACE

        self._canvas.create_rectangle(
            0, y, w, y + ROW_HEIGHT,
            fill=bg, outline='', tags=(tag_row, 'row_bg'))

        x_base = 6  # left margin

        # ── Tree connector lines ─────────────────────────────────────────
        # For each ancestor depth, draw a vertical continuation bar if that
        # ancestor still has more children coming below (in the visible list).
        for d in range(depth):
            lx = x_base + d * INDENT_PX + TRIANGLE_W // 2
            if self._ancestor_has_more_visible(node, d):
                self._canvas.create_line(
                    lx, y, lx, y + ROW_HEIGHT,
                    fill=BORDER_COLOR, tags=(tag_row, 'tree_line'))

        # Own-level connector: vertical + horizontal arm
        if depth > 0:
            conn_x  = x_base + (depth - 1) * INDENT_PX + TRIANGLE_W // 2
            is_last = last_child.get(flat_idx, True)
            v_end   = mid_y if is_last else y + ROW_HEIGHT
            h_end   = x_base + depth * INDENT_PX
            self._canvas.create_line(
                conn_x, y, conn_x, v_end,
                fill=BORDER_COLOR, tags=(tag_row, 'tree_line'))
            self._canvas.create_line(
                conn_x, mid_y, h_end, mid_y,
                fill=BORDER_COLOR, tags=(tag_row, 'tree_line'))

        # ── Expand / collapse triangle ───────────────────────────────────
        tri_x = x_base + depth * INDENT_PX

        if node.has_children:
            if node.collapsed:
                # ▶  right-pointing
                pts = [tri_x + 3,  mid_y - 5,
                       tri_x + 11, mid_y,
                       tri_x + 3,  mid_y + 5]
            else:
                # ▼  down-pointing
                pts = [tri_x + 2,  mid_y - 3,
                       tri_x + 12, mid_y - 3,
                       tri_x + 7,  mid_y + 4]
            self._canvas.create_polygon(
                pts, fill=TEXT_DIM, outline='',
                tags=(tag_row, tag_tri, 'triangle'))

        x = tri_x + TRIANGLE_W + 4

        # ── Type badge ───────────────────────────────────────────────────
        rule_type              = _rule_type_label(node.rule)
        accent, badge_bg, badge_fg = TYPE_COLORS.get(rule_type, TYPE_COLORS['?'])
        badge_text  = rule_type
        badge_w     = len(badge_text) * 7 + BADGE_PAD_X * 2
        badge_h     = 18
        badge_y_top = y + (ROW_HEIGHT - badge_h) // 2

        self._canvas.create_rectangle(
            x, badge_y_top, x + badge_w, badge_y_top + badge_h,
            fill=badge_bg, outline='', tags=(tag_row, 'badge'))
        self._canvas.create_text(
            x + badge_w // 2, badge_y_top + badge_h // 2,
            text=badge_text, fill=badge_fg,
            font=(theme.UI_FONT, 9, 'bold'), anchor='center',
            tags=(tag_row, 'badge'))
        x += badge_w + 8

        # ── Enabled dot (far right) ──────────────────────────────────────
        dot_r     = 4
        dot_x     = w - 12
        dot_color = ACCENT_GREEN if enabled else ACCENT_RED
        self._canvas.create_oval(
            dot_x - dot_r, mid_y - dot_r,
            dot_x + dot_r, mid_y + dot_r,
            fill=dot_color, outline='', tags=(tag_row, 'dot'))

        # ── Destination label (right-aligned, left of dot) ───────────────
        dest_text = ''
        if depth == 0:
            dest = (node.rule.get('target_directory') or
                    node.rule.get('folder_name') or '')
            if dest:
                dest_text = f'→ {_short_path(dest, 30)}'
        else:
            sub_dir = node.rule.get('sub_directory', '') or ''
            if sub_dir:
                dest_text = f'/{sub_dir}'

        resolved_dirs = resolve_node_directories(node, self._base_dirs)
        count_key = _count_key_for_dirs(resolved_dirs)
        if count_key:
            count = self._file_counts.get(count_key)
            if count is not None:
                count_text = f'{count:,} file' + ('s' if count != 1 else '')
                dest_text = f'{dest_text}  ·  {count_text}' if dest_text else count_text

        is_out_of_sync = bool(node.rule.get('sync_master')) and node.rule.get('rule_id') in self._out_of_sync_ids
        if is_out_of_sync:
            dest_text = f'{dest_text}  ⚠ out of sync' if dest_text else '⚠ out of sync'

        dest_fg   = ACCENT_YELLOW if is_out_of_sync else (TEXT_DIM if enabled else TEXT_FAINT)
        dest_x    = dot_x - dot_r - 6   # right-anchor just before dot
        dest_meas = len(dest_text) * 6 + 8 if dest_text else 0

        if dest_text:
            self._canvas.create_text(
                dest_x, mid_y,
                text=dest_text, fill=dest_fg,
                font=(theme.UI_FONT, 9), anchor='e',
                tags=(tag_row, 'dest'))

        # ── Tag summary (fills remaining space) ─────────────────────────
        available_w  = max(20, dest_x - dest_meas - x - 8)
        tag_summary  = _rule_tag_summary(node.rule)
        if node.rule.get('mirror_generated'):
            tag_summary = '🔒 ' + tag_summary
        max_chars    = max(8, int(available_w / 7))
        tag_display  = _truncate(tag_summary, max_chars)
        tag_fg       = TEXT_COLOR if enabled else TEXT_DIM

        self._canvas.create_text(
            x, mid_y,
            text=tag_display, fill=tag_fg,
            font=(theme.MONO_FONT, 10), anchor='w',
            tags=(tag_row, 'tag_text'))

        # ── Bottom separator ─────────────────────────────────────────────
        self._canvas.create_line(
            0, y + ROW_HEIGHT - 1, w, y + ROW_HEIGHT - 1,
            fill=BORDER_COLOR, tags=(tag_row, 'separator'))

    # ------------------------------------------------------------------
    # Canvas events
    # ------------------------------------------------------------------

    def _on_canvas_configure(self, event):
        self._canvas_w = max(event.width, 200)
        self._draw_all()

    def _on_mousewheel(self, event):
        try:
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# Phase 4 — TagListWidget
# ---------------------------------------------------------------------------

class TagListWidget(tk.Frame):
    """Label + scrollable Listbox + add / remove entry for a list of tags."""

    def __init__(self, parent, label: str, accent: str = ACCENT,
                 on_change=None, height: int = 4, validate=None, **kw):
        super().__init__(parent, bg=BG_PANEL, **kw)
        self._on_change = on_change
        self._validate = validate  # optional callable(str) -> bool
        self._locked = False
        self._build(label, accent, height)

    def _build(self, label: str, accent: str, height: int) -> None:
        self.columnconfigure(0, weight=1)
        tk.Label(self, text=label, bg=BG_PANEL, fg=accent,
                 font=(theme.UI_FONT, 9, 'bold')).grid(
            row=0, column=0, sticky='w', pady=(0, 2))

        list_frame = tk.Frame(self, bg=BG_PANEL)
        list_frame.grid(row=1, column=0, sticky='nsew')
        list_frame.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._lb = tk.Listbox(
            list_frame, height=height, selectmode='extended',
            font=(theme.MONO_FONT, 10), bg=BG_SURFACE, fg=TEXT_COLOR,
            selectbackground=accent, selectforeground=ON_ACCENT,
            relief='flat', highlightthickness=1,
            highlightcolor=accent, highlightbackground=BORDER_COLOR,
            activestyle='none')
        sb = ttk.Scrollbar(list_frame, orient='vertical', command=self._lb.yview,
                           style='Panel.Vertical.TScrollbar')
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=0, column=1, sticky='ns')
        self._lb.bind('<Delete>',    lambda _e: self._remove())
        self._lb.bind('<BackSpace>', lambda _e: self._remove())

        entry_frame = tk.Frame(self, bg=BG_PANEL)
        entry_frame.grid(row=2, column=0, sticky='ew', pady=(3, 0))
        entry_frame.columnconfigure(0, weight=1)
        self._entry_var = tk.StringVar()
        self._entry = tk.Entry(
            entry_frame, textvariable=self._entry_var,
            bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
            relief='flat', font=(theme.MONO_FONT, 10))
        self._entry.grid(row=0, column=0, sticky='ew', padx=(0, 2))
        self._entry.bind('<Return>',   lambda _e: self._add())
        self._entry.bind('<KP_Enter>', lambda _e: self._add())

        btn_frame = tk.Frame(entry_frame, bg=BG_PANEL)
        btn_frame.grid(row=0, column=1)
        self._buttons: list = []
        for txt, cmd, abg in [(' + ', self._add, ACCENT_GREEN),
                               (' − ', self._remove, ACCENT_RED)]:
            b = tk.Button(btn_frame, text=txt, command=cmd,
                          bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat',
                          font=(theme.UI_FONT, 11, 'bold'), cursor='hand2',
                          activebackground=abg, activeforeground=ON_ACCENT,
                          borderwidth=0, pady=0)
            b.pack(side='left', padx=1)
            self._buttons.append(b)

    def set_locked(self, locked: bool) -> None:
        """Disable editing (used for sync-managed / read-only mirror rules)."""
        self._locked = locked
        state = 'disabled' if locked else 'normal'
        self._entry.config(state=state)
        for b in self._buttons:
            b.config(state=state)

    def set_tags(self, tags: list) -> None:
        self._lb.delete(0, tk.END)
        for t in (tags or []):
            self._lb.insert(tk.END, str(t))

    def get_tags(self) -> list:
        return list(self._lb.get(0, tk.END))

    def focus_entry(self) -> None:
        self._entry.focus_set()

    def _add(self) -> None:
        if self._locked:
            return
        raw = self._entry_var.get().strip()
        if not raw:
            return
        new_tags = [t.strip() for t in raw.replace(',', ' ').split() if t.strip()]
        if self._validate:
            new_tags = [t for t in new_tags if self._validate(t)]
        existing = set(self.get_tags())
        for t in new_tags:
            if t not in existing:
                self._lb.insert(tk.END, t)
                existing.add(t)
        self._entry_var.set('')
        if self._on_change:
            self._on_change()

    def _remove(self) -> None:
        if self._locked:
            return
        sel = list(self._lb.curselection())
        for i in reversed(sel):
            self._lb.delete(i)
        if self._on_change:
            self._on_change()


# ---------------------------------------------------------------------------
# Phase 4 — RuleEditPanel
# ---------------------------------------------------------------------------

class RuleEditPanel(tk.Frame):
    """
    Persistent right-side editor for the currently-selected rule.

    Public API:
        load_node(node)    — populate all fields from node.rule
        save_to_node(node) — write current field values back to node.rule
        clear()            — show empty/placeholder state
        on_change          — callable fired on every edit (app marks dirty)
    """

    def __init__(self, parent, on_change=None, file_counts: Optional[dict] = None,
                 on_recount=None, on_sync_requested=None, on_jump_to_source=None,
                 resolve_source_name=None, sync_status_provider=None,
                 base_dirs: Optional[dict] = None, on_category_changed=None,
                 on_fetch_pool_names=None, on_apply_post_dates=None,
                 on_manage_sync_exclusions=None, **kw):
        super().__init__(parent, bg=BG_PANEL, **kw)
        self.on_change: Optional[callable] = on_change
        self._file_counts: dict = file_counts if file_counts is not None else {}
        self._base_dirs: dict = base_dirs if base_dirs is not None else {}
        self._on_recount = on_recount  # callable(dirs: list[str]) -> None
        self._on_sync_requested = on_sync_requested  # callable(node: RuleNode) -> None
        self._on_jump_to_source = on_jump_to_source   # callable(rule_id: str) -> None
        self._resolve_source_name = resolve_source_name  # callable(rule_id: str) -> str
        self._sync_status_provider = sync_status_provider  # callable(rule: dict) -> str
        self._on_category_changed = on_category_changed  # callable(node: RuleNode) -> None
        self._on_fetch_pool_names = on_fetch_pool_names  # callable(pool_ids: list[int]) -> (dict, error_str|None)
        self._on_apply_post_dates = on_apply_post_dates  # callable(rule: dict, done_callback) -> None
        self._on_manage_sync_exclusions = on_manage_sync_exclusions  # callable(rule: dict) -> None
        self._current_node: Optional[RuleNode] = None
        self._loading = False
        self._build_ui()
        self.clear()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ── Header ───────────────────────────────────────────────────────
        self._hdr_frame = tk.Frame(self, bg=BG_HEADER, height=46)
        self._hdr_frame.grid(row=0, column=0, sticky='ew')
        self._hdr_frame.grid_propagate(False)
        self._hdr_frame.columnconfigure(1, weight=1)

        self._badge_lbl = tk.Label(
            self._hdr_frame, text='', bg=BG_HEADER, fg=ACCENT,
            font=(theme.UI_FONT, 9, 'bold'), padx=8, pady=4)
        self._badge_lbl.grid(row=0, column=0, padx=(8, 0), pady=8, sticky='w')

        self._name_lbl = tk.Label(
            self._hdr_frame, text='', bg=BG_HEADER, fg=TEXT_BRIGHT,
            font=(theme.UI_FONT, 10), anchor='w', justify='left')
        self._name_lbl.grid(row=0, column=1, sticky='ew', padx=8, pady=8)

        # ── Notebook ─────────────────────────────────────────────────────
        self._setup_notebook_style()
        self._nb = ttk.Notebook(self, style='Panel.TNotebook')
        self._nb.grid(row=1, column=0, sticky='nsew')

        self._tags_tab = tk.Frame(self._nb, bg=BG_PANEL)
        self._pools_tab = tk.Frame(self._nb, bg=BG_PANEL)
        self._dest_tab = tk.Frame(self._nb, bg=BG_PANEL)
        self._opts_tab = tk.Frame(self._nb, bg=BG_PANEL)
        self._nb.add(self._tags_tab, text='  Tags  ')
        self._nb.add(self._pools_tab, text='  Pools  ')
        self._nb.add(self._dest_tab, text='  Destination  ')
        self._nb.add(self._opts_tab, text='  Options  ')

        self._build_tags_tab()
        self._build_pools_tab()
        self._build_dest_tab()
        self._build_opts_tab()

        # ── Empty state placeholder ───────────────────────────────────────
        self._empty_lbl = tk.Label(
            self, text='Select a rule to edit',
            bg=BG_PANEL, fg=TEXT_DIM,
            font=(theme.UI_FONT, 13), justify='center')
        # shown by clear(), hidden by load_node()

    def _setup_notebook_style(self) -> None:
        _apply_panel_ttk_style()

    # ------------------------------------------------------------------
    # Tags tab
    # ------------------------------------------------------------------

    def _build_tags_tab(self) -> None:
        p = self._tags_tab
        p.columnconfigure(0, weight=1)
        # Each tag section sizes to its own content (a handful of tags) instead
        # of stretching to fill the panel — leftover space collects in the
        # spacer row at the bottom so the boxes above stay compact and close
        # together, rather than three sparse listboxes spread thin.
        for row in range(5):
            p.rowconfigure(row, weight=0)
        p.rowconfigure(5, weight=1)  # spacer

        ch = self._on_field_changed

        # Mirror / group-only banner — shown for sync-generated mirrors (locked, with a
        # "Jump to Source" link) or for manually-added Group-Sorting-only sub-rules
        # (informational only, fully editable).
        self._mirror_banner = tk.Frame(p, bg=MIRROR_BG)
        self._mirror_lbl = tk.Label(
            self._mirror_banner, text='', bg=MIRROR_BG, fg=ACCENT_TEAL,
            font=(theme.UI_FONT, 9), justify='left', anchor='w')
        self._mirror_lbl.pack(side='left', padx=(8, 4), pady=6, fill='x', expand=True)
        self._jump_to_source_btn = tk.Button(
            self._mirror_banner, text='Jump to Source', command=self._jump_to_source,
            bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=8, cursor='hand2',
            font=(theme.UI_FONT, 9), activebackground=ACCENT, activeforeground=ON_ACCENT,
            borderwidth=0)
        self._jump_to_source_btn.pack(side='right', padx=8, pady=6)

        self._tw_needed = TagListWidget(
            p, 'Required Tags  (ALL must match — AND)',
            accent=ACCENT, on_change=ch, height=5)
        self._tw_needed.grid(row=1, column=0, sticky='ew', padx=10, pady=(10, 6))

        self._tw_can = TagListWidget(
            p, 'Any-Of Tags  (at least one must match — OR)',
            accent=ACCENT_PEACH, on_change=ch, height=5)
        self._tw_can.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 6))

        self._tw_excl = TagListWidget(
            p, 'Exclude Tags  (file must NOT have these)',
            accent=ACCENT_RED, on_change=ch, height=4)
        self._tw_excl.grid(row=3, column=0, sticky='ew', padx=10, pady=(0, 6))

        # Legacy tags notice (shown only when rule has old 'tags' key)
        self._legacy_frame = tk.Frame(p, bg=BG_PANEL)
        self._legacy_lbl = tk.Label(
            self._legacy_frame, text='', bg=BG_PANEL, fg=ACCENT_YELLOW,
            font=(theme.UI_FONT, 9), justify='left', wraplength=320)
        self._legacy_lbl.pack(side='left', padx=4)

        # Spacer — absorbs all leftover vertical space so the sections above
        # stay compact instead of stretching out with dead air between them.
        tk.Frame(p, bg=BG_PANEL).grid(row=5, column=0, sticky='nsew')

    # ------------------------------------------------------------------
    # Pools tab
    # ------------------------------------------------------------------

    def _build_pools_tab(self) -> None:
        p = self._pools_tab
        p.columnconfigure(0, weight=1)
        p.rowconfigure(1, weight=1)

        self._pools_desc_lbl = tk.Label(
            p, text='Matches files whose e621 post belongs to any of these pool IDs.\n'
                    'Combined with tags above using OR — a file matches the rule if it\n'
                    'satisfies the tag criteria OR belongs to one of these pools.',
            bg=BG_PANEL, fg=TEXT_DIM, font=(theme.UI_FONT, 9),
            justify='left', anchor='w')
        self._pools_desc_lbl.grid(row=0, column=0, sticky='ew', padx=10, pady=(10, 6))

        ch = self._on_field_changed
        self._tw_pools = TagListWidget(
            p, 'Pool IDs  (e621 pool_id numbers)',
            accent=ACCENT_TEAL, on_change=ch, height=6,
            validate=lambda t: t.isdigit())
        self._tw_pools.grid(row=1, column=0, sticky='nsew', padx=10, pady=(0, 6))

        fetch_row = tk.Frame(p, bg=BG_PANEL)
        fetch_row.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 4))
        tk.Button(fetch_row, text='Fetch Names from DB', command=self._fetch_pool_names_clicked,
                  bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=8, cursor='hand2',
                  font=(theme.UI_FONT, 9), activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).pack(side='left')

        self._pool_names_lbl = tk.Label(
            p, text='', bg=BG_PANEL, fg=TEXT_DIM, font=(theme.UI_FONT, 9),
            justify='left', anchor='nw', wraplength=320)
        self._pool_names_lbl.grid(row=3, column=0, sticky='ew', padx=10, pady=(0, 10))

    # ------------------------------------------------------------------
    # Destination tab
    # ------------------------------------------------------------------

    def _build_dest_tab(self) -> None:
        p = self._dest_tab
        p.columnconfigure(0, weight=1)

        # ── For depth-0 rules ─────────────────────────────────────────────
        self._d0_frame = tk.Frame(p, bg=BG_PANEL)
        self._d0_frame.columnconfigure(1, weight=1)

        def _lbl(parent, row, text):
            tk.Label(parent, text=text, bg=BG_PANEL, fg=TEXT_DIM,
                     font=(theme.UI_FONT, 9), anchor='w').grid(
                row=row, column=0, columnspan=2, sticky='w',
                padx=(12, 8), pady=(12, 0))

        _lbl(self._d0_frame, 0, 'Target Directory')
        tdir_row = tk.Frame(self._d0_frame, bg=BG_PANEL)
        tdir_row.grid(row=1, column=0, columnspan=2, sticky='ew',
                      padx=(12, 12), pady=(2, 0))
        tdir_row.columnconfigure(0, weight=1)
        self._target_var = tk.StringVar()
        self._target_var.trace_add('write', lambda *_: (self._on_field_changed(), self._update_append_media_type_state()))
        tk.Entry(tdir_row, textvariable=self._target_var,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10)).grid(
            row=0, column=0, sticky='ew', padx=(0, 4))
        tk.Button(tdir_row, text='Browse…', command=self._browse_target,
                  bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat',
                  font=(theme.UI_FONT, 9), cursor='hand2', padx=8,
                  activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).grid(row=0, column=1)

        count_row = tk.Frame(self._d0_frame, bg=BG_PANEL)
        count_row.grid(row=2, column=0, columnspan=2, sticky='w',
                       padx=(12, 12), pady=(4, 0))
        self._d0_count_lbl = tk.Label(
            count_row, text='', bg=BG_PANEL, fg=ACCENT_TEAL,
            font=(theme.UI_FONT, 9), justify='left', anchor='w')
        self._d0_count_lbl.pack(side='left')
        tk.Button(count_row, text='Recount', command=self._recount_current,
                  bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat',
                  font=(theme.UI_FONT, 8), cursor='hand2', padx=6,
                  activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).pack(side='left', padx=(8, 0))

        _lbl(self._d0_frame, 3, 'Folder Name  (relative, used when no Target Directory)')
        self._folder_var = tk.StringVar()
        self._folder_var.trace_add('write', lambda *_: self._on_field_changed())
        tk.Entry(self._d0_frame, textvariable=self._folder_var,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10)).grid(
            row=4, column=0, columnspan=2, sticky='ew',
            padx=(12, 12), pady=(2, 0))

        self._match_all_var = tk.BooleanVar()
        tk.Checkbutton(
            self._d0_frame,
            text="match_all — treat 'Any-Of' tags as required (AND)",
            variable=self._match_all_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            font=(theme.UI_FONT, 9)).grid(
            row=5, column=0, columnspan=2, sticky='w', padx=12, pady=(14, 0))

        self._append_media_type_var = tk.BooleanVar()
        self._append_media_type_ck = tk.Checkbutton(
            self._d0_frame,
            text='Append media type to folder name  (e.g. "Folder - Pics", "Folder - Vids", "Folder - Gifs")',
            variable=self._append_media_type_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            disabledforeground=TEXT_DIM,
            font=(theme.UI_FONT, 9))
        self._append_media_type_ck.grid(
            row=6, column=0, columnspan=2, sticky='w', padx=12, pady=(8, 0))
        tk.Label(self._d0_frame,
                 text='Only applies when using Folder Name (no Target Directory) with base directories.',
                 bg=BG_PANEL, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 8), justify='left').grid(
            row=7, column=0, columnspan=2, sticky='w', padx=(26, 12), pady=(0, 4))

        # ── For sub-rules (depth > 0) ──────────────────────────────────────
        self._sub_frame = tk.Frame(p, bg=BG_PANEL)
        self._sub_frame.columnconfigure(1, weight=1)

        tk.Label(self._sub_frame, text='Sub-Directory', bg=BG_PANEL, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 9), anchor='w').grid(
            row=0, column=0, columnspan=2, sticky='w',
            padx=(12, 8), pady=(14, 0))
        self._subdir_var = tk.StringVar()
        self._subdir_var.trace_add('write', lambda *_: self._on_field_changed())
        tk.Entry(self._sub_frame, textvariable=self._subdir_var,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10)).grid(
            row=1, column=0, columnspan=2, sticky='ew',
            padx=(12, 12), pady=(2, 0))

        sub_count_row = tk.Frame(self._sub_frame, bg=BG_PANEL)
        sub_count_row.grid(row=2, column=0, columnspan=2, sticky='w',
                           padx=(12, 12), pady=(4, 0))
        self._sub_count_lbl = tk.Label(
            sub_count_row, text='', bg=BG_PANEL, fg=ACCENT_TEAL,
            font=(theme.UI_FONT, 9), justify='left', anchor='w')
        self._sub_count_lbl.pack(side='left')
        tk.Button(sub_count_row, text='Recount', command=self._recount_current,
                  bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat',
                  font=(theme.UI_FONT, 8), cursor='hand2', padx=6,
                  activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).pack(side='left', padx=(8, 0))

        tk.Label(self._sub_frame,
                 text='Files matching this rule go into a sub-folder\n'
                      'of the parent rule\'s target directory.',
                 bg=BG_PANEL, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 9), justify='left').grid(
            row=3, column=0, columnspan=2, sticky='w',
            padx=(12, 12), pady=(8, 0))

    # ------------------------------------------------------------------
    # Options tab
    # ------------------------------------------------------------------

    def _build_opts_tab(self) -> None:
        p = self._opts_tab
        p.columnconfigure(0, weight=1)

        self._enabled_var  = tk.BooleanVar(value=True)
        self._exclfav_var  = tk.BooleanVar(value=True)
        self._onlyfav_var  = tk.BooleanVar(value=False)

        tk.Checkbutton(
            p, text='Enabled',
            variable=self._enabled_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            font=(theme.UI_FONT, 10)).grid(
            row=0, column=0, sticky='w', padx=16, pady=(16, 0))

        self._exclfav_ck = tk.Checkbutton(
            p, text='Exclude Favourite Artists  (skip files handled by artist sorter)',
            variable=self._exclfav_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            font=(theme.UI_FONT, 10))
        self._exclfav_ck.grid(row=1, column=0, sticky='w', padx=16, pady=(14, 0))

        self._onlyfav_ck = tk.Checkbutton(
            p, text='Only Favourite Directory  (restrict to fav folder)',
            variable=self._onlyfav_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            font=(theme.UI_FONT, 10))
        self._onlyfav_ck.grid(row=2, column=0, sticky='w', padx=16, pady=(14, 0))

        self._pool_var = tk.BooleanVar(value=False)
        self._pool_ck = tk.Checkbutton(
            p, text='Pool Sorting  (runs first of all, in the Pool Sorting tab — matches pool IDs only)',
            variable=self._pool_var, command=self._on_pool_toggled,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT_TEAL,
            font=(theme.UI_FONT, 10))
        self._pool_ck.grid(row=3, column=0, sticky='w', padx=16, pady=(14, 0))

        self._special_var = tk.BooleanVar(value=False)
        self._special_ck = tk.Checkbutton(
            p, text='Special Sorting  (runs next, in the Special Sorting tab — never mirrored)',
            variable=self._special_var, command=self._on_special_toggled,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT_YELLOW,
            font=(theme.UI_FONT, 10))
        self._special_ck.grid(row=4, column=0, sticky='w', padx=16, pady=(14, 0))

        self._match_post_date_var = tk.BooleanVar(value=False)
        self._match_post_date_ck = tk.Checkbutton(
            p, text='Match Post Date  (set file modified/created date to the e621 post date —\n'
                    'keeps pools/comics in upload order in a file browser)',
            variable=self._match_post_date_var, command=self._on_match_post_date_toggled,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT_TEAL,
            justify='left', font=(theme.UI_FONT, 10))
        self._match_post_date_ck.grid(row=5, column=0, sticky='w', padx=16, pady=(14, 0))

        self._post_date_opts_frame = tk.Frame(p, bg=BG_PANEL)
        self._post_date_opts_frame.grid(row=6, column=0, sticky='ew', padx=16, pady=(6, 0))
        self._post_date_apply_btn = tk.Button(
            self._post_date_opts_frame, text='Apply Post Dates Now', command=self._apply_post_dates_now,
            bg=ACCENT_TEAL, fg=ON_ACCENT, relief='flat', padx=10, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 9, 'bold'),
            activebackground=ACCENT_TEAL, activeforeground=ON_ACCENT,
            borderwidth=0)
        self._post_date_apply_btn.pack(side='left')
        self._post_date_status_lbl = tk.Label(
            self._post_date_opts_frame, text='', bg=BG_PANEL, fg=TEXT_DIM,
            font=(theme.UI_FONT, 9), justify='left', anchor='w', wraplength=320)
        self._post_date_status_lbl.pack(side='left', padx=(10, 0))

        tk.Frame(p, bg=BORDER_COLOR, height=1).grid(
            row=7, column=0, sticky='ew', padx=16, pady=(16, 8))

        self._exclude_sync_var = tk.BooleanVar(value=False)
        self._exclude_sync_ck = tk.Checkbutton(
            p, text="Exclude from sync  (never mirrored by a Sync Master)",
            variable=self._exclude_sync_var, command=self._on_field_changed,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT,
            font=(theme.UI_FONT, 10))
        self._exclude_sync_ck.grid(row=8, column=0, sticky='w', padx=16, pady=(0, 0))

        self._sync_master_var = tk.BooleanVar(value=False)
        self._sync_master_ck = tk.Checkbutton(
            p, text='Sync Master  (mirror every eligible top-level rule under this one)',
            variable=self._sync_master_var, command=self._on_sync_master_toggled,
            bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
            activebackground=BG_PANEL, activeforeground=ACCENT_TEAL,
            font=(theme.UI_FONT, 10, 'bold'))
        self._sync_master_ck.grid(row=9, column=0, sticky='w', padx=16, pady=(14, 0))

        self._sync_opts_frame = tk.Frame(p, bg=BG_PANEL)
        self._sync_opts_frame.grid(row=10, column=0, sticky='ew', padx=16, pady=(6, 0))
        self._sync_opts_frame.columnconfigure(1, weight=1)

        tk.Label(self._sync_opts_frame, text='Prefix:', bg=BG_PANEL, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 9)).grid(row=0, column=0, sticky='w')
        self._sync_prefix_var = tk.StringVar()
        self._sync_prefix_var.trace_add('write', lambda *_: self._on_field_changed())
        tk.Entry(self._sync_opts_frame, textvariable=self._sync_prefix_var, width=16,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10)).grid(
            row=0, column=1, sticky='w', padx=(6, 12))

        self._sync_position_var = tk.StringVar(value='before')
        for i, (label, val) in enumerate([('Before name', 'before'), ('After name', 'after')]):
            tk.Radiobutton(
                self._sync_opts_frame, text=label, value=val,
                variable=self._sync_position_var, command=self._on_field_changed,
                bg=BG_PANEL, fg=TEXT_COLOR, selectcolor=BG_SURFACE,
                activebackground=BG_PANEL, activeforeground=ACCENT,
                font=(theme.UI_FONT, 9)).grid(row=0, column=2 + i, sticky='w', padx=(0, 8))

        self._sync_status_lbl = tk.Label(
            self._sync_opts_frame, text='', bg=BG_PANEL, fg=ACCENT_YELLOW,
            font=(theme.UI_FONT, 9), justify='left', anchor='w', wraplength=320)
        self._sync_status_lbl.grid(row=1, column=0, columnspan=4, sticky='ew', pady=(8, 0))

        self._manage_sync_exclusions_btn = tk.Button(
            self._sync_opts_frame, text='Manage excluded rules…',
            command=self._manage_sync_exclusions,
            bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=8, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 9),
            activebackground=ACCENT, activeforeground=ON_ACCENT, borderwidth=0)
        self._manage_sync_exclusions_btn.grid(row=2, column=2, columnspan=2, sticky='e', pady=(8, 0))

        tk.Button(self._sync_opts_frame, text='Sync Now', command=self._sync_now,
                  bg=ACCENT_TEAL, fg=ON_ACCENT, relief='flat', padx=10, pady=2,
                  cursor='hand2', font=(theme.UI_FONT, 9, 'bold'),
                  activebackground=ACCENT_TEAL, activeforeground=ON_ACCENT,
                  borderwidth=0).grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 0))

    def _manage_sync_exclusions(self) -> None:
        if self._current_node is not None and self._on_manage_sync_exclusions:
            self._on_manage_sync_exclusions(self._current_node.rule)

    def _on_sync_master_toggled(self) -> None:
        if self._sync_master_var.get():
            # A rule can't be a Sync Master and also Special/Pool Sorting.
            self._special_var.set(False)
            self._pool_var.set(False)
        self._update_sync_opts_visibility()
        self._on_field_changed()
        if self._on_category_changed and self._current_node is not None:
            self._on_category_changed(self._current_node)

    def _on_special_toggled(self) -> None:
        if self._special_var.get():
            self._sync_master_var.set(False)
            self._pool_var.set(False)
            self._update_sync_opts_visibility()
        self._on_field_changed()
        if self._on_category_changed and self._current_node is not None:
            self._on_category_changed(self._current_node)

    def _on_pool_toggled(self) -> None:
        if self._pool_var.get():
            self._sync_master_var.set(False)
            self._special_var.set(False)
            self._update_sync_opts_visibility()
            # Pool Sorting rules match by pool ID only — clear any tag criteria
            # so the rule can't silently keep matching on leftover tags.
            self._tw_needed.set_tags([])
            self._tw_can.set_tags([])
            self._tw_excl.set_tags([])
        self._on_field_changed()
        if self._on_category_changed and self._current_node is not None:
            self._on_category_changed(self._current_node)

    def _update_sync_opts_visibility(self) -> None:
        if self._sync_master_var.get():
            self._sync_opts_frame.grid()
        else:
            self._sync_opts_frame.grid_remove()

    def _on_match_post_date_toggled(self) -> None:
        self._update_post_date_opts_visibility()
        self._on_field_changed()

    def _update_post_date_opts_visibility(self) -> None:
        if self._match_post_date_var.get():
            self._post_date_opts_frame.grid()
        else:
            self._post_date_opts_frame.grid_remove()
        self._post_date_status_lbl.config(text='')

    def _apply_post_dates_now(self) -> None:
        """Manually trigger date-matching for this rule's files right now, rather
        than waiting for it to run automatically as part of every full sort."""
        from tkinter import messagebox
        if self._current_node is None or not self._on_apply_post_dates:
            return
        if not messagebox.askyesno(
                'Apply Post Dates Now',
                'This will query the e621 database and scan the source/archive '
                'directories for files matching this rule.\n\n'
                'Files not yet in the correct destination will be moved there; '
                "every matching file's modified/created date will be set to match "
                'its e621 post date.\n\nContinue?', parent=self.winfo_toplevel()):
            return
        rule_ref = self._current_node.rule
        self._post_date_apply_btn.config(state='disabled', text='Applying…')
        self._post_date_status_lbl.config(text='Querying database and scanning files…', fg=TEXT_DIM)
        self._on_apply_post_dates(rule_ref, lambda result, error: self._on_post_dates_applied(rule_ref, result, error))

    def _on_post_dates_applied(self, rule_ref: dict, result: Optional[dict], error: Optional[str]) -> None:
        self._post_date_apply_btn.config(state='normal', text='Apply Post Dates Now')
        # The user may have selected a different rule while this was running —
        # only update the status label if it's still showing for the same rule.
        if self._current_node is None or self._current_node.rule is not rule_ref:
            return
        if error:
            self._post_date_status_lbl.config(text=f'Failed: {error}', fg=ACCENT_RED)
            return
        found = result.get('files_found', 0) if result else 0
        moved = result.get('moved', 0) if result else 0
        dated = result.get('dates_set', 0) if result else 0
        color = ACCENT_TEAL if (found == 0 or dated == found) else ACCENT_YELLOW
        self._post_date_status_lbl.config(
            text=f'Done — {found} file(s) checked, {moved} moved, {dated} date(s) set.',
            fg=color)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Show empty placeholder state (no node loaded)."""
        self._current_node = None
        self._nb.grid_remove()
        self._hdr_frame.grid_remove()
        self._empty_lbl.grid(row=0, column=0, rowspan=2, sticky='nsew')

    def load_node(self, node: RuleNode) -> None:
        """Populate all fields from node.rule — does not fire on_change."""
        self._loading = True
        self._current_node = node
        rule  = node.rule
        depth = node.depth

        # ── Header ───────────────────────────────────────────────────────
        badge = _rule_type_label(rule)
        _fg, badge_bg, badge_fg = TYPE_COLORS.get(badge, TYPE_COLORS['?'])
        self._badge_lbl.config(text=f' {badge} ', bg=badge_bg, fg=badge_fg)
        self._name_lbl.config(text=_truncate(node.display_name(), 72))

        # ── Tags ─────────────────────────────────────────────────────────
        self._tw_needed.set_tags(rule.get('needed_tags') or [])
        self._tw_can.set_tags(rule.get('can_have_tags') or [])
        self._tw_excl.set_tags(rule.get('exclude_tags') or [])
        legacy = rule.get('tags') or []
        if legacy:
            self._legacy_lbl.config(
                text='Legacy "tags" field: ' + ', '.join(str(t) for t in legacy)
                     + '\n(read-only here; edit will write to needed_tags / can_have_tags)')
            self._legacy_frame.grid(row=4, column=0, sticky='ew', padx=10, pady=(0, 6))
        else:
            self._legacy_frame.grid_remove()

        # ── Mirror lock (sync-managed rules are read-only for tag content) ──
        is_mirror = bool(rule.get('mirror_generated'))
        for tw in (self._tw_needed, self._tw_can, self._tw_excl, self._tw_pools):
            tw.set_locked(is_mirror)

        # ── Pool Sorting rules match by pool ID only — hide the Tags tab ────
        is_pool_cat = node_tab_category(node) == 'pool'
        if is_pool_cat:
            if str(self._nb.select()) == str(self._tags_tab):
                self._nb.select(self._pools_tab)
            self._nb.tab(self._tags_tab, state='hidden')
            self._pools_desc_lbl.config(
                text='Matches files whose e621 post belongs to any of these pool IDs.\n'
                     'Pool Sorting rules match by pool ID only — no tag criteria.')
        else:
            self._nb.tab(self._tags_tab, state='normal')
            self._pools_desc_lbl.config(
                text='Matches files whose e621 post belongs to any of these pool IDs.\n'
                     'Combined with tags above using OR — a file matches the rule if it\n'
                     'satisfies the tag criteria OR belongs to one of these pools.')

        is_group_only = (not is_mirror and depth > 0 and node_tab_category(node) == 'group')
        if is_mirror:
            source_name = self._source_display_name(rule.get('mirror_of'))
            self._mirror_lbl.config(
                text=f'🔒 Managed by sync — mirrors "{source_name}". Edit the source rule instead.')
            self._jump_to_source_btn.pack(side='right', padx=8, pady=6)
            self._mirror_banner.grid(row=0, column=0, sticky='ew', padx=10, pady=(8, 0))
        elif is_group_only:
            self._mirror_lbl.config(
                text='Group-only rule — only matches within this Group Sorting tree; '
                     'not mirrored, and has no Main Sorting equivalent.')
            self._jump_to_source_btn.pack_forget()
            self._mirror_banner.grid(row=0, column=0, sticky='ew', padx=10, pady=(8, 0))
        else:
            self._mirror_banner.grid_remove()

        # ── Pools ────────────────────────────────────────────────────────
        self._tw_pools.set_tags([str(p) for p in (rule.get('pool_ids') or [])])
        self._refresh_pool_names_display(rule)

        # ── Destination ───────────────────────────────────────────────────
        if depth == 0:
            self._target_var.set(rule.get('target_directory') or '')
            self._folder_var.set(rule.get('folder_name') or '')
            self._match_all_var.set(bool(rule.get('match_all', False)))
            self._append_media_type_var.set(bool(rule.get('append_media_type_label', False)))
            self._update_append_media_type_state()
            self._sub_frame.grid_remove()
            self._d0_frame.grid(row=0, column=0, sticky='nsew')
        else:
            self._subdir_var.set(rule.get('sub_directory') or '')
            self._d0_frame.grid_remove()
            self._sub_frame.grid(row=0, column=0, sticky='nsew')

        self.refresh_file_count_display()

        # ── Options ───────────────────────────────────────────────────────
        self._enabled_var.set(bool(rule.get('enabled', True)))
        self._exclfav_var.set(bool(rule.get('exclude_favorite_artists', True)))
        self._onlyfav_var.set(bool(rule.get('only_fav_directory', False)))
        self._special_var.set(rule.get('sort_group') == 'special')
        self._pool_var.set(rule.get('sort_group') == 'pool')
        self._match_post_date_var.set(bool(rule.get('match_post_date', False)))
        self._exclude_sync_var.set(bool(rule.get('exclude_from_sync', False)))
        self._sync_master_var.set(bool(rule.get('sync_master', False)))
        self._sync_prefix_var.set(rule.get('sync_prefix') or '')
        self._sync_position_var.set(rule.get('sync_prefix_position') or 'before')
        self._update_sync_opts_visibility()
        self._update_post_date_opts_visibility()
        if depth == 0:
            self._exclfav_ck.grid()
            self._onlyfav_ck.grid()
            self._pool_ck.grid()
            self._special_ck.grid()
            self._match_post_date_ck.grid()
            self._exclude_sync_ck.grid()
            self._sync_master_ck.grid()
            self._refresh_sync_status_label(rule)
        else:
            self._exclfav_ck.grid_remove()
            self._onlyfav_ck.grid_remove()
            self._pool_ck.grid_remove()
            self._special_ck.grid_remove()
            self._match_post_date_ck.grid_remove()
            self._post_date_opts_frame.grid_remove()
            self._exclude_sync_ck.grid_remove()
            self._sync_master_ck.grid_remove()
            self._sync_opts_frame.grid_remove()
            self._sync_status_lbl.config(text='')

        # ── Show panel ───────────────────────────────────────────────────
        self._empty_lbl.grid_remove()
        self._hdr_frame.grid(row=0, column=0, sticky='ew')
        self._nb.grid(row=1, column=0, sticky='nsew')

        self._loading = False

    def _refresh_sync_status_label(self, rule: dict) -> None:
        if not rule.get('sync_master'):
            self._sync_status_lbl.config(text='')
            return
        if self._sync_status_provider:
            self._sync_status_lbl.config(text=self._sync_status_provider(rule))

    def _refresh_pool_names_display(self, rule: dict) -> None:
        """Render the resolved pool_id -> name mapping stored on the rule."""
        pool_ids = [str(p) for p in (rule.get('pool_ids') or [])]
        if not pool_ids:
            self._pool_names_lbl.config(text='', fg=TEXT_DIM)
            return
        names = rule.get('pool_names') or {}
        lines = [f'{pid} → {names[pid]}' if names.get(pid) else f'{pid} → (unresolved)'
                 for pid in pool_ids]
        self._pool_names_lbl.config(text='\n'.join(lines), fg=TEXT_DIM)

    def _fetch_pool_names_clicked(self) -> None:
        if self._current_node is None:
            return
        if not self._on_fetch_pool_names:
            self._pool_names_lbl.config(text='DB lookup unavailable.', fg=ACCENT_RED)
            return
        pool_ids = [int(t) for t in self._tw_pools.get_tags() if str(t).strip().isdigit()]
        if not pool_ids:
            self._pool_names_lbl.config(text='No pool IDs entered.', fg=TEXT_DIM)
            return
        self._pool_names_lbl.config(text='Fetching…', fg=TEXT_DIM)
        self.update_idletasks()
        names, err = self._on_fetch_pool_names(pool_ids)
        rule = self._current_node.rule
        if err:
            self._refresh_pool_names_display(rule)
            self._pool_names_lbl.config(
                text=self._pool_names_lbl.cget('text') + f'\nFetch failed: {err}', fg=ACCENT_RED)
            return
        existing = dict(rule.get('pool_names') or {})
        for pid in pool_ids:
            name = names.get(pid)
            if name:
                existing[str(pid)] = name
        rule['pool_names'] = existing
        self._refresh_pool_names_display(rule)
        missing = [pid for pid in pool_ids if str(pid) not in existing]
        if missing:
            self._pool_names_lbl.config(
                text=self._pool_names_lbl.cget('text')
                     + f'\n(no match in DB for: {", ".join(str(p) for p in missing)})',
                fg=ACCENT_YELLOW)
        # Header/tree summaries embed the pool name — refresh them too.
        self._name_lbl.config(text=_truncate(self._current_node.display_name(), 72))
        if self.on_change:
            self.on_change()

    def save_to_node(self, node: RuleNode) -> None:
        """Write current field values back into node.rule."""
        rule  = node.rule
        depth = node.depth

        rule['needed_tags']   = self._tw_needed.get_tags()
        rule['can_have_tags'] = self._tw_can.get_tags()
        rule['exclude_tags']  = self._tw_excl.get_tags()

        pool_ids = []
        for raw in self._tw_pools.get_tags():
            raw = str(raw).strip()
            if raw.isdigit():
                pool_ids.append(int(raw))
        rule['pool_ids'] = pool_ids
        if rule.get('pool_names'):
            valid_ids = {str(p) for p in pool_ids}
            rule['pool_names'] = {k: v for k, v in rule['pool_names'].items() if k in valid_ids}
            self._refresh_pool_names_display(rule)

        if depth == 0:
            rule['target_directory'] = self._target_var.get().strip()
            rule['folder_name']      = self._folder_var.get().strip()
            rule['match_all']        = self._match_all_var.get()
            # Only persist the flag when no target directory is set
            has_target = bool(self._target_var.get().strip())
            rule['append_media_type_label'] = False if has_target else self._append_media_type_var.get()
        else:
            rule['sub_directory'] = self._subdir_var.get().strip()

        rule['enabled'] = self._enabled_var.get()
        if depth == 0:
            rule['exclude_favorite_artists'] = self._exclfav_var.get()
            rule['only_fav_directory']       = self._onlyfav_var.get()
            if self._pool_var.get():
                rule['sort_group'] = 'pool'
            elif self._special_var.get():
                rule['sort_group'] = 'special'
            else:
                rule['sort_group'] = 'main'
            rule['match_post_date']           = self._match_post_date_var.get()
            rule['exclude_from_sync']        = self._exclude_sync_var.get()
            rule['sync_master']              = self._sync_master_var.get()
            if rule['sync_master']:
                rule['sync_prefix']          = self._sync_prefix_var.get().strip()
                rule['sync_prefix_position'] = self._sync_position_var.get()
            self._refresh_sync_status_label(rule)

        # Keep header display name in sync
        node.refresh_has_children()
        self._name_lbl.config(text=_truncate(node.display_name(), 72))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_append_media_type_state(self) -> None:
        """Enable the append-media-type checkbox only when no target directory is set."""
        has_target = bool(self._target_var.get().strip())
        if has_target:
            self._append_media_type_var.set(False)
            self._append_media_type_ck.config(state='disabled')
        else:
            self._append_media_type_ck.config(state='normal')

    def _on_field_changed(self, *_) -> None:
        if self._loading:
            return
        if self._current_node is not None:
            self.save_to_node(self._current_node)
        if self.on_change:
            self.on_change()

    def _browse_target(self) -> None:
        current = self._target_var.get().strip()
        initial = current if current and Path(current).exists() else str(Path.home())
        chosen  = filedialog.askdirectory(
            title='Choose Target Directory',
            initialdir=initial,
            parent=self.winfo_toplevel())
        if chosen:
            self._target_var.set(chosen.replace('\\', '/'))

    # ------------------------------------------------------------------
    # File-count display
    # ------------------------------------------------------------------

    def refresh_file_count_display(self) -> None:
        """Update the count label(s) for the currently-loaded node from the cache."""
        if self._current_node is None:
            return
        dirs = resolve_node_directories(self._current_node, self._base_dirs)
        lbl = self._d0_count_lbl if self._current_node.depth == 0 else self._sub_count_lbl
        if not dirs:
            lbl.config(text='(destination not resolvable — set a Target Directory, Folder Name, '
                            'or base_directories in tag_sorting.yaml/config.yaml)')
            return
        if len(dirs) == 1:
            count = self._file_counts.get(_normalize_dir_key(dirs[0]))
            suffix = 'not counted yet' if count is None else f'{count:,} file' + ('s' if count != 1 else '')
            lbl.config(text=f'{_short_path(dirs[0], 44)}  —  {suffix}')
        else:
            lines = []
            total = 0
            any_counted = False
            for d in dirs:
                c = self._file_counts.get(_normalize_dir_key(d))
                if c is not None:
                    total += c
                    any_counted = True
                lines.append(f'{_short_path(d, 40)}: ' + (f'{c:,}' if c is not None else '?'))
            header = f'Total: {total:,} file(s)' if any_counted else 'Not counted yet'
            lbl.config(text=header + '\n' + '\n'.join(lines))

    def _recount_current(self) -> None:
        if self._current_node is None or not self._on_recount:
            return
        dirs = resolve_node_directories(self._current_node, self._base_dirs)
        if dirs:
            self._on_recount(dirs)

    # ------------------------------------------------------------------
    # Sync Master helpers
    # ------------------------------------------------------------------

    def _source_display_name(self, rule_id: Optional[str]) -> str:
        if rule_id and self._resolve_source_name:
            name = self._resolve_source_name(rule_id)
            if name:
                return name
        return '(unknown source — may have been deleted)'

    def _jump_to_source(self) -> None:
        if self._current_node is None or not self._on_jump_to_source:
            return
        mirror_of = self._current_node.rule.get('mirror_of')
        if mirror_of:
            self._on_jump_to_source(mirror_of)

    def _sync_now(self) -> None:
        if self._current_node is not None and self._on_sync_requested:
            self._on_sync_requested(self._current_node)


# ---------------------------------------------------------------------------
# Phase 2 / 3 — TagSortConfigApp
# ---------------------------------------------------------------------------

class TagSortConfigApp:
    """Main application window.  Grows phase-by-phase."""

    def __init__(self, root: tk.Tk, config_path: Path):
        self.root        = root
        self.config_path = config_path
        self._data:   dict           = {}
        self._rules:  list           = []
        self._flat:   list[RuleNode] = []
        self._dirty:  bool           = False
        self._file_counts: dict      = {}  # normalized dir -> file count
        self._counting:    bool      = False
        self._out_of_sync_ids: set   = set()  # rule_id set (masters needing sync)
        self._base_dirs: dict        = {}  # pictures/videos/gifs -> path (from config.yaml)

        root.title(f'Tag Sort Configuration — {config_path.name}')
        root.geometry('1280x740')
        root.minsize(820, 480)
        root.configure(bg=BG_COLOR)

        self._build_ui()
        self._wire_tree_callbacks()
        self._bind_shortcuts()
        self._load()
        root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)   # paned row gets all extra space

        # ── Header bar (row 0) ──────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG_HEADER, height=44)
        header.grid(row=0, column=0, sticky='ew')
        header.grid_propagate(False)
        header.columnconfigure(3, weight=1)

        tk.Label(header, text='  Tag Sort Configuration',
                 bg=BG_HEADER, fg=TEXT_BRIGHT,
                 font=(theme.UI_FONT, 14, 'bold')).grid(
            row=0, column=0, sticky='w', pady=8, padx=(4, 0))

        btn_bar = tk.Frame(header, bg=BG_HEADER)
        btn_bar.grid(row=0, column=1, padx=(16, 0), pady=8)
        for text, cmd, accent in [('Save', self._save, True),
                                   ('Reload', self._reload, False)]:
            bg = ACCENT if accent else BG_SURFACE
            fg = BG_COLOR if accent else TEXT_COLOR
            b  = tk.Button(btn_bar, text=text, command=cmd, bg=bg, fg=fg,
                           relief='flat', padx=12, pady=2, cursor='hand2',
                           font=(theme.UI_FONT, 10, 'bold' if accent else 'normal'),
                           activebackground=ACCENT, activeforeground=ON_ACCENT,
                           borderwidth=0)
            orig_bg = bg
            b.bind('<Enter>', lambda e, w=b: w.configure(bg=ACCENT))
            b.bind('<Leave>', lambda e, w=b, c=orig_bg: w.configure(bg=c))
            b.pack(side='left', padx=(0, 4))

        self._add_rule_btn = tk.Button(
            btn_bar, text='+ New Rule', command=self._cmd_add_top_level,
            bg=BG_SURFACE, fg=ACCENT_GREEN, relief='flat', padx=12, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 10, 'bold'),
            activebackground=ACCENT_GREEN, activeforeground=ON_ACCENT, borderwidth=0)
        self._add_rule_btn.pack(side='left', padx=(4, 4))

        # ── Maintenance actions — visually set apart from core editing above ──
        tk.Frame(btn_bar, bg=BORDER_COLOR, width=1).pack(
            side='left', fill='y', padx=8, pady=2)

        self._count_btn = tk.Button(
            btn_bar, text='Count Files', command=self._count_all_directories,
            bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=12, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 10),
            activebackground=ACCENT_TEAL, activeforeground=ON_ACCENT, borderwidth=0)
        self._count_btn.pack(side='left', padx=(0, 4))

        self._sync_all_btn = tk.Button(
            btn_bar, text='Sync Now', command=self._cmd_sync_all_masters,
            bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=12, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 10),
            activebackground=ACCENT_TEAL, activeforeground=ON_ACCENT, borderwidth=0)
        self._sync_all_btn.pack(side='left', padx=(0, 4))

        # ── Rare one-off migration helper — dimmer, kept apart from routine ops ──
        tk.Frame(btn_bar, bg=BORDER_COLOR, width=1).pack(
            side='left', fill='y', padx=8, pady=2)

        self._migrate_special_btn = tk.Button(
            btn_bar, text='Move Pre-Group → Special', command=self._cmd_migrate_pre_group_to_special,
            bg=BG_HEADER, fg=TEXT_DIM, relief='flat', padx=12, pady=2,
            cursor='hand2', font=(theme.UI_FONT, 9),
            activebackground=ACCENT_YELLOW, activeforeground=ON_ACCENT, borderwidth=0)
        self._migrate_special_btn.pack(side='left', padx=(0, 4))

        self._dirty_lbl = tk.Label(header, text='', bg=BG_HEADER,
                                   fg=ACCENT_YELLOW, font=(theme.UI_FONT, 10, 'bold'))
        self._dirty_lbl.grid(row=0, column=2, sticky='w', padx=(8, 0))

        filter_frame = tk.Frame(header, bg=BG_HEADER)
        filter_frame.grid(row=0, column=3, sticky='e', padx=(0, 12), pady=8)
        tk.Label(filter_frame, text='Filter:', bg=BG_HEADER, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 10)).pack(side='left', padx=(0, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add('write', lambda *_: self._on_filter())
        tk.Entry(filter_frame, textvariable=self._filter_var,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10), width=22).pack(side='left')

        # ── Global settings bar (row 1) ─────────────────────────────────
        gbar = tk.Frame(self.root, bg=BG_SURFACE)
        gbar.grid(row=1, column=0, sticky='ew')

        self._gs_enabled_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            gbar, text='Tag Sorting Enabled',
            variable=self._gs_enabled_var, command=self._on_gs_changed,
            bg=BG_SURFACE, fg=TEXT_COLOR, selectcolor=BG_COLOR,
            activebackground=BG_SURFACE, activeforeground=ACCENT,
            font=(theme.UI_FONT, 10)).pack(side='left', padx=(10, 14), pady=5)

        tk.Frame(gbar, bg=BORDER_COLOR, width=1).pack(side='left', fill='y', pady=4)

        tk.Label(gbar, text='  Subdirectory:', bg=BG_SURFACE, fg=TEXT_DIM,
                 font=(theme.UI_FONT, 9)).pack(side='left')
        self._gs_subdir_var = tk.StringVar()
        self._gs_subdir_var.trace_add('write', lambda *_: self._on_gs_changed())
        tk.Entry(gbar, textvariable=self._gs_subdir_var, width=20,
                 bg=BG_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 10)).pack(
            side='left', padx=(4, 14), pady=5)

        tk.Frame(gbar, bg=BORDER_COLOR, width=1).pack(side='left', fill='y', pady=4)

        self._db_visible = False
        self._db_toggle_btn = tk.Button(
            gbar, text='  DB Settings \u25ba  ',
            command=self._toggle_db_settings,
            bg=BG_SURFACE, fg=TEXT_DIM, relief='flat',
            font=(theme.UI_FONT, 9), cursor='hand2',
            activebackground=BG_COLOR, activeforeground=ACCENT,
            borderwidth=0)
        self._db_toggle_btn.pack(side='left', padx=(8, 0), pady=5)

        # DB settings panel (collapsed; inserted at row 2 when open)
        self._db_frame = tk.Frame(self.root, bg=BG_SURFACE)
        db_fields = tk.Frame(self._db_frame, bg=BG_SURFACE)
        db_fields.pack(fill='x', padx=10, pady=5)
        self._db_vars: dict = {}
        for col, (lbl, key, w) in enumerate([
            ('Host',     'host',     14),
            ('Port',     'port',     6),
            ('User',     'user',     12),
            ('Password', 'password', 12),
            ('Database', 'database', 18),
        ]):
            tk.Label(db_fields, text=lbl + ':', bg=BG_SURFACE, fg=TEXT_DIM,
                     font=(theme.UI_FONT, 9)).grid(row=0, column=col * 2,
                                                sticky='w', padx=(8, 2))
            v = tk.StringVar()
            v.trace_add('write', lambda *_: self._on_gs_changed())
            self._db_vars[key] = v
            tk.Entry(db_fields, textvariable=v, width=w,
                     bg=BG_COLOR, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                     relief='flat', font=(theme.UI_FONT, 9),
                     show='*' if key == 'password' else '').grid(
                row=0, column=col * 2 + 1, sticky='ew', padx=(0, 4))

        # ── Main pane (row 2 normally, row 3 when DB panel open) ────────
        self._paned = tk.PanedWindow(
            self.root, orient='horizontal',
            bg=BORDER_COLOR, sashwidth=4, sashrelief='flat', handlepad=0)
        self._paned.grid(row=2, column=0, sticky='nsew')

        # Left side: tab strip (Special / Group / Main Sorting) + shared tree.
        # The tree stays a single widget — switching tabs just re-filters which
        # top-level rules (and their sub-rule trees) are visible.
        tree_container = tk.Frame(self._paned, bg=BG_COLOR)
        tree_container.columnconfigure(0, weight=1)
        tree_container.rowconfigure(1, weight=1)

        tab_bar = tk.Frame(tree_container, bg=BG_HEADER)
        tab_bar.grid(row=0, column=0, sticky='ew')
        self._tab_buttons: dict = {}
        self._active_tab = 'main'
        for key in ('pool', 'special', 'group', 'main'):
            b = tk.Button(
                tab_bar, text=TAB_LABELS[key], command=lambda k=key: self._switch_tab(k),
                bg=BG_HEADER, fg=TEXT_DIM, relief='flat', padx=12, pady=6,
                cursor='hand2', font=(theme.UI_FONT, 10),
                activebackground=ROW_HOVER, activeforeground=TEXT_BRIGHT, borderwidth=0)
            b.pack(side='left')
            self._tab_buttons[key] = b

        self._tree = RuleTreePanel(tree_container)
        self._tree.grid(row=1, column=0, sticky='nsew')

        self._paned.add(tree_container, minsize=380, stretch='always')

        self._edit_panel = RuleEditPanel(
            self._paned, on_change=self._mark_dirty,
            file_counts=self._file_counts, on_recount=self._recount_directories,
            on_sync_requested=self._sync_single_master,
            on_jump_to_source=self._jump_to_rule,
            resolve_source_name=self._resolve_rule_name,
            sync_status_provider=self._sync_status_text,
            base_dirs=self._base_dirs,
            on_category_changed=self._on_rule_category_changed,
            on_fetch_pool_names=self._fetch_pool_names_for_node,
            on_apply_post_dates=self._apply_post_dates_for_rule,
            on_manage_sync_exclusions=self._manage_sync_exclusions)
        self._paned.add(self._edit_panel, minsize=320, stretch='always')

        # ── Status bar (row 3) ──────────────────────────────────────────
        self._status_var = tk.StringVar(value='Ready')
        tk.Label(self.root, textvariable=self._status_var, anchor='w',
                 bg=BG_HEADER, fg=TEXT_DIM, font=(theme.UI_FONT, 9),
                 padx=10, pady=2).grid(row=3, column=0, sticky='ew')

    # ------------------------------------------------------------------
    # Phase 3 — Wire tree callbacks
    # ------------------------------------------------------------------

    def _wire_tree_callbacks(self):
        self._tree.on_select   = self._on_tree_select
        self._tree.on_toggle   = self._on_tree_toggle
        self._tree.on_activate = self._on_tree_activate
        self._tree.on_delete   = self._cmd_delete
        self._tree.on_context  = self._show_context_menu
        self._tree.on_drop     = self._on_tree_drop

    def _bind_shortcuts(self):
        r = self.root
        r.bind('<Control-s>', lambda e: self._save())
        r.bind('<Control-S>', lambda e: self._save())
        r.bind('<Control-n>', lambda e: self._cmd_add())
        r.bind('<Control-N>', lambda e: self._cmd_add())
        r.bind('<Control-Shift-n>', lambda e: self._cmd_add_sub())
        r.bind('<Control-Shift-N>', lambda e: self._cmd_add_sub())
        r.bind('<Control-d>', lambda e: self._cmd_duplicate())
        r.bind('<Control-D>', lambda e: self._cmd_duplicate())
        r.bind('<Control-z>', lambda e: self._reload())

    # ------------------------------------------------------------------
    # Phase 6 — Global settings helpers
    # ------------------------------------------------------------------

    def _toggle_db_settings(self):
        self._db_visible = not self._db_visible
        if self._db_visible:
            self._db_frame.grid(row=2, column=0, sticky='ew')
            self._paned.grid_configure(row=3)
            self.root.rowconfigure(2, weight=0)
            self.root.rowconfigure(3, weight=1)
            self._db_toggle_btn.config(text='  DB Settings ▼  ')
        else:
            self._db_frame.grid_remove()
            self._paned.grid_configure(row=2)
            self.root.rowconfigure(2, weight=1)
            self.root.rowconfigure(3, weight=0)
            self._db_toggle_btn.config(text='  DB Settings ►  ')

    def _on_gs_changed(self, *_):
        """Any global-setting field changed — mark dirty without rebuilding tree."""
        self._mark_dirty()
        # tag_sort_subdirectory affects auto-constructed split-mode paths
        live_data = dict(self._data)
        live_data['tag_sort_subdirectory'] = self._gs_subdir_var.get().strip()
        computed = get_global_base_dirs(live_data)
        self._base_dirs.clear()
        self._base_dirs.update(computed)
        self._tree.set_base_dirs(self._base_dirs)
        self._edit_panel.refresh_file_count_display()

    def _load_global_settings(self):
        """Populate global settings bar from self._data."""
        self._gs_enabled_var.set(bool(self._data.get('enabled', True)))
        self._gs_subdir_var.set(str(self._data.get('tag_sort_subdirectory', '') or ''))
        db = self._data.get('e621_database') or {}
        for key, var in self._db_vars.items():
            var.set(str(db.get(key) or ''))

    def _save_global_settings(self):
        """Write global settings bar values back into self._data."""
        self._data['enabled'] = self._gs_enabled_var.get()
        subdir = self._gs_subdir_var.get().strip()
        if subdir:
            self._data['tag_sort_subdirectory'] = subdir
        else:
            self._data.pop('tag_sort_subdirectory', None)
        db = {}
        for key, var in self._db_vars.items():
            val = var.get().strip()
            db[key] = val if val else None
        self._data['e621_database'] = db

    def _fetch_pool_names_for_node(self, pool_ids: list) -> tuple:
        """callable(pool_ids) -> (names_dict, error_str_or_None), passed to RuleEditPanel."""
        try:
            db_config = resolve_e621_db_config(self._data)
            return fetch_pool_names(pool_ids, db_config), None
        except Exception as e:
            return {}, str(e)

    def _apply_post_dates_for_rule(self, rule: dict, done_callback) -> None:
        """Manual 'Apply Post Dates Now' trigger — runs this one rule's own
        tag/pool matcher with set_post_date=True in a background thread, so
        the UI doesn't freeze while it queries the DB and scans directories.

        This is a real (non-dry-run) pass: files not yet at their destination
        get moved there as normal; every matched file — moved or already in
        place — gets its modified/created date set to match its e621 post.
        done_callback(result_dict_or_None, error_str_or_None) is invoked on
        the main thread via self.root.after() once it finishes.
        """
        # Imported here (on the main thread, before the worker thread starts)
        # rather than inside worker(): importing _handlers for the first time
        # runs _handlers/__init__.py, which imports hash_manager.py, which
        # calls signal.signal() at module scope — and signal.signal() only
        # works from the main thread. Doing the import here first means it's
        # already cached in sys.modules by the time the worker thread runs.
        # fff: this really sorts files, but the rule comes from the raw (un-rerouted) editor
        # data; reroute it so a test-environment run cannot move files into real folders.
        rule = _rule_for_env(rule)
        try:
            from fff.sorter.config import Config as FFFConfig
            from fff.sorter.features.tag_sorter import TagSorter
            from fff.sorter.handlers.database_manager import DatabaseManager
        except Exception as e:
            self.root.after(0, lambda: done_callback(None, str(e)))
            return

        def worker():
            result, error = None, None
            try:
                main_cfg = FFFConfig(str(DEFAULT_MAIN_CONFIG_PATH))
                db_config = main_cfg.get_e621_database_config()
                db_manager = DatabaseManager(main_cfg)
                tag_sorter = TagSorter(main_cfg, db_config, db_manager=db_manager)

                needed_tags = rule.get('needed_tags') or None
                can_have_tags = rule.get('can_have_tags') or None
                legacy_tags = rule.get('tags') or None
                pool_ids = rule.get('pool_ids') or None
                target_dir = rule.get('target_directory') or None
                rule_base_dirs = rule.get('base_directories') or {}
                tag_global_base_dirs = main_cfg.get_tag_sorting_base_dirs()
                effective_base_dirs = rule_base_dirs if any(rule_base_dirs.values()) else tag_global_base_dirs
                folder_name = rule.get('folder_name') or (os.path.basename(target_dir) if target_dir else None)
                use_split_mode = bool(not target_dir and any(effective_base_dirs.values()) and folder_name)

                result = tag_sorter.sort_by_tags(
                    tags=legacy_tags if legacy_tags and not needed_tags and not can_have_tags else None,
                    needed_tags=needed_tags,
                    can_have_tags=can_have_tags,
                    pool_ids=pool_ids,
                    destination_dir=target_dir if not use_split_mode else None,
                    base_directories=effective_base_dirs if use_split_mode else {},
                    folder_name=folder_name if use_split_mode else None,
                    match_all=rule.get('match_all', False),
                    exclude_favorite_artists=rule.get('exclude_favorite_artists', False),
                    exclude_tags=rule.get('exclude_tags') or [],
                    only_fav_directory=rule.get('only_fav_directory', False),
                    sub_rules=rule.get('sub_rules') or [],
                    dry_run=False,
                    append_media_type_label=rule.get('append_media_type_label', False),
                    set_post_date=True,
                )
            except Exception as e:
                error = str(e)
            self.root.after(0, lambda: done_callback(result, error))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Phase 3 — Tree event handlers
    # ------------------------------------------------------------------

    def _on_tree_select(self, flat_idx: int):
        node = self._flat[flat_idx]
        self._edit_panel.load_node(node)
        self._status(f'Selected: {node.display_name()}')

    def _on_tree_toggle(self, flat_idx: int):
        """Rebuild visible rows after a collapse/expand and redraw."""
        cs = extract_collapse_state(self._flat)
        self._flat = build_flat_list(self._rules, _collapse_state=cs)
        self._tree.refresh(self._flat, selected_idx=flat_idx)
        node = self._flat[flat_idx]
        state = 'collapsed' if node.collapsed else 'expanded'
        self._status(f'{state.capitalize()}: {node.display_name()}')

    def _on_tree_activate(self, flat_idx: int):
        """Double-click / Enter — load node in editor and focus tags entry."""
        node = self._flat[flat_idx]
        self._edit_panel.load_node(node)
        self._edit_panel._nb.select(0)       # switch to Tags tab
        self._edit_panel._tw_needed.focus_entry()
        self._status(f'Editing: {node.display_name()}')

    def _on_tree_drop(self, src_flat_idx: int, dst_flat_idx: int,
                      zone: str, copy: bool):
        """Called when DnD drop is completed on the tree."""
        src_name = self._flat[src_flat_idx].display_name()
        new_idx  = move_node(self._flat, src_flat_idx, dst_flat_idx,
                             zone, self._rules, copy=copy)
        self._mark_dirty()
        self._refresh_tree(new_idx)
        action = 'Copied' if copy else 'Moved'
        self._status(f'{action}: {src_name}')

    # ------------------------------------------------------------------
    # Phase 3 — Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, flat_idx: int, x: int, y: int):
        from tkinter import messagebox
        node = self._flat[flat_idx]
        is_depth0 = (node.depth == 0)

        menu = tk.Menu(self.root, tearoff=0,
                       bg=BG_SURFACE, fg=TEXT_COLOR,
                       activebackground=ROW_SELECTED, activeforeground=TEXT_BRIGHT,
                       relief='flat', borderwidth=1)

        menu.add_command(label='Edit          Enter',
                         command=lambda: self._on_tree_activate(flat_idx))
        menu.add_command(label='Add Rule      Ctrl+N',
                         command=lambda: self._cmd_add(flat_idx))
        sub_label = ('Add Group-Only Sub-Rule  Ctrl+Shift+N' if node_tab_category(node) == 'group'
                    else 'Add Sub-Rule  Ctrl+Shift+N')
        menu.add_command(label=sub_label,
                         command=lambda: self._cmd_add_sub(flat_idx))
        menu.add_command(label='Duplicate     Ctrl+D',
                         command=lambda: self._cmd_duplicate(flat_idx))
        menu.add_separator()
        menu.add_command(label='Toggle On / Off',
                         command=lambda: self._cmd_toggle(flat_idx))
        menu.add_separator()
        if is_depth0:
            current_cat = rule_tab_category(node.rule)
            if current_cat != 'pool':
                menu.add_command(label='Move to Pool Sorting',
                                 command=lambda: self._cmd_set_sort_group(flat_idx, 'pool'))
            if current_cat != 'special':
                menu.add_command(label='Move to Special Sorting',
                                 command=lambda: self._cmd_set_sort_group(flat_idx, 'special'))
            if current_cat != 'main':
                menu.add_command(label='Move to Main Sorting',
                                 command=lambda: self._cmd_set_sort_group(flat_idx, 'main'))
            menu.add_separator()
        menu.add_command(label='Move Up',
                         command=lambda: self._cmd_move(flat_idx, -1))
        menu.add_command(label='Move Down',
                         command=lambda: self._cmd_move(flat_idx, +1))
        menu.add_separator()
        if not is_depth0:
            menu.add_command(label='Promote ↗  (make main rule)',
                             command=lambda: self._cmd_promote(flat_idx))
        if is_depth0:
            menu.add_command(label='↓ Make Sub-Rule of…',
                             command=lambda: self._cmd_demote(flat_idx))
        menu.add_separator()
        menu.add_command(label='Delete',
                         foreground=ACCENT_RED,
                         activeforeground=ACCENT_RED,
                         command=lambda: self._cmd_delete(flat_idx))

        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------
    # Phase 3 — Action commands
    # ------------------------------------------------------------------

    def _selected_flat_idx(self) -> Optional[int]:
        return self._tree._selected_idx

    def _cmd_add_top_level(self) -> None:
        """Add a brand-new TOP-LEVEL rule in the active tab (the '+ New Rule' button).

        Ignores the current selection unless it's already a top-level rule, so it
        never accidentally nests the new rule as a sub-rule of whatever's selected.
        """
        sel = self._selected_flat_idx()
        if sel is not None and self._flat[sel].depth == 0:
            self._cmd_add(sel)
        else:
            # _cmd_add(None) would otherwise re-derive the (sub-rule) selection
            # itself via _selected_flat_idx() — clear it first so it truly appends.
            self._tree.set_selected(None)
            self._cmd_add(None)

    def _refresh_tree(self, select_flat_idx: Optional[int] = None):
        """Rebuild flat list preserving collapse state, redraw tree + edit panel.

        Also enforces the Special/Group/Main tab block ordering — since that can
        shift flat-index positions, the target selection is tracked by rule
        identity (captured before reordering) rather than by raw index.
        """
        idx_hint = select_flat_idx if select_flat_idx is not None else self._tree._selected_idx
        target_rule = None
        if idx_hint is not None and 0 <= idx_hint < len(self._flat):
            target_rule = self._flat[idx_hint].rule

        enforce_tab_order(self._rules)

        cs = extract_collapse_state(self._flat)
        self._flat = build_flat_list(self._rules, _collapse_state=cs)

        sel = None
        if target_rule is not None:
            sel = next((i for i, n in enumerate(self._flat) if n.rule is target_rule), None)
        if sel is None and idx_hint is not None and self._flat:
            sel = max(0, min(idx_hint, len(self._flat) - 1))

        self._recompute_sync_status()
        self._update_tab_buttons()
        self._tree.refresh(self._flat, selected_idx=sel)
        # Phase 4: keep edit panel in sync
        if sel is not None and self._flat:
            self._edit_panel.load_node(self._flat[sel])
        else:
            self._edit_panel.clear()

    def _switch_tab(self, key: str) -> None:
        if key == self._active_tab:
            return
        self._active_tab = key
        self._tree.set_category(key)
        self._edit_panel.clear()
        self._update_tab_buttons()
        self._status(f'Viewing: {TAB_LABELS[key]}')

    def _update_tab_buttons(self) -> None:
        counts = {'pool': 0, 'special': 0, 'group': 0, 'main': 0}
        for r in self._rules:
            counts[rule_tab_category(r)] += 1
        for key, btn in self._tab_buttons.items():
            active = (key == self._active_tab)
            btn.config(
                text=f'{TAB_LABELS[key]} ({counts[key]})',
                bg=BG_SURFACE if active else BG_HEADER,
                fg=ACCENT if active else TEXT_DIM,
                font=(theme.UI_FONT, 10, 'bold' if active else 'normal'))

    def _on_rule_category_changed(self, node: RuleNode) -> None:
        """A rule's Special/Sync-Master flag was toggled — follow it to its new tab."""
        new_category = rule_tab_category(node.rule)
        if new_category == 'group':
            # Capture the "before this rule" boundary NOW, before enforce_tab_order
            # reshuffles the list and permanently loses that positional signal.
            self._offer_pre_group_migration(node.rule)
        if new_category != self._active_tab:
            self._active_tab = new_category
            self._tree.set_category(new_category)
        self._mark_dirty()
        rule_ref = node.rule
        cs = extract_collapse_state(self._flat)
        enforce_tab_order(self._rules)
        self._flat = build_flat_list(self._rules, _collapse_state=cs)
        self._recompute_sync_status()
        self._update_tab_buttons()
        new_idx = next((i for i, n in enumerate(self._flat) if n.rule is rule_ref), None)
        self._tree.refresh(self._flat, selected_idx=new_idx)
        if new_idx is not None:
            self._edit_panel.load_node(self._flat[new_idx])

    def _offer_pre_group_migration(self, master_rule: dict) -> None:
        """When a rule newly becomes a Sync Master, offer to mark everything that was
        positioned before it (in the not-yet-reordered list) as Special Sorting."""
        from tkinter import messagebox
        try:
            idx = self._rules.index(master_rule)
        except ValueError:
            return
        candidates = [r for r in self._rules[:idx] if rule_tab_category(r) == 'main']
        if not candidates:
            return
        names = [_rule_display_name(r) for r in candidates[:15]]
        more = f'\n... and {len(candidates) - 15} more' if len(candidates) > 15 else ''
        msg = (f'"{_rule_display_name(master_rule)}" is now a Sync Master (Group Sorting).\n\n'
              f'{len(candidates)} rule(s) were positioned before it — mark them as Special '
              f'Sorting now so they keep running first?\n\n' + '\n'.join(names) + more)
        if messagebox.askyesno('Preserve Run Order?', msg, parent=self.root):
            for r in candidates:
                r['sort_group'] = 'special'

    def _cmd_add(self, flat_idx: Optional[int] = None):
        """Add a new blank rule after the selected one (or at end), in the active tab."""
        new_rule = {
            'rule_id': str(uuid.uuid4()),
            'needed_tags': [],
            'can_have_tags': [],
            'exclude_tags': [],
            'tags': [],
            'pool_ids': [],
            'target_directory': '',
            'folder_name': '',
            'exclude_favorite_artists': True,
            'only_fav_directory': False,
            'enabled': True,
            'match_all': False,
            'sub_rules': [],
            'sort_group': self._active_tab if self._active_tab in ('special', 'pool') else 'main',
        }
        if self._active_tab == 'group':
            new_rule['sync_master'] = True
            new_rule['sync_prefix'] = ''
            new_rule['sync_prefix_position'] = 'before'
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is not None and idx < len(self._flat):
            new_flat_idx = insert_node_after(self._flat, idx, new_rule, self._rules)
        else:
            self._rules.append(new_rule)
            cs = extract_collapse_state(self._flat)
            self._flat = build_flat_list(self._rules, _collapse_state=cs)
            new_flat_idx = len(self._flat) - 1
        self._mark_dirty()
        self._refresh_tree(new_flat_idx)
        self._status(f'Added new rule at position {new_flat_idx + 1}')

    def _cmd_add_sub(self, flat_idx: Optional[int] = None):
        """Add a new blank sub-rule as a child of the selected node."""
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            messagebox.showinfo('No Selection', 'Select a rule to add a sub-rule to.',
                                parent=self.root)
            return
        new_sub = {
            'rule_id': str(uuid.uuid4()),
            'needed_tags': [],
            'can_have_tags': [],
            'exclude_tags': [],
            'tags': [],
            'pool_ids': [],
            'sub_directory': '',
            'enabled': True,
            'sub_rules': [],
        }
        parent_label = self._flat[idx].display_name()
        new_flat_idx = insert_node_into(self._flat, idx, new_sub, self._rules)
        self._mark_dirty()
        self._refresh_tree(new_flat_idx)
        self._status(f'Added sub-rule under: {parent_label}')


    def _cmd_duplicate(self, flat_idx: Optional[int] = None):
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            messagebox.showinfo('No Selection', 'Select a rule first.', parent=self.root)
            return
        node     = self._flat[idx]
        # Deep-copy the entire rule dict, including all nested sub_rules (even
        # from collapsed nodes that are absent from the flat list).
        new_rule = deepcopy(node.rule)

        # Assign fresh rule_ids throughout the copy so it doesn't collide with
        # the original for sync-mirror lookups (a duplicate is never a mirror).
        def _reassign_ids(rule):
            rule['rule_id'] = str(uuid.uuid4())
            rule.pop('mirror_of', None)
            rule.pop('mirror_generated', None)
            for sub in (rule.get('sub_rules') or []):
                _reassign_ids(sub)
        _reassign_ids(new_rule)

        # Build RuleNode objects for the full copied subtree so that write_back
        # can reconstruct sub_rules correctly from the node-parent relationships.
        def _build_nodes(rule, depth, parent):
            n = RuleNode(rule=rule, depth=depth, parent=parent,
                         index_in_parent=0, collapsed=False)
            result = [n]
            for sub in (rule.get('sub_rules') or []):
                result.extend(_build_nodes(sub, depth + 1, n))
            return result

        new_nodes = _build_nodes(new_rule, node.depth, node.parent)
        # Strip sub_rules from dicts — write_back will rebuild them from the
        # node-parent tree we just constructed.
        for nn in new_nodes:
            nn.rule.pop('sub_rules', None)

        insert_at = _subtree_end(self._flat, idx) + 1
        for i, nn in enumerate(new_nodes):
            self._flat.insert(insert_at + i, nn)
        write_back(self._flat, self._rules)
        self._mark_dirty()
        self._refresh_tree(insert_at)
        self._status(f'Duplicated: {node.display_name()}')

    def _cmd_toggle(self, flat_idx: Optional[int] = None):
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            messagebox.showinfo('No Selection', 'Select a rule first.', parent=self.root)
            return
        node = self._flat[idx]
        node.rule['enabled'] = not node.rule.get('enabled', True)
        self._mark_dirty()
        self._refresh_tree(idx)
        state = 'enabled' if node.rule['enabled'] else 'disabled'
        self._status(f'{state.capitalize()}: {node.display_name()}')

    def _cmd_set_sort_group(self, flat_idx: int, group: str) -> None:
        """Quick-move a top-level rule to Pool, Special, or Main Sorting from the context menu."""
        node = self._flat[flat_idx]
        if node.depth != 0:
            return
        node.rule['sort_group'] = group
        if group in ('special', 'pool'):
            node.rule['sync_master'] = False
        if group == 'pool':
            # Pool Sorting rules match by pool ID only — clear any tag criteria.
            node.rule['needed_tags'] = []
            node.rule['can_have_tags'] = []
            node.rule['exclude_tags'] = []
            node.rule['tags'] = []
        self._on_rule_category_changed(node)
        self._status(f'Moved to {TAB_LABELS[rule_tab_category(node.rule)]}: {node.display_name()}')

    def _cmd_migrate_pre_group_to_special(self) -> None:
        """Bulk-move every top-level rule currently positioned before the first
        Group Sorting (Sync Master) rule into Special Sorting."""
        from tkinter import messagebox
        first_group_idx = next((i for i, r in enumerate(self._rules) if r.get('sync_master')), None)
        if first_group_idx is None:
            messagebox.showinfo('No Group Sorting Rules',
                                'There are no Sync Master rules yet — nothing to move.',
                                parent=self.root)
            return
        candidates = [r for r in self._rules[:first_group_idx]
                     if rule_tab_category(r) == 'main']
        if not candidates:
            messagebox.showinfo('Nothing to Move',
                                'All rules before the first Group Sorting rule are '
                                'already marked Special.', parent=self.root)
            return
        names = [_rule_display_name(r) for r in candidates[:15]]
        more = f'\n... and {len(candidates) - 15} more' if len(candidates) > 15 else ''
        msg = (f'Move {len(candidates)} rule(s) currently positioned before the first '
              f'Group Sorting rule into Special Sorting?\n\n' + '\n'.join(names) + more)
        if not messagebox.askyesno('Move to Special Sorting', msg, parent=self.root):
            return
        for r in candidates:
            r['sort_group'] = 'special'
        self._mark_dirty()
        self._active_tab = 'special'
        self._tree.set_category('special')
        self._refresh_tree()
        self._status(f'Moved {len(candidates)} rule(s) to Special Sorting')

    def _cmd_move(self, flat_idx: Optional[int] = None, delta: int = 0):
        """Move a node up (-1) or down (+1) within its sibling list."""
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None or delta == 0:
            return
        node     = self._flat[idx]
        depth    = node.depth
        parent   = node.parent

        # Find previous/next sibling in flat list
        siblings = [(fi, n) for fi, n in enumerate(self._flat)
                    if n.depth == depth and n.parent is parent]
        pos_in_siblings = next((i for i, (fi, _) in enumerate(siblings) if fi == idx), None)
        if pos_in_siblings is None:
            return

        target_sib_pos = pos_in_siblings + delta
        if target_sib_pos < 0 or target_sib_pos >= len(siblings):
            return  # already at boundary

        dst_flat_idx, _ = siblings[target_sib_pos]
        zone = 'before' if delta < 0 else 'after'
        new_idx = move_node(self._flat, idx, dst_flat_idx, zone, self._rules)
        self._mark_dirty()
        self._refresh_tree(new_idx)
        self._status(f'Moved {"up" if delta < 0 else "down"}: {node.display_name()}')

    def _cmd_promote(self, flat_idx: Optional[int] = None):
        """Promote a sub-rule to a top-level rule inserted after its parent."""
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            return
        node = self._flat[idx]
        if node.depth == 0:
            messagebox.showinfo('Already Top-Level',
                                'This rule is already a main rule.', parent=self.root)
            return

        # Find the parent's flat index
        parent_node   = node.parent
        parent_flat   = next((fi for fi, n in enumerate(self._flat) if n is parent_node), None)

        # Remove from sub-rule position; rebuild so parent's sub_rules list is updated
        removed = remove_node(self._flat, idx, self._rules)
        root_rule = deepcopy(removed[0].rule)
        # Carry children — they're already embedded in root_rule via write_back
        # Convert sub-rule-only fields to main-rule fields
        if 'sub_directory' in root_rule and not root_rule.get('target_directory'):
            root_rule['target_directory'] = root_rule.pop('sub_directory', '')
        root_rule.setdefault('folder_name', '')
        root_rule.setdefault('exclude_favorite_artists', True)
        root_rule.setdefault('only_fav_directory', False)
        root_rule.setdefault('match_all', False)

        # Re-adjust parent flat idx after removal
        if parent_flat is not None and parent_flat >= len(self._flat):
            parent_flat = len(self._flat) - 1

        if parent_flat is not None:
            new_idx = insert_node_after(self._flat, parent_flat, root_rule, self._rules)
        else:
            self._rules.append(root_rule)
            cs = extract_collapse_state(self._flat)
            self._flat = build_flat_list(self._rules, _collapse_state=cs)
            new_idx = len(self._flat) - 1

        self._mark_dirty()
        self._refresh_tree(new_idx)
        self._status(f'Promoted to main rule: {_rule_tag_summary(root_rule)}')

    def _cmd_demote(self, flat_idx: Optional[int] = None):
        """Demote a main rule to a sub-rule of a chosen parent."""
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            return
        node = self._flat[idx]
        if node.depth != 0:
            messagebox.showinfo('Not a Main Rule',
                                'Only top-level rules can be demoted.', parent=self.root)
            return

        # Build a list of candidate parents (all other depth-0 nodes)
        candidates = [(fi, n) for fi, n in enumerate(self._flat)
                      if n.depth == 0 and fi != idx]
        if not candidates:
            messagebox.showinfo('No Candidates',
                                'No other top-level rules to use as parent.',
                                parent=self.root)
            return

        # Simple dialog: pick parent + sub_directory name
        dlg = _DemoteDialog(self.root, node, candidates)
        if not dlg.result:
            return
        parent_flat_idx, sub_dir = dlg.result

        # Build a sub-rule dict from the current rule
        sub_rule = deepcopy(node.rule)
        sub_rule['sub_directory'] = sub_dir
        # Remove main-rule-only fields
        for key in ('target_directory', 'folder_name',
                    'exclude_favorite_artists', 'only_fav_directory', 'match_all'):
            sub_rule.pop(key, None)

        # Remove the node from top level, then insert into parent
        remove_node(self._flat, idx, self._rules)
        # Re-find parent after removal (its flat index may have shifted)
        parent_node = candidates[0][1] if not candidates else None
        for fi, n in enumerate(self._flat):
            if n is candidates[next(
                (i for i, (cfi, _) in enumerate(candidates) if cfi == parent_flat_idx),
                0)][1]:
                parent_flat_idx = fi
                parent_node = n
                break

        new_idx = insert_node_into(self._flat, parent_flat_idx, sub_rule, self._rules)
        self._mark_dirty()
        self._refresh_tree(new_idx)
        self._status(f'Demoted to sub-rule /{sub_dir} of {parent_node.display_name() if parent_node else "?"}')

    def _cmd_delete(self, flat_idx: Optional[int] = None):
        from tkinter import messagebox
        idx = flat_idx if flat_idx is not None else self._selected_flat_idx()
        if idx is None:
            messagebox.showinfo('No Selection', 'Select a rule first.', parent=self.root)
            return
        node = self._flat[idx]
        sub_count = _subtree_end(self._flat, idx) - idx
        detail = f' and {sub_count} child node(s)' if sub_count else ''
        if not messagebox.askyesno('Delete Rule',
                                   f'Delete "{node.display_name()}"{detail}?',
                                   parent=self.root):
            return
        remove_node(self._flat, idx, self._rules)
        new_sel = min(idx, len(self._flat) - 1) if self._flat else None
        self._mark_dirty()
        self._refresh_tree(new_sel)
        self._status(f'Deleted: {node.display_name()}')

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _load(self):
        from tkinter import messagebox
        try:
            self._data = _load_yaml(self.config_path)
        except FileNotFoundError:
            self._data = {'enabled': True, 'e621_database': {}, 'rules': []}
        except Exception as exc:
            messagebox.showerror('Load Error', f'Failed to load config:\n{exc}')
            self._data = {'enabled': True, 'e621_database': {}, 'rules': []}

        self._rules = self._data.setdefault('rules', [])
        ids_backfilled = ensure_rule_ids(self._rules)
        children_normalized = any(
            _normalize_sync_children(master)
            for master in self._rules if master.get('sync_master'))
        enforce_tab_order(self._rules)
        self._flat  = build_flat_list(self._rules)
        self._recompute_base_dirs()
        self._recompute_sync_status()
        self._tree.set_category(self._active_tab)
        self._tree.refresh(self._flat)
        self._update_tab_buttons()
        self._edit_panel.clear()
        self._load_global_settings()
        self._set_dirty(ids_backfilled or children_normalized)
        self._status(
            f'Loaded {len(self._rules)} top-level rules'
            f'  ({len(self._flat)} total nodes)'
            + ('  — cleaned duplicate group rules (unsaved)' if children_normalized
               else ('  — assigned missing rule IDs (unsaved)' if ids_backfilled else '')))

    def _reload(self):
        from tkinter import messagebox
        if self._dirty:
            if not messagebox.askyesno('Unsaved Changes',
                                       'Discard changes and reload?'):
                return
        self._load()

    def _save(self):
        from tkinter import messagebox
        # Build a fully expanded (uncollapsed) flat list so that write_back
        # doesn't lose sub-rules belonging to collapsed parent nodes.
        full_flat = build_flat_list(self._rules)
        write_back(full_flat, self._rules)
        self._data['rules'] = self._rules
        self._save_global_settings()
        try:
            _save_yaml(self.config_path, self._data)
            self._set_dirty(False)
            self._status(f'Saved to {self.config_path.name}')
        except Exception as exc:
            messagebox.showerror('Save Error', f'Failed to save:\n{exc}')

    def _on_filter(self):
        self._tree.set_filter(self._filter_var.get())

    # ------------------------------------------------------------------
    # File counting
    # ------------------------------------------------------------------

    def _recompute_base_dirs(self) -> None:
        """Recompute pictures/videos/gifs base dirs (auto-constructed from config.yaml
        destination_directories when a rule has no hard-set target_directory)."""
        computed = get_global_base_dirs(self._data)
        self._base_dirs.clear()
        self._base_dirs.update(computed)
        self._tree.set_base_dirs(self._base_dirs)

    def _push_file_counts(self) -> None:
        """Re-draw the tree and edit panel using the current count cache."""
        self._tree.set_file_counts(self._file_counts)
        self._edit_panel.refresh_file_count_display()

    def _count_all_directories(self) -> None:
        """Scan every resolvable rule directory in a background thread."""
        if self._counting:
            return
        # Use a fully-expanded flat list (ignoring current collapse state), not
        # self._flat — a collapsed node (e.g. a Sync Master with many mirrored
        # sub-rules) would otherwise have its children silently skipped, since
        # resolve_node_directories() needs each descendant node to walk its
        # parent chain to resolve a directory.
        full_flat = build_flat_list(self._rules)
        node_dir_lists = []
        individual_dirs = set()
        for node in full_flat:
            dirs = resolve_node_directories(node, self._base_dirs)
            if dirs:
                node_dir_lists.append(dirs)
                individual_dirs.update(dirs)
        if not individual_dirs:
            self._status('No resolvable target directories to count')
            return

        self._counting = True
        self._count_btn.config(state='disabled', text='Counting…')
        self._status(f'Counting files in {len(individual_dirs)} folder(s)…')

        def worker():
            results = {}
            for d in individual_dirs:
                results[_normalize_dir_key(d)] = _count_files_in_dir(d)
            self.root.after(0, lambda: self._on_counts_done(results, node_dir_lists))

        threading.Thread(target=worker, daemon=True).start()

    def _on_counts_done(self, results: dict, node_dir_lists: list) -> None:
        self._file_counts.update(results)
        # Also compute combined-group totals for rules that resolve to multiple
        # directories (split-mode pictures/videos/gifs).
        for dirs in node_dir_lists:
            if len(dirs) <= 1:
                continue
            key = _count_key_for_dirs(dirs)
            total = 0
            any_found = False
            for d in dirs:
                c = self._file_counts.get(_normalize_dir_key(d))
                if c is not None:
                    total += c
                    any_found = True
            self._file_counts[key] = total if any_found else None
        self._counting = False
        self._count_btn.config(state='normal', text='Count Files')
        self._push_file_counts()
        counted = sum(1 for v in results.values() if v is not None)
        self._status(f'Counted {counted}/{len(results)} folder(s)')

    def _recount_directories(self, dirs: list) -> None:
        """Recount a node's directory/directories synchronously (Recount button)."""
        individual = {_normalize_dir_key(d): _count_files_in_dir(d) for d in dirs}
        self._file_counts.update(individual)
        key = _count_key_for_dirs(dirs)
        if key and len(dirs) > 1:
            total = sum(c for c in individual.values() if c is not None)
            any_found = any(c is not None for c in individual.values())
            self._file_counts[key] = total if any_found else None
        self._push_file_counts()
        total_count = self._file_counts.get(key) if key else None
        label = dirs[0] if len(dirs) == 1 else f'{len(dirs)} folder(s)'
        self._status(
            f'{_short_path(label, 50) if len(dirs) == 1 else label}: {total_count:,} file(s)'
            if total_count is not None else f'Could not count: {label}')

    # ------------------------------------------------------------------
    # Sync Master
    # ------------------------------------------------------------------

    def _resolve_rule_name(self, rule_id: str) -> Optional[str]:
        rule = find_rule_by_id(self._rules, rule_id)
        return _rule_display_name(rule) if rule else None

    def _sync_status_text(self, master: dict) -> str:
        plan = run_sync(master, self._rules, apply=False)
        parts = []
        if plan['added']:
            parts.append(f"{len(plan['added'])} new")
        if plan['updated']:
            parts.append(f"{len(plan['updated'])} changed")
        if plan['removed']:
            parts.append(f"{len(plan['removed'])} stale")
        if plan['reorder_needed']:
            parts.append('needs reorder')
        if plan['injected'] or plan['uninjected']:
            parts.append('exclude-tags out of date')
        if not parts:
            return '✓ In sync'
        return '⚠ Out of sync — ' + ', '.join(parts)

    def _manage_sync_exclusions(self, master: dict) -> None:
        """Edit the top-level rules excluded only from this sync master."""
        excluded = {str(rule_id) for rule_id in (master.get('sync_exclude_rule_ids') or [])}
        choices = [
            rule for rule in self._rules
            if rule is not master
            and not rule.get('sync_master')
            and rule.get('sort_group') not in ('special', 'pool')
        ]

        dialog = tk.Toplevel(self.root)
        dialog.title(f'Excluded Rules — {_rule_display_name(master)}')
        dialog.configure(bg=BG_PANEL)
        theme.dark_title_bar(dialog)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.minsize(420, 360)

        tk.Label(
            dialog,
            text='Select rules that this Sync Master should skip.\n'
                 'The selection affects this group only.',
            bg=BG_PANEL, fg=TEXT_COLOR, justify='left', anchor='w',
            font=(theme.UI_FONT, 10)).pack(fill='x', padx=12, pady=(12, 8))

        list_frame = tk.Frame(dialog, bg=BG_PANEL)
        list_frame.pack(fill='both', expand=True, padx=12)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side='right', fill='y')
        listbox = tk.Listbox(
            list_frame, selectmode='multiple', yscrollcommand=scrollbar.set,
            bg=BG_SURFACE, fg=TEXT_COLOR, selectbackground=ACCENT,
            selectforeground=ON_ACCENT, relief='flat', borderwidth=0,
            font=(theme.UI_FONT, 10), activestyle='none')
        listbox.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=listbox.yview)

        for index, rule in enumerate(choices):
            label = _rule_display_name(rule)
            if not rule.get('enabled', True):
                label += '  (disabled)'
            listbox.insert('end', label)
            if str(rule.get('rule_id')) in excluded:
                listbox.selection_set(index)

        buttons = tk.Frame(dialog, bg=BG_PANEL)
        buttons.pack(fill='x', padx=12, pady=12)

        def save_exclusions() -> None:
            selected = {choices[index].get('rule_id') for index in listbox.curselection()}
            master['sync_exclude_rule_ids'] = [
                rule_id for rule_id in (r.get('rule_id') for r in choices)
                if rule_id in selected
            ]
            dialog.destroy()
            self._mark_dirty()
            self._recompute_sync_status()
            self._edit_panel.load_node(self._edit_panel._current_node)

        tk.Button(
            buttons, text='Cancel', command=dialog.destroy,
            bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=10, pady=3,
            cursor='hand2', borderwidth=0).pack(side='right', padx=(6, 0))
        tk.Button(
            buttons, text='Save', command=save_exclusions,
            bg=ACCENT, fg=ON_ACCENT, relief='flat', padx=10, pady=3,
            cursor='hand2', borderwidth=0).pack(side='right')

        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        self.root.wait_window(dialog)

    def _jump_to_rule(self, rule_id: str) -> None:
        from tkinter import messagebox
        for idx, node in enumerate(self._flat):
            if node.rule.get('rule_id') == rule_id:
                self._tree.set_selected(idx)
                self._tree._scroll_to_selected()
                self._edit_panel.load_node(node)
                self._status(f'Jumped to: {node.display_name()}')
                return
        messagebox.showinfo('Not Found',
                            'Source rule not found (it may be collapsed or deleted).',
                            parent=self.root)

    def _confirm_and_run_sync(self, masters: list) -> None:
        from tkinter import messagebox
        plans = [(m, run_sync(m, self._rules, apply=False)) for m in masters]
        lines = []
        any_changes = False
        for m, plan in plans:
            changes = []
            if plan['added']:
                changes.append(f"+{len(plan['added'])} new mirror(s)")
            if plan['updated']:
                changes.append(f"~{len(plan['updated'])} updated")
            if plan['removed']:
                changes.append(f"-{len(plan['removed'])} removed")
            if plan['reorder_needed']:
                changes.append('reorder needed')
            if plan['injected']:
                changes.append(f"{len(plan['injected'])} exclude-tag injection(s)")
            if plan['uninjected']:
                changes.append(f"{len(plan['uninjected'])} exclude-tag cleanup(s)")
            if changes:
                any_changes = True
                lines.append(f'{_rule_display_name(m)}: ' + ', '.join(changes))
            else:
                lines.append(f'{_rule_display_name(m)}: already in sync')

        if not any_changes:
            messagebox.showinfo('Sync', 'Already up to date — no changes needed.', parent=self.root)
            return

        msg = 'The following changes will be applied:\n\n' + '\n'.join(lines)
        if not messagebox.askyesno('Confirm Sync', msg, parent=self.root):
            return

        for m, _ in plans:
            run_sync(m, self._rules, apply=True)

        self._mark_dirty()
        self._refresh_tree()
        self._status(f'Synced {len(masters)} master rule(s)')

    def _sync_single_master(self, node: RuleNode) -> None:
        if not node.rule.get('sync_master'):
            return
        self._confirm_and_run_sync([node.rule])

    def _cmd_sync_all_masters(self) -> None:
        from tkinter import messagebox
        masters = [r for r in self._rules if r.get('sync_master')]
        if not masters:
            messagebox.showinfo('No Sync Masters',
                                'No rules are flagged as Sync Master.\n\n'
                                'Enable "Sync Master" in the Options tab of a rule to use this feature.',
                                parent=self.root)
            return
        self._confirm_and_run_sync(masters)

    def _recompute_sync_status(self) -> None:
        out_of_sync = set()
        for r in self._rules:
            if r.get('sync_master') and is_master_out_of_sync(r, self._rules):
                out_of_sync.add(r.get('rule_id'))
        self._out_of_sync_ids = out_of_sync
        self._tree.set_out_of_sync_ids(out_of_sync)

    def _mark_dirty(self):
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._dirty_lbl.config(text='● Unsaved changes' if dirty else '')

    def _status(self, msg: str):
        self._status_var.set(msg)

    def _on_close(self):
        from tkinter import messagebox
        if self._dirty:
            ans = messagebox.askyesnocancel('Unsaved Changes',
                                            'Save before closing?')
            if ans is None:
                return
            if ans:
                self._save()
        self.root.destroy()


# ---------------------------------------------------------------------------
# Phase 3 — _DemoteDialog  (pick parent + sub-directory)
# ---------------------------------------------------------------------------

class _DemoteDialog(tk.Toplevel):
    """Minimal dialog: choose a parent rule and supply a sub-directory name."""

    def __init__(self, parent_win, node: RuleNode,
                 candidates: list[tuple[int, RuleNode]]):
        super().__init__(parent_win)
        self.title('Make Sub-Rule Of…')
        self.configure(bg=BG_COLOR)
        self.resizable(True, False)
        self.result = None  # (parent_flat_idx, sub_dir)

        self._candidates  = candidates
        self._demote_node = node

        self._build()
        self._populate()

        self.transient(parent_win)
        self.grab_set()
        self.geometry('520x380')
        self.update_idletasks()
        px = parent_win.winfo_rootx() + (parent_win.winfo_width()  - self.winfo_width())  // 2
        py = parent_win.winfo_rooty() + (parent_win.winfo_height() - self.winfo_height()) // 2
        self.geometry(f'+{px}+{py}')
        self.wait_window()

    def _build(self):
        self.columnconfigure(0, weight=1)

        tk.Label(self, text='Select parent rule:', bg=BG_COLOR, fg=ACCENT,
                 font=(theme.UI_FONT, 11, 'bold')).grid(
            row=0, column=0, sticky='w', padx=12, pady=(12, 4))

        lb_frame = tk.Frame(self, bg=BG_COLOR)
        lb_frame.grid(row=1, column=0, sticky='nsew', padx=12)
        lb_frame.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._lb = tk.Listbox(lb_frame, height=8, selectmode='single',
                              font=(theme.MONO_FONT, 10), bg=BG_SURFACE, fg=TEXT_COLOR,
                              selectbackground=ACCENT, selectforeground=ON_ACCENT,
                              relief='flat', highlightthickness=1,
                              highlightcolor=ACCENT, highlightbackground=BORDER_COLOR)
        sb = ttk.Scrollbar(lb_frame, orient='vertical', command=self._lb.yview,
                           style='Panel.Vertical.TScrollbar')
        self._lb.configure(yscrollcommand=sb.set)
        self._lb.grid(row=0, column=0, sticky='nsew')
        sb.grid(row=0, column=1, sticky='ns')
        self._lb.bind('<Double-1>', lambda e: self._ok())

        dir_row = tk.Frame(self, bg=BG_COLOR)
        dir_row.grid(row=2, column=0, sticky='ew', padx=12, pady=(8, 4))
        dir_row.columnconfigure(1, weight=1)
        tk.Label(dir_row, text='Sub-directory name:', bg=BG_COLOR, fg=ACCENT,
                 font=(theme.UI_FONT, 11, 'bold')).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self._sub_var = tk.StringVar()
        tk.Entry(dir_row, textvariable=self._sub_var,
                 bg=BG_SURFACE, fg=TEXT_COLOR, insertbackground=TEXT_COLOR,
                 relief='flat', font=(theme.UI_FONT, 11)).grid(row=0, column=1, sticky='ew')

        btn_row = tk.Frame(self, bg=BG_COLOR)
        btn_row.grid(row=3, column=0, sticky='e', padx=12, pady=(4, 12))
        tk.Button(btn_row, text='  OK  ', command=self._ok,
                  bg=ACCENT, fg=ON_ACCENT, relief='flat', padx=12, pady=3,
                  font=(theme.UI_FONT, 10, 'bold'), cursor='hand2',
                  activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).pack(side='left', padx=(0, 6))
        tk.Button(btn_row, text='Cancel', command=self.destroy,
                  bg=BG_SURFACE, fg=TEXT_COLOR, relief='flat', padx=12, pady=3,
                  font=(theme.UI_FONT, 10), cursor='hand2',
                  activebackground=ACCENT, activeforeground=ON_ACCENT,
                  borderwidth=0).pack(side='left')

    def _populate(self):
        for _, n in self._candidates:
            label = _truncate(_rule_tag_summary(n.rule), 50)
            dest  = n.rule.get('target_directory') or n.rule.get('folder_name') or ''
            if dest:
                label += f'  →  {_short_path(dest, 24)}'
            self._lb.insert(tk.END, label)
        # Pre-fill sub-dir from the demoted rule's target
        target = self._demote_node.rule.get('target_directory', '') or ''
        if target:
            self._sub_var.set(Path(target).name)
        elif self._demote_node.rule.get('folder_name'):
            self._sub_var.set(self._demote_node.rule['folder_name'])

    def _ok(self):
        from tkinter import messagebox
        sel = self._lb.curselection()
        if not sel:
            messagebox.showwarning('No Selection', 'Pick a parent rule.', parent=self)
            return
        sub = self._sub_var.get().strip()
        if not sub:
            messagebox.showwarning('Validation', 'Sub-directory name is required.', parent=self)
            return
        parent_flat_idx = self._candidates[sel[0]][0]
        self.result = (parent_flat_idx, sub)
        self.destroy()


# ---------------------------------------------------------------------------
# Smoke-test / entry point  (Phase 1 verification — kept for regression)
# ---------------------------------------------------------------------------

def _self_test(config_path: Path) -> None:
    """
    Quick data-layer smoke test — run automatically when the script is
    executed directly.  Prints diagnostics to stdout.
    """
    print(f'Loading: {config_path}')
    data  = _load_yaml(config_path)
    rules = data.get('rules') or []
    print(f'  {len(rules)} top-level rules')

    flat = build_flat_list(rules)
    print(f'  {len(flat)} total nodes (all levels)')

    for node in flat:
        indent = '  ' * node.depth
        badge  = _rule_type_label(node.rule)
        label  = node.display_name()
        kids   = f'  [{len(node.rule.get("sub_rules") or [])} sub-rules]' \
                 if node.has_children else ''
        print(f'  {indent}[{badge}] {label}{kids}')

    # Round-trip test: write_back then rebuild should yield same count
    import copy
    rules_backup = copy.deepcopy(rules)
    flat2 = build_flat_list(rules_backup)
    write_back(flat2, rules_backup)
    flat3 = build_flat_list(rules_backup)
    assert len(flat3) == len(flat), \
        f'Round-trip mismatch: {len(flat)} → {len(flat3)}'
    print('  Round-trip write_back OK')

    # Test insert_node_after
    if flat:
        test_rules = copy.deepcopy(rules)
        test_flat  = build_flat_list(test_rules)
        new_rule   = {
            'needed_tags': ['test_tag'],
            'can_have_tags': [],
            'exclude_tags': [],
            'tags': [],
            'target_directory': '/tmp/test',
            'folder_name': '',
            'enabled': True,
            'match_all': False,
            'exclude_favorite_artists': True,
            'only_fav_directory': False,
            'sub_rules': [],
        }
        before_count = len(test_flat)
        insert_node_after(test_flat, 0, new_rule, test_rules)
        assert len(test_flat) == before_count + 1, 'insert_node_after failed'
        print('  insert_node_after OK')

    # Test move_node (reorder)
    if len(flat) >= 2:
        test_rules = copy.deepcopy(rules)
        test_flat  = build_flat_list(test_rules)
        first_name = test_flat[0].display_name()
        move_node(test_flat, 0, 1, 'after', test_rules)
        assert test_flat[0].display_name() != first_name or len(test_flat) == 1, \
            'move_node reorder failed'
        print('  move_node (reorder) OK')

    print('\nPhase 1 data layer: ALL CHECKS PASSED')


def build(host, config_path: Optional[Path] = None) -> 'TagSortConfigApp':
    """Build the editor into ``host`` (a manager ToolHost or a themed Tk root)."""
    return TagSortConfigApp(host, Path(config_path or DEFAULT_CONFIG_PATH))


def launch(config_path: Optional[Path] = None) -> None:
    """Launch the Tag Sort Config UI v2."""
    root = tk.Tk()
    try:
        root.tk.call('tk', 'scaling', 1.0)
    except Exception:
        pass
    theme.apply(root)

    build(root, config_path)
    root.mainloop()


def parse_args(argv=None):  # fff: the original parsed args inside the __main__ block
    import argparse
    p = argparse.ArgumentParser(description='Tag Sort Config UI v2')
    p.add_argument('--config', default=str(DEFAULT_CONFIG_PATH),
                   help='Path to tag_sorting.yaml')
    p.add_argument('--test-only', action='store_true',
                   help='Run data-layer self-test without opening the window')
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    cfg = Path(args.config)
    if args.test_only:
        _self_test(cfg)
    else:
        launch(config_path=cfg)


if __name__ == '__main__':
    main()

