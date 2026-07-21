"""
Home screen launcher — slot list rendered from config.home_slots.

Folders drop open inline: tapping a folder slot inserts its members directly
under that row (same typography, no title or close chrome); the centered list
grows around them and the widget block stays visible. Tapping the folder
again, launching a member, or a re-render collapses it.
Android apps launch via waydroid when available.
"""
from __future__ import annotations

import subprocess
from typing import Callable

import os as _os

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gtk

_HOME_CSS = b"""
.home-item-btn {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 18px 0;
    color: #f4f4f4;
}
.home-item-btn:hover, .home-item-btn:active {
    background: transparent;
    color: #9a9a9a;
}
.home-item-label {
    font-size: 27pt;
    font-weight: 300;
}
.home-folder-indicator {
    font-size: 14pt;
    font-weight: 300;
    color: #5a5a5a;
    margin-left: 6px;
}
"""


_WAYDROID_ENV = {
    **_os.environ,
    'WAYLAND_DISPLAY': 'wayland-0',
    'XDG_RUNTIME_DIR': f'/run/user/{_os.getuid()}',
}


def _waydroid_session_running() -> bool:
    try:
        out = subprocess.check_output(['waydroid', 'status'], text=True, timeout=2,
                                      env=_WAYDROID_ENV, stderr=subprocess.DEVNULL)
        return 'Session:\tRUNNING' in out
    except Exception:
        return False


def _launch_android(pkg: str) -> None:
    try:
        if not _waydroid_session_running():
            # Start the session in background; app will come up once it's ready
            subprocess.Popen(
                ['waydroid', 'session', 'start'],
                env=_WAYDROID_ENV, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Give the session a moment before launching the app
            from gi.repository import GLib
            GLib.timeout_add(5000, lambda p=pkg: _do_launch_android(p) or False)
            return
        _do_launch_android(pkg)
    except FileNotFoundError:
        pass


def _do_launch_android(pkg: str) -> None:
    try:
        subprocess.Popen(
            ['waydroid', 'app', 'launch', pkg],
            env=_WAYDROID_ENV, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _launch_cmd(cmd: list[str]) -> None:
    try:
        subprocess.Popen(cmd, close_fds=True, env=_WAYDROID_ENV)
    except FileNotFoundError:
        pass


class HomeLauncher(Gtk.Box):
    """Vertical list of home slots; folders drop open inline under their slot."""

    def __init__(self, open_dialer_fn: Callable[[], None] | None = None,
                 get_slots_fn: Callable[[], list[dict]] | None = None,
                 on_launch_slot: Callable[[dict], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_vexpand(True)
        self._open_dialer = open_dialer_fn
        self._get_slots = get_slots_fn or (lambda: [])
        self._on_launch_slot = on_launch_slot or (lambda slot: None)
        self._open_folder_index: int | None = None
        self._build()

    @property
    def folder_open(self) -> bool:
        return self._open_folder_index is not None

    def close_folder(self) -> bool:
        """Collapse the open folder drop-down. Returns True if one was open."""
        if self._open_folder_index is None:
            return False
        self._open_folder_index = None
        self._build()
        return True

    def refresh(self) -> None:
        self._open_folder_index = None
        self._build()

    def _build(self) -> None:
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt

        slots = self._get_slots()
        open_idx = self._open_folder_index
        if open_idx is not None and not (
            0 <= open_idx < len(slots) and slots[open_idx].get('type') == 'folder'
        ):
            self._open_folder_index = open_idx = None

        for idx, slot in enumerate(slots):
            slot_type = slot.get('type')
            if slot_type == 'app':
                self.append(self._make_row(slot.get('label', ''),
                                           lambda s=slot: self._on_launch_slot(s)))
            elif slot_type == 'folder':
                self.append(self._make_folder_row(slot, idx))
                if idx == open_idx:
                    self.append(self._make_member_dropdown(slot))

    def _make_member_dropdown(self, slot: dict) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for member in slot.get('folder', []):
            box.append(self._make_row(member.get('label', ''),
                                      lambda m=member: self._tap_member(m)))
        revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=120,
        )
        revealer.set_child(box)
        GLib.idle_add(self._reveal_once, revealer)
        return revealer

    @staticmethod
    def _reveal_once(revealer: Gtk.Revealer) -> bool:
        revealer.set_reveal_child(True)
        return GLib.SOURCE_REMOVE

    def _make_row(self, label: str, on_tap: Callable[[], None]) -> Gtk.Button:
        lbl = Gtk.Label(label=label)
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.add_css_class('home-item-label')

        btn = Gtk.Button()
        btn.add_css_class('home-item-btn')
        btn.set_hexpand(True)
        btn.set_child(lbl)
        btn.connect('clicked', lambda _b: on_tap())
        return btn

    def _make_folder_row(self, slot: dict, idx: int) -> Gtk.Button:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_halign(Gtk.Align.CENTER)

        lbl = Gtk.Label(label=slot.get('label', ''))
        lbl.add_css_class('home-item-label')
        ind = Gtk.Label(label='›')
        ind.add_css_class('home-folder-indicator')

        row.append(lbl)
        row.append(ind)

        btn = Gtk.Button()
        btn.add_css_class('home-item-btn')
        btn.set_hexpand(True)
        btn.set_child(row)
        btn.connect('clicked', lambda _b, i=idx, s=slot: self._toggle_folder(i, s))
        return btn

    def _toggle_folder(self, idx: int, slot: dict) -> None:
        if self._open_folder_index == idx:
            self._open_folder_index = None
        elif slot.get('folder'):
            self._open_folder_index = idx
        else:
            return
        self._build()

    def _tap_member(self, member: dict) -> None:
        self.close_folder()
        self._on_launch_slot({'type': 'app', **member})
