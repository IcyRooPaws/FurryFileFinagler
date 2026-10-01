"""Run a desktop tool inside the manager window instead of its own ``tk.Tk()``.

The tools were written against a root window: they call ``title``/``geometry``/``protocol``,
bind shortcuts on the root and ``quit``/``destroy`` it when done. ``ToolHost`` is a frame
that accepts those calls, so the same tool class runs embedded or standalone:

* ``title`` reports to the manager (shown under the tool's heading); window sizing is ignored.
* ``protocol("WM_DELETE_WINDOW", fn)`` registers the tool's close handler (unsaved-changes
  prompts), which ``request_close`` runs when the manager closes the tool.
* Keyboard bindings go to the top-level window but only fire while this tool is on screen,
  so two open tools never react to each other's shortcuts.
* ``quit``/``destroy`` close only the tool.

The helpers below (``close``, ``on_destroy``, ``run_in_background``, ``message_panel``) work
on a ``ToolHost`` and on a plain ``tk.Tk`` alike, for tools that build themselves either way.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from fff.gui.theme import PALETTE

# Event types that stay on the frame itself; everything else (keys) is routed.
_LOCAL_EVENTS = {"Configure", "Destroy", "Map", "Unmap", "Enter", "Leave", "Motion", "Button",
                 "ButtonPress", "ButtonRelease", "MouseWheel", "FocusIn", "FocusOut", "Expose",
                 "Visibility"}


def _is_local(sequence: str) -> bool:
    if sequence.startswith("<<"):  # virtual events are generated on the widget itself
        return True
    parts = sequence.strip("<>").split("-")
    return any(part in _LOCAL_EVENTS or part.startswith(("B1", "B2", "B3")) for part in parts)


class _KeyRouter:
    """One binding per sequence on the top-level window, dispatched to visible tools."""

    def __init__(self, toplevel: tk.Misc):
        self.toplevel = toplevel
        self.handlers: dict[str, list[tuple[ToolHost, Callable]]] = {}

    @classmethod
    def of(cls, widget: tk.Misc) -> "_KeyRouter":
        toplevel = widget.winfo_toplevel()
        router = getattr(toplevel, "_fff_key_router", None)
        if router is None:
            router = toplevel._fff_key_router = cls(toplevel)
        return router

    def add(self, host: "ToolHost", sequence: str, func: Callable, add) -> None:
        if sequence not in self.handlers:
            self.handlers[sequence] = []
            self.toplevel.bind(sequence, lambda event, s=sequence: self.dispatch(s, event), add="+")
        entries = self.handlers[sequence]
        if not add:
            entries[:] = [entry for entry in entries if entry[0] is not host]
        entries.append((host, func))

    def dispatch(self, sequence: str, event):
        result = None
        for host, func in list(self.handlers.get(sequence, ())):
            if host.winfo_exists() and host.winfo_viewable() and func(event) == "break":
                result = "break"
        return result

    def forget(self, host: "ToolHost") -> None:
        for entries in self.handlers.values():
            entries[:] = [entry for entry in entries if entry[0] is not host]


class ToolHost(tk.Frame):
    """A frame that stands in for a tool's root window (see the module docstring)."""

    def __init__(self, master, *, on_title: Callable[[str], None] | None = None):
        super().__init__(master, background=PALETTE["page"])
        self._title = ""
        self._on_title = on_title
        self._close_handler: Callable | None = None
        super().bind("<Destroy>", self._destroyed, add="+")

    # --- window-manager calls the tools make on their root ----------------------------------

    def title(self, text: str | None = None):
        if text is None:
            return self._title
        self._title = str(text)
        if self._on_title is not None:
            self._on_title(self._title)
        return ""

    wm_title = title

    def geometry(self, *_args):
        return ""

    def minsize(self, *_args):
        return None

    maxsize = resizable = attributes = iconbitmap = iconphoto = minsize

    def protocol(self, name=None, func=None):
        if name == "WM_DELETE_WINDOW" and func is not None:
            self._close_handler = func
        return ""

    wm_protocol = protocol

    def quit(self):
        self.destroy()

    def bind(self, sequence=None, func=None, add=None):
        if sequence and func is not None and not _is_local(sequence):
            _KeyRouter.of(self).add(self, sequence, func, add)
            return None
        return super().bind(sequence, func, add)

    # --- used by the manager -----------------------------------------------------------------

    def request_close(self) -> bool:
        """Close like the window's X button would. False if the tool kept itself open."""
        if self._close_handler is not None:
            self._close_handler()
        else:
            self.destroy()
        return not self.winfo_exists()

    def _destroyed(self, event) -> None:
        if event.widget is self:
            _KeyRouter.of(self).forget(self)


# --- helpers for tools that build into either a ToolHost or a tk.Tk ------------------------

def close(widget: tk.Misc) -> None:
    """Close the tool: the embedded page, or the standalone window."""
    if isinstance(widget, ToolHost):
        widget.request_close()
    else:
        widget.destroy()


def on_destroy(widget: tk.Misc, func: Callable[[], None]) -> None:
    """Run ``func`` once when ``widget`` (a host or root) is destroyed."""
    def handler(event):
        if event.widget is widget:
            func()
    tk.Misc.bind(widget, "<Destroy>", handler, add="+")


def run_in_background(widget: tk.Misc, work: Callable[[], object], on_done: Callable[[object], None],
                      on_error: Callable[[BaseException], None]) -> None:
    """Run ``work()`` on a thread; call ``on_done(result)``/``on_error(exc)`` back on the Tk thread."""
    results: queue.Queue = queue.Queue()

    def worker():
        try:
            results.put((True, work()))
        except BaseException as exc:  # reported in the UI, never swallowed
            results.put((False, exc))

    def poll():
        if not widget.winfo_exists():
            return
        try:
            ok, value = results.get_nowait()
        except queue.Empty:
            widget.after(100, poll)
            return
        (on_done if ok else on_error)(value)

    threading.Thread(target=worker, name="fff-tool-loader", daemon=True).start()
    widget.after(100, poll)


def message_panel(parent: tk.Misc, heading: str, message: str, *, busy: bool = False,
                  tone: str = "", actions: tuple[tuple[str, Callable[[], None]], ...] = ()) -> ttk.Frame:
    """A centred card for loading/empty/error states. Placed, so it mixes with grid or pack."""
    card = ttk.Frame(parent, style="Card.TFrame", padding=(28, 22))
    card.place(relx=0.5, rely=0.4, anchor="center")
    ttk.Label(card, text=heading, style="CardTitle.TLabel").pack(anchor="w")
    body_style = "CardWarning.TLabel" if tone == "warning" else "CardBody.TLabel"
    ttk.Label(card, text=message, style=body_style, wraplength=460, justify="left").pack(
        anchor="w", pady=(6, 0))
    if busy:
        bar = ttk.Progressbar(card, mode="indeterminate", length=320)
        bar.pack(anchor="w", fill="x", pady=(14, 0))
        bar.start(12)
    if actions:
        row = ttk.Frame(card, style="Surface.TFrame")
        row.pack(anchor="e", pady=(16, 0))
        for index, (label, command) in enumerate(actions):
            ttk.Button(row, text=label, command=command,
                       style="Primary.TButton" if index == 0 else "TButton").pack(side="left", padx=(8, 0))
    return card
