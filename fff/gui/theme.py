"""Shared look for every FFF desktop window: one palette, one font set, one ttk style sheet.

Call ``apply(root)`` once per Tk root (the manager does it for embedded tools; each tool's
standalone ``launch``/``main`` does it for its own window). Widgets then pick up the theme
through ttk styles and the Tk option database; code that needs a colour or font directly
uses the tokens below (``PALETTE[...]``, ``font()``, ``mono()``) instead of literals.
"""
from __future__ import annotations

PALETTE = {
    "page": "#F1F6F6",          # window background
    "surface": "#FFFFFF",       # cards, lists, text fields
    "surface_alt": "#E8F0F1",   # headers, inactive tabs, toolbar strips
    "hover": "#E1EEEF",
    "selected": "#CFE8EA",
    "line": "#D4E0E2",
    "line_strong": "#B7C9CC",
    "ink": "#19343B",
    "muted": "#60777D",
    "faint": "#9AABAF",
    "accent": "#087F8C",
    "accent_hover": "#066A75",
    "accent_soft": "#DDF0F1",
    "sidebar": "#183B45",
    "sidebar_text": "#E5F0F0",
    "sidebar_hint": "#B9D0D2",
    "ok": "#18794E",
    "ok_soft": "#DFF3E8",
    "warning": "#A45B08",
    "warning_soft": "#FCF0DC",
    "error": "#B42318",
    "error_soft": "#FBE4E1",
    "info": "#1D4ED8",
    "info_soft": "#DCE7FD",
    "violet": "#6D28D9",
    "violet_soft": "#EBE4FC",
    "orange": "#C2410C",
    "orange_soft": "#FCE8DC",
    "media": "#22343A",         # backdrop behind images and videos
    "media_text": "#FFFFFF",
}

# Resolved by apply() to fonts that exist on this machine.
UI_FONT = "Segoe UI"
UI_FONT_BOLD = "Segoe UI Semibold"
MONO_FONT = "Consolas"


def font(size: int = 10, *styles: str) -> tuple:
    """A UI font tuple, e.g. ``font(11, "bold")``."""
    return (UI_FONT, size, *styles)


def mono(size: int = 9, *styles: str) -> tuple:
    return (MONO_FONT, size, *styles)


def _pick_fonts(root) -> None:
    global UI_FONT, UI_FONT_BOLD, MONO_FONT
    from tkinter import font as tkfont

    families = set(tkfont.families(root))
    default_family = tkfont.nametofont("TkDefaultFont", root).actual("family")
    UI_FONT = "Segoe UI" if "Segoe UI" in families else default_family
    UI_FONT_BOLD = "Segoe UI Semibold" if "Segoe UI Semibold" in families else UI_FONT
    MONO_FONT = next((f for f in ("Cascadia Mono", "Consolas", "Courier New") if f in families),
                     tkfont.nametofont("TkFixedFont", root).actual("family"))


