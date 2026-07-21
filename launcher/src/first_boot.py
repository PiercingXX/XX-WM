from __future__ import annotations

import gi
import subprocess
from collections.abc import Callable

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import THEME_PRESETS, ShellConfig

_WIZARD_CSS = b"""
.wizard-root {
    background: #000000;
    color: #f4f4f4;
    font-family: 'Space Mono', monospace;
}
.wizard-title {
    font-size: 28pt;
    font-weight: 300;
    color: #f4f4f4;
}
.wizard-subtitle {
    font-size: 13pt;
    color: #9a9a9a;
}
.wizard-label {
    font-size: 12pt;
    color: #f4f4f4;
}
.pin-dots {
    font-size: 22pt;
    letter-spacing: 0.4em;
    font-family: monospace;
    color: #f4f4f4;
    min-height: 48px;
}
.pin-key {
    font-size: 20pt;
    font-weight: 300;
    font-family: 'Space Mono', monospace;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: #111111;
    color: #f4f4f4;
    border: none;
    padding: 0;
}
.pin-key:hover { background: #1e1e1e; }
.pin-key.del {
    font-size: 16pt;
    background: transparent;
    color: #9a9a9a;
}
.pin-key.del:hover { background: #111111; }
.pin-sub {
    font-size: 8pt;
    color: #9a9a9a;
    margin-top: -2px;
}
.wizard-next {
    font-size: 13pt;
    font-family: 'Space Mono', monospace;
    min-height: 56px;
    border-radius: 16px;
    background: #f4f4f4;
    color: #000000;
    border: none;
}
.wizard-next:hover {
    background: #e0e0e0;
}
.wizard-skip {
    font-size: 11pt;
    color: #9a9a9a;
}
.theme-swatch {
    min-height: 80px;
    border-radius: 16px;
    border: 2px solid transparent;
}
"""

_TIMEZONES = [
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver',
    'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
    'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Kolkata', 'Australia/Sydney',
]


def keyboard_step_available() -> bool:
    """True when squeekboard + the Colemak layouts are installed (18.3)."""
    import shutil
    from pathlib import Path
    if not shutil.which('squeekboard'):
        return False
    return any(
        path.exists() for path in (
            Path.home() / '.local' / 'share' / 'squeekboard' / 'keyboards',
            Path('/usr/share/piercing-shell/squeekboard'),
        )
    )


class FirstBootWizard(Gtk.Window):
    """
    First-run setup (PIN, theme, timezone) followed by the usage walkthrough:
    an interactive gesture tour, shade / DnD & Focus / config-file pages, and
    a try-the-keyboard step when squeekboard is present. `tour_only=True`
    replays just the walkthrough (`piercing-shell --welcome`).
    """

    def __init__(self, on_complete: Callable[[], None],
                 tour_only: bool = False) -> None:
        super().__init__(title='PiercingOS Setup')
        self._tour_only = tour_only

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_complete = on_complete
        self._config = ShellConfig()
        self._pin_entered = ''
        self._pin_buf = ''
        self._confirm_buf = ''

        self._theme_provider = Gtk.CssProvider()

        provider = Gtk.CssProvider()
        provider.load_from_data(_WIZARD_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )

        self._stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT,
            transition_duration=220,
        )

        self._stack.add_named(self._build_welcome(), 'welcome')
        self._stack.add_named(self._build_pin_step(), 'pin')
        self._stack.add_named(self._build_pin_confirm_step(), 'pin_confirm')
        self._stack.add_named(self._build_theme_step(), 'theme')
        self._stack.add_named(self._build_timezone_step(), 'timezone')

        self._tour_pages = self._build_tour_pages()
        for name, widget in self._tour_pages:
            self._stack.add_named(widget, name)

        if tour_only:
            self._stack.set_visible_child_name(self._tour_pages[0][0])

        root = Gtk.Box()
        root.add_css_class('wizard-root')
        root.set_hexpand(True)
        root.set_vexpand(True)
        root.append(self._stack)
        self.set_child(root)

    @staticmethod
    def is_needed() -> bool:
        from pathlib import Path
        config_path = Path.home() / '.config' / 'piercing-shell' / 'config.json'
        return not config_path.exists()

    def _page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.set_margin_top(64)
        page.set_margin_start(32)
        page.set_margin_end(32)
        page.set_margin_bottom(48)
        return page

    def _build_welcome(self) -> Gtk.Widget:
        page = self._page()

        title = Gtk.Label(label='PiercingOS', xalign=0)
        title.add_css_class('wizard-title')

        sub = Gtk.Label(
            label='Let\'s set up your device.\nThis takes about a minute.',
            xalign=0,
            wrap=True,
        )
        sub.add_css_class('wizard-subtitle')

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        next_btn = Gtk.Button(label='Get started')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._stack.set_visible_child_name('pin'))

        page.append(title)
        page.append(sub)
        page.append(spacer)
        page.append(next_btn)
        return page

    def _build_pin_step(self) -> Gtk.Widget:
        page = self._page()
        page.set_margin_top(32)

        title = Gtk.Label(label='Set a PIN', xalign=0)
        title.add_css_class('wizard-title')

        sub = Gtk.Label(label='Up to 8 digits. Used to unlock your device.', xalign=0, wrap=True)
        sub.add_css_class('wizard-subtitle')

        self._pin_dots = Gtk.Label(label='')
        self._pin_dots.add_css_class('pin-dots')
        self._pin_dots.set_halign(Gtk.Align.CENTER)
        self._pin_dots.set_margin_top(8)

        self._pin_buf = ''
        keypad = self._build_pin_keypad(
            on_digit=self._pin_add,
            on_delete=self._pin_del,
        )

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', self._on_pin_next)

        skip_btn = Gtk.Button(label='Skip (no PIN)')
        skip_btn.add_css_class('flat')
        skip_btn.add_css_class('wizard-skip')
        skip_btn.connect('clicked', lambda _b: self._stack.set_visible_child_name('theme'))

        page.append(title)
        page.append(sub)
        page.append(self._pin_dots)
        page.append(keypad)
        page.append(next_btn)
        page.append(skip_btn)
        return page

    def _build_pin_confirm_step(self) -> Gtk.Widget:
        page = self._page()
        page.set_margin_top(32)

        title = Gtk.Label(label='Confirm PIN', xalign=0)
        title.add_css_class('wizard-title')

        sub = Gtk.Label(label='Enter the same PIN again.', xalign=0, wrap=True)
        sub.add_css_class('wizard-subtitle')

        self._confirm_dots = Gtk.Label(label='')
        self._confirm_dots.add_css_class('pin-dots')
        self._confirm_dots.set_halign(Gtk.Align.CENTER)
        self._confirm_dots.set_margin_top(8)

        self._confirm_buf = ''
        self._pin_error = Gtk.Label(label='')
        self._pin_error.add_css_class('wizard-subtitle')
        self._pin_error.set_halign(Gtk.Align.CENTER)

        keypad = self._build_pin_keypad(
            on_digit=self._confirm_add,
            on_delete=self._confirm_del,
        )

        confirm_btn = Gtk.Button(label='Set PIN')
        confirm_btn.add_css_class('wizard-next')
        confirm_btn.connect('clicked', self._on_pin_confirm)

        page.append(title)
        page.append(sub)
        page.append(self._confirm_dots)
        page.append(self._pin_error)
        page.append(keypad)
        page.append(confirm_btn)
        return page

    def _build_pin_keypad(self, on_digit: object, on_delete: object) -> Gtk.Grid:
        _KEYS = [
            ('1', ''),    ('2', 'ABC'), ('3', 'DEF'),
            ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
            ('7', 'PQRS'),('8', 'TUV'), ('9', 'WXYZ'),
            ('',  ''),    ('0', ''),    ('←', ''),
        ]
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        grid.set_halign(Gtk.Align.CENTER)
        for idx, (digit, sub) in enumerate(_KEYS):
            col, row = idx % 3, idx // 3
            if not digit:
                spacer = Gtk.Box()
                spacer.set_size_request(100, 80)
                grid.attach(spacer, col, row, 1, 1)
            elif digit == '←':
                btn = Gtk.Button(label='←')
                btn.add_css_class('pin-key')
                btn.add_css_class('del')
                btn.connect('clicked', lambda _b, fn=on_delete: fn())
                grid.attach(btn, col, row, 1, 1)
            else:
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                inner.set_halign(Gtk.Align.CENTER)
                inner.set_valign(Gtk.Align.CENTER)
                inner.append(Gtk.Label(label=digit))
                if sub:
                    s = Gtk.Label(label=sub)
                    s.add_css_class('pin-sub')
                    inner.append(s)
                btn = Gtk.Button()
                btn.add_css_class('pin-key')
                btn.set_child(inner)
                btn.connect('clicked', lambda _b, d=digit, fn=on_digit: fn(d))
                grid.attach(btn, col, row, 1, 1)
        return grid

    def _pin_add(self, digit: str) -> None:
        if len(self._pin_buf) < 8:
            self._pin_buf += digit
            self._pin_dots.set_text('●' * len(self._pin_buf))

    def _pin_del(self) -> None:
        if self._pin_buf:
            self._pin_buf = self._pin_buf[:-1]
            self._pin_dots.set_text('●' * len(self._pin_buf))

    def _confirm_add(self, digit: str) -> None:
        if len(self._confirm_buf) < 8:
            self._confirm_buf += digit
            self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _confirm_del(self) -> None:
        if self._confirm_buf:
            self._confirm_buf = self._confirm_buf[:-1]
            self._confirm_dots.set_text('●' * len(self._confirm_buf))

    def _build_theme_step(self) -> Gtk.Widget:
        page = self._page()

        title = Gtk.Label(label='Pick a theme', xalign=0)
        title.add_css_class('wizard-title')

        self._theme_swatch = Gtk.Box()
        self._theme_swatch.add_css_class('theme-swatch')
        self._theme_swatch.set_hexpand(True)

        self._theme_dropdown = Gtk.DropDown.new_from_strings(
            [preset.name for preset in THEME_PRESETS.values()]
        )
        self._theme_dropdown.set_selected(0)
        self._theme_dropdown.connect('notify::selected', self._on_theme_selected)
        self._on_theme_selected(self._theme_dropdown, None)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', self._on_theme_next)

        page.append(title)
        page.append(self._theme_swatch)
        page.append(self._theme_dropdown)
        page.append(spacer)
        page.append(next_btn)
        return page

    def _build_timezone_step(self) -> Gtk.Widget:
        page = self._page()

        title = Gtk.Label(label='Timezone', xalign=0)
        title.add_css_class('wizard-title')

        self._tz_dropdown = Gtk.DropDown.new_from_strings(_TIMEZONES)
        self._tz_dropdown.set_selected(0)

        spacer = Gtk.Box()
        spacer.set_vexpand(True)

        finish_btn = Gtk.Button(label='Done')
        finish_btn.add_css_class('wizard-next')
        finish_btn.connect('clicked', self._on_finish)

        page.append(title)
        page.append(self._tz_dropdown)
        page.append(spacer)
        page.append(finish_btn)
        return page

    # ------------------------------------------------------------------
    # Walkthrough (Workstream 18) — gesture tour + info pages
    # ------------------------------------------------------------------

    def _build_tour_pages(self) -> list[tuple[str, Gtk.Widget]]:
        pages: list[tuple[str, Gtk.Widget]] = [
            ('tour_swipe_up', self._gesture_page(
                'Swipe up', 'Opens the app drawer — every app, searchable.\n\nTry it now.',
                kind='swipe_up')),
            ('tour_swipe_down', self._gesture_page(
                'Swipe down', 'Opens notifications and quick settings.\n\nTry it now.',
                kind='swipe_down')),
            ('tour_swipe_side', self._gesture_page(
                'Swipe sideways', 'Switches between recent apps.\n\nTry it now.',
                kind='swipe_side')),
            ('tour_long_press', self._gesture_page(
                'Long-press home', 'Edits your home slots — reorder, rename, add apps and folders.\n\nHold anywhere now.',
                kind='long_press')),
            ('info_shade', self._info_page(
                'Shade & quick settings',
                'Swipe down anytime: WiFi, Bluetooth, torch and more, brightness '
                'and volume, your notifications, and an inline calendar behind '
                'the date.')),
            ('info_focus', self._info_page(
                'Do Not Disturb & Focus',
                'DnD silences everything except starred contacts and repeat '
                'callers.\n\nFocus pauses distracting apps and holds their '
                'notifications until you are done. Both live in quick settings.')),
        ]
        if keyboard_step_available():
            pages.append(('tour_keyboard', self._keyboard_page()))
        pages.append(('info_files', self._final_page()))
        return pages

    def _advance_tour(self) -> None:
        names = [name for name, _w in self._tour_pages]
        current = self._stack.get_visible_child_name()
        if current not in names:
            self._stack.set_visible_child_name(names[0])
            return
        idx = names.index(current)
        if idx + 1 < len(names):
            self._stack.set_visible_child_name(names[idx + 1])
        else:
            self._finish_tour()

    def _finish_tour(self) -> None:
        self._on_complete()
        GLib.idle_add(self.close)

    def _tour_scaffold(self, title_text: str, body_text: str) -> tuple[Gtk.Box, Gtk.Box]:
        page = self._page()
        title = Gtk.Label(label=title_text, xalign=0)
        title.add_css_class('wizard-title')
        body = Gtk.Label(label=body_text, xalign=0, wrap=True)
        body.add_css_class('wizard-subtitle')
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        page.append(title)
        page.append(body)
        page.append(spacer)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.append(footer)
        return page, footer

    def _skip_button(self) -> Gtk.Button:
        skip = Gtk.Button(label='Skip')
        skip.add_css_class('flat')
        skip.add_css_class('wizard-skip')
        skip.connect('clicked', lambda _b: self._advance_tour())
        return skip

    def _gesture_page(self, title_text: str, body_text: str, kind: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        footer.append(self._skip_button())

        if kind == 'long_press':
            gesture = Gtk.GestureLongPress.new()
            gesture.set_touch_only(False)
            gesture.connect('pressed', lambda _g, _x, _y: self._advance_tour())
            page.add_controller(gesture)
        else:
            swipe = Gtk.GestureSwipe.new()
            swipe.set_touch_only(False)

            def _on_swipe(_g: Gtk.GestureSwipe, vx: float, vy: float,
                          k: str = kind) -> None:
                if k == 'swipe_up' and vy < -300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_down' and vy > 300 and abs(vy) > abs(vx):
                    self._advance_tour()
                elif k == 'swipe_side' and abs(vx) > 300 and abs(vx) > abs(vy):
                    self._advance_tour()

            swipe.connect('swipe', _on_swipe)
            page.add_controller(swipe)
        return page

    def _info_page(self, title_text: str, body_text: str) -> Gtk.Widget:
        page, footer = self._tour_scaffold(title_text, body_text)
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        return page

    def _keyboard_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Try the keyboard',
            'PiercingOS ships a Colemak layout. Tap the field — the keyboard '
            'appears whenever you need to type.')
        entry = Gtk.Entry()
        entry.set_placeholder_text('Type something…')
        entry.set_margin_bottom(16)
        page.insert_child_after(entry, page.get_first_child().get_next_sibling())
        next_btn = Gtk.Button(label='Next')
        next_btn.add_css_class('wizard-next')
        next_btn.connect('clicked', lambda _b: self._advance_tour())
        footer.append(next_btn)
        return page

    def _final_page(self) -> Gtk.Widget:
        page, footer = self._tour_scaffold(
            'Everything is a text file',
            'Every shell preference lives in ~/.config/piercing-shell/ — edit '
            'it in a terminal and the shell reloads live. The full reference '
            'is docs/config.md.\n\nReplay this tour anytime with:\n'
            'piercing-shell --welcome')
        done_btn = Gtk.Button(label='Done')
        done_btn.add_css_class('wizard-next')
        done_btn.connect('clicked', lambda _b: self._finish_tour())
        footer.append(done_btn)
        return page

    def _on_theme_selected(self, dropdown: Gtk.DropDown, _param: object) -> None:
        theme_key = list(THEME_PRESETS)[dropdown.get_selected()]
        preset = THEME_PRESETS[theme_key]
        css = (
            f'.wizard-root {{ background: {preset.background}; color: {preset.foreground}; }}'
            f'.wizard-title {{ color: {preset.foreground}; }}'
            f'.wizard-subtitle {{ color: {preset.muted}; }}'
            f'.wizard-next {{ background: {preset.accent}; color: {preset.background}; }}'
            f'.pin-entry {{ background: {preset.surface}; color: {preset.foreground}; border-color: {preset.border}; }}'
            f'.theme-swatch {{ background: {preset.surface}; border-color: {preset.accent}; }}'
        ).encode()
        self._theme_provider.load_from_data(css)

    def _on_pin_next(self, _btn: Gtk.Widget) -> None:
        if not self._pin_buf:
            self._pin_dots.add_css_class('error')
            return
        self._pin_dots.remove_css_class('error')
        self._pin_entered = self._pin_buf
        self._confirm_buf = ''
        self._confirm_dots.set_text('')
        self._pin_error.set_text('')
        self._stack.set_visible_child_name('pin_confirm')

    def _on_pin_confirm(self, _btn: Gtk.Widget) -> None:
        if self._confirm_buf != self._pin_entered:
            self._pin_error.set_text('PINs do not match — try again')
            self._confirm_buf = ''
            self._confirm_dots.set_text('')
            return
        self._config.set_pin(self._pin_entered)
        self._stack.set_visible_child_name('theme')

    def _on_theme_next(self, _btn: Gtk.Button) -> None:
        theme_key = list(THEME_PRESETS)[self._theme_dropdown.get_selected()]
        self._config.set_theme(theme_key)
        self._stack.set_visible_child_name('timezone')

    def _on_finish(self, _btn: Gtk.Button) -> None:
        tz = _TIMEZONES[self._tz_dropdown.get_selected()]
        try:
            subprocess.run(['timedatectl', 'set-timezone', tz], check=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Colemak is the shipped keyboard layout; squeekboard follows
        # org.gnome.desktop.input-sources (silent no-op without gsettings)
        try:
            subprocess.run(
                ['gsettings', 'set', 'org.gnome.desktop.input-sources',
                 'sources', "[('xkb', 'us+colemak')]"],
                timeout=5, capture_output=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Setup is durable from here even if the walkthrough is skipped
        self._config.save()
        self._advance_tour()