def apply(root) -> None:
    """Theme ``root`` and everything created under it. Safe to call more than once."""
    import tkinter as tk
    from tkinter import font as tkfont, ttk

    _pick_fonts(root)
    p = PALETTE
    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10),
                       ("TkHeadingFont", 10), ("TkCaptionFont", 10), ("TkSmallCaptionFont", 9),
                       ("TkIconFont", 10), ("TkTooltipFont", 9)):
        try:
            tkfont.nametofont(name, root).configure(family=UI_FONT, size=size)
        except tk.TclError:
            pass
    try:
        tkfont.nametofont("TkFixedFont", root).configure(family=MONO_FONT, size=9)
    except tk.TclError:
        pass

    root.configure(background=p["page"])
    for pattern, value in (
            ("*Toplevel.background", p["page"]), ("*Frame.background", p["page"]),
            ("*Canvas.background", p["page"]), ("*Canvas.highlightThickness", 0),
            ("*Listbox.background", p["surface"]), ("*Listbox.foreground", p["ink"]),
            ("*Listbox.selectBackground", p["accent"]), ("*Listbox.selectForeground", "#FFFFFF"),
            ("*Listbox.relief", "flat"), ("*Listbox.borderWidth", 0),
            ("*Listbox.highlightThickness", 1), ("*Listbox.highlightBackground", p["line_strong"]),
            ("*Listbox.highlightColor", p["accent"]),
            ("*Text.background", p["surface"]), ("*Text.foreground", p["ink"]),
            ("*Text.insertBackground", p["ink"]), ("*Text.selectBackground", p["selected"]),
            ("*Text.relief", "flat"), ("*Text.borderWidth", 0), ("*Text.highlightThickness", 1),
            ("*Text.highlightBackground", p["line_strong"]), ("*Text.highlightColor", p["accent"]),
            ("*Entry.background", p["surface"]), ("*Entry.foreground", p["ink"]),
            ("*Entry.highlightThickness", 1), ("*Entry.highlightBackground", p["line_strong"]),
            ("*Entry.highlightColor", p["accent"]),
            ("*Menu.background", p["surface"]), ("*Menu.foreground", p["ink"]),
            ("*Menu.activeBackground", p["accent"]), ("*Menu.activeForeground", "#FFFFFF"),
            ("*TCombobox*Listbox.background", p["surface"]),
            ("*TCombobox*Listbox.selectBackground", p["accent"])):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    body, small, bold = font(10), font(9), (UI_FONT_BOLD, 10)
    style.configure(".", background=p["page"], foreground=p["ink"], font=body,
                    bordercolor=p["line"], lightcolor=p["page"], darkcolor=p["page"],
                    troughcolor=p["surface_alt"], focuscolor=p["accent"],
                    selectbackground=p["selected"], selectforeground=p["ink"],
                    fieldbackground=p["surface"], insertcolor=p["ink"])
    style.map(".", foreground=[("disabled", p["faint"])])

    # Frames and labels
    style.configure("TFrame", background=p["page"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("Card.TFrame", background=p["surface"], bordercolor=p["line"],
                    relief="solid", borderwidth=1)
    style.configure("Sidebar.TFrame", background=p["sidebar"])
    style.configure("TLabel", background=p["page"], foreground=p["ink"], font=body)
    style.configure("Muted.TLabel", foreground=p["muted"], font=small)
    style.configure("Title.TLabel", font=(UI_FONT_BOLD, 21), foreground=p["ink"])
    style.configure("Heading.TLabel", font=(UI_FONT_BOLD, 13), foreground=p["ink"])
    style.configure("Section.TLabel", font=(UI_FONT_BOLD, 11), foreground=p["accent"])
    style.configure("Ok.TLabel", foreground=p["ok"])
    style.configure("Warning.TLabel", foreground=p["warning"])
    style.configure("Error.TLabel", foreground=p["error"])
    style.configure("Link.TLabel", foreground=p["accent"], font=small)
    style.configure("CardTitle.TLabel", background=p["surface"], font=(UI_FONT_BOLD, 11))
    style.configure("CardBody.TLabel", background=p["surface"], foreground=p["muted"], font=small)
    style.configure("CardWarning.TLabel", background=p["surface"], foreground=p["warning"], font=small)
    style.configure("SidebarTitle.TLabel", background=p["sidebar"], foreground="#FFFFFF",
                    font=(UI_FONT_BOLD, 16))
    style.configure("SidebarHint.TLabel", background=p["sidebar"], foreground=p["sidebar_hint"],
                    font=font(8))
    style.configure("TLabelframe", background=p["page"], bordercolor=p["line"], relief="solid",
                    borderwidth=1)
    style.configure("TLabelframe.Label", background=p["page"], foreground=p["ink"], font=bold)
    style.configure("TSeparator", background=p["line"])
    style.configure("TPanedwindow", background=p["line"])
    style.configure("Sash", sashthickness=6, gripcount=0, background=p["line"])

    # Buttons
    style.configure("TButton", background=p["surface"], foreground=p["ink"], padding=(10, 5),
                    bordercolor=p["line_strong"], lightcolor=p["surface"], darkcolor=p["surface"],
                    font=body, focusthickness=0)
    style.map("TButton",
              background=[("disabled", p["surface_alt"]), ("pressed", p["selected"]),
                          ("active", p["hover"])],
              bordercolor=[("focus", p["accent"])])
    style.configure("Secondary.TButton", padding=(12, 7))
    style.configure("Tool.TButton", padding=(12, 6), font=(UI_FONT_BOLD, 9))
    for name, color, hover in (("Primary", p["accent"], p["accent_hover"]),
                               ("Danger", p["error"], "#912018")):
        style.configure(f"{name}.TButton", background=color, foreground="#FFFFFF",
                        bordercolor=color, lightcolor=color, darkcolor=color,
                        padding=(14, 7), font=bold)
        style.map(f"{name}.TButton",
                  background=[("disabled", p["line"]), ("pressed", hover), ("active", hover)],
                  foreground=[("disabled", p["muted"])],
                  bordercolor=[("active", hover)])
    for name, bg, fg, weight in (("Nav", p["sidebar"], p["sidebar_text"], UI_FONT),
                                 ("ActiveNav", p["accent"], "#FFFFFF", UI_FONT_BOLD)):
        style.configure(f"{name}.TButton", background=bg, foreground=fg, anchor="w",
                        padding=(10, 7), font=(weight, 10), borderwidth=0,
                        bordercolor=bg, lightcolor=bg, darkcolor=bg)
        style.map(f"{name}.TButton",
                  background=[("active", p["accent_hover"])], foreground=[("active", "#FFFFFF")],
                  bordercolor=[("active", p["accent_hover"])])

    # Inputs
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, fieldbackground=p["surface"], background=p["surface"],
                        bordercolor=p["line_strong"], lightcolor=p["surface"], darkcolor=p["surface"],
                        arrowcolor=p["muted"], padding=4)
        style.map(widget, bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
                  fieldbackground=[("readonly", p["surface"]), ("disabled", p["surface_alt"])])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=p["page"], foreground=p["ink"], font=body,
                        indicatorbackground=p["surface"], indicatorforeground=p["accent"],
                        upperbordercolor=p["line_strong"], lowerbordercolor=p["line_strong"])
        style.map(widget, background=[("active", p["page"])],
                  indicatorbackground=[("selected", p["accent"]), ("pressed", p["accent_soft"])],
                  indicatorforeground=[("selected", "#FFFFFF")])
    style.configure("TScale", background=p["accent"], troughcolor=p["surface_alt"],
                    bordercolor=p["line"])
    style.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["surface_alt"],
                    bordercolor=p["line"], lightcolor=p["accent"], darkcolor=p["accent"])

    # Lists, tabs, scrollbars
    style.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["ink"], rowheight=24, bordercolor=p["line"])
    style.map("Treeview", background=[("selected", p["selected"])],
              foreground=[("selected", p["ink"])])
    style.configure("Treeview.Heading", background=p["surface_alt"], foreground=p["ink"],
                    font=(UI_FONT_BOLD, 9), bordercolor=p["line"], relief="flat", padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", p["hover"])])
    style.configure("TNotebook", background=p["page"], borderwidth=0, bordercolor=p["line"],
                    tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=p["surface_alt"], foreground=p["muted"],
                    padding=(14, 6), font=(UI_FONT_BOLD, 9), bordercolor=p["line"],
                    lightcolor=p["surface_alt"])
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"]), ("active", p["hover"])],
              foreground=[("selected", p["accent"]), ("active", p["ink"])],
              lightcolor=[("selected", p["surface"])])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=p["line"], troughcolor=p["page"],
                        bordercolor=p["page"], lightcolor=p["line"], darkcolor=p["line"],
                        arrowcolor=p["muted"], gripcount=0, arrowsize=12)
        style.map(f"{orient}.TScrollbar",
                  background=[("pressed", p["accent"]), ("active", p["line_strong"])])
