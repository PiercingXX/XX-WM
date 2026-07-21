from __future__ import annotations

import gi
from datetime import datetime
from pathlib import Path

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

from gi.repository import Adw, Gdk, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from app_index import AppEntry, AppIndex
from app_item_actions import AppItemActions
from config import FONT_FAMILIES, THEME_PRESETS, ShellConfig
from gesture_config import ACTION_LABELS, GESTURE_LABELS, GestureConfig, VALID_ACTIONS
from system_status import status_line

_PAGE_ORDER = ['home', 'apps', 'settings']

_UPDATE_NOTIF_ID = 999901

_WEB_SEARCH_URL = 'https://duckduckgo.com/?q='


class ShellWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title='PiercingOS')
        self.add_css_class('piercing-shell')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.BOTTOM)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self.config = ShellConfig()
        self.gesture_config = GestureConfig()
        from dnd import DndState
        self.dnd_state = DndState(self.config)
        self.app_index = AppIndex()
        self.app_index.refresh()

        try:
            from default_layout import apply_default_layout
            apply_default_layout(self.config, self.gesture_config)
        except Exception:
            pass  # first-boot seeding must never block the shell

        self._idle_timer_id: int | None = None
        self._pick_slot_mode = False
        self._drawer_open_folder: int | None = None
        self._drawer_folder_header: Gtk.ListBoxRow | None = None
        self._last_search_results: list[AppEntry] = []
        self._call_ui: object | None = None
        self._call_bar: object | None = None
        self._shade: object | None = None
        self._switcher: object | None = None
        self._dialer: object | None = None
        self._back_layer: object | None = None

        from modem_monitor import ModemMonitor
        self._modem_monitor = ModemMonitor(
            on_incoming=self._on_call_incoming,
            on_answered=self._on_call_answered,
            on_ended=self._on_call_ended,
        )

        self.base_provider = Gtk.CssProvider()
        self.theme_provider = Gtk.CssProvider()
        self._load_css()

        self.stack = Gtk.Stack(
            hexpand=True,
            vexpand=True,
            transition_duration=200,
            transition_type=Gtk.StackTransitionType.NONE,
        )
        # Leaving the drawer disarms slot-pick mode so a later tap launches
        self.stack.connect(
            'notify::visible-child-name',
            lambda stack, _p: setattr(self, '_pick_slot_mode', False)
            if stack.get_visible_child_name() != 'apps' else None,
        )

        self.item_actions = AppItemActions(
            self.config,
            on_changed=self._refresh_after_item_action,
            on_status=self._show_status,
        )

        self.apps_search = Gtk.SearchEntry(placeholder_text='Filter apps')
        self.apps_search.connect('search-changed', self._on_apps_search_changed)
        self.apps_search.connect('activate', self._on_apps_search_activate)

        self.theme_dropdown = Gtk.DropDown.new_from_strings([preset.name for preset in THEME_PRESETS.values()])
        self.theme_dropdown.connect('notify::selected', self._on_theme_changed)

        self.font_dropdown = Gtk.DropDown.new_from_strings(['Sans Light', 'Space Mono', 'JetBrains Mono', 'JetBrains Mono Nerd'])
        self.font_dropdown.connect('notify::selected', self._on_font_changed)

        self.dark_mode_switch = Gtk.Switch(active=self.config.prefer_dark)
        self.dark_mode_switch.connect('notify::active', self._on_dark_mode_toggled)

        _size_labels = ['XS (70%)', 'S (85%)', 'Normal', 'L (115%)', 'XL (130%)']
        _size_values = [0.7, 0.85, 1.0, 1.15, 1.3]
        self._size_values = _size_values
        self.size_dropdown = Gtk.DropDown.new_from_strings(_size_labels)
        cur_scale = self.config.text_size_scale
        closest = min(range(len(_size_values)), key=lambda i: abs(_size_values[i] - cur_scale))
        self.size_dropdown.set_selected(closest)
        self.size_dropdown.connect('notify::selected', self._on_size_changed)

        _align_labels = ['Left', 'Center', 'Right']
        _align_values = ['left', 'center', 'right']
        self._align_values = _align_values
        self.align_dropdown = Gtk.DropDown.new_from_strings(_align_labels)
        cur_align = self.config.home_alignment
        self.align_dropdown.set_selected(_align_values.index(cur_align) if cur_align in _align_values else 0)
        self.align_dropdown.connect('notify::selected', self._on_align_changed)

        _lock_labels = ['Never', '30 sec', '1 min', '2 min', '5 min', '10 min']
        _lock_seconds = [0, 30, 60, 120, 300, 600]
        self._lock_seconds = _lock_seconds
        self.auto_lock_dropdown = Gtk.DropDown.new_from_strings(_lock_labels)
        cur_timeout = self.config.auto_lock_timeout
        idx = _lock_seconds.index(cur_timeout) if cur_timeout in _lock_seconds else 3
        self.auto_lock_dropdown.set_selected(idx)
        self.auto_lock_dropdown.connect('notify::selected', self._on_auto_lock_changed)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class('dim-label')

        self.app_count_label = Gtk.Label(xalign=0)
        self.app_count_label.add_css_class('dim-label')

        self.clock_label = Gtk.Label(xalign=0)
        self.clock_label.add_css_class('display-clock')

        self.date_label = Gtk.Label(xalign=0)
        self.date_label.add_css_class('display-date')

        self.status_strip = Gtk.Label(xalign=0)
        self.status_strip.add_css_class('dim-label')

        self.apps_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.apps_list.add_css_class('text-list')

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(self._build_root())
        self.set_content(self.toast_overlay)

        # Idle monitoring — any motion or key event resets the auto-lock timer
        motion = Gtk.EventControllerMotion.new()
        motion.connect('motion', lambda *_: self._reset_idle_timer())
        self.add_controller(motion)
        key_ctrl = Gtk.EventControllerKey.new()
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_ctrl)

        self._sync_controls_from_config()
        self._apply_theme()
        self._refresh_clock()
        self._populate_apps()
        self._refresh_status()
        self._setup_idle_timer()
        GLib.timeout_add_seconds(1, self._tick_clock)
        GLib.timeout_add_seconds(60, self._tick_status)

        from update_checker import UpdateChecker
        self._update_checker = UpdateChecker(
            self.config, on_updates_available=self._on_updates_available,
        )

    def _load_css(self) -> None:
        style_path = Path(__file__).with_name('style.css')
        self.base_provider.load_from_path(str(style_path))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, self.base_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(display, self.theme_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def _build_root(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('shell-root')

        self.stack.add_titled(self._build_home_page(), 'home', 'Home')
        self.stack.add_titled(self._build_apps_page(), 'apps', 'Apps')
        self.stack.add_titled(self._build_settings_page(), 'settings', 'Settings')

        swipe = Gtk.GestureSwipe.new()
        swipe.set_touch_only(False)
        swipe.connect('swipe', self._on_stack_swipe)
        self.stack.add_controller(swipe)
        self._swipe_navigated = False

        root.append(self.stack)
        return root

    def _on_stack_swipe(self, _gesture: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        # Vertical swipes: configured swipe-down action, or switcher (up)
        if abs(vel_y) > abs(vel_x) * 1.5:
            self._swipe_navigated = True
            if vel_y > 300:
                self._dispatch_gesture_action(
                    self.gesture_config.get('swipe_down_top') or 'notification_shade')
            elif vel_y < -400:
                self._show_switcher()
            return
        if abs(vel_y) > abs(vel_x):
            return
        current = self.stack.get_visible_child_name()
        # Swipe right in the drawer collapses an open folder drop-down first
        if current == 'apps' and vel_x > 200 and self._drawer_open_folder is not None:
            self._swipe_navigated = True
            self._drawer_open_folder = None
            self._populate_apps(self.apps_search.get_text())
            return
        # Home swipes dispatch their configured apps (design.md "Gestures");
        # an unbound ('none') swipe falls through to page navigation so the
        # drawer stays reachable without lisgd.
        if current == 'home' and not self._home_launcher.edit_mode:
            action = self.gesture_config.get(
                'swipe_left_home' if vel_x < -200 else 'swipe_right_home')
            if abs(vel_x) > 200 and action != 'none':
                self._swipe_navigated = True
                self._dispatch_gesture_action(action)
                return
        idx = _PAGE_ORDER.index(current) if current in _PAGE_ORDER else 0
        if vel_x < -200 and idx < len(_PAGE_ORDER) - 1:
            self._swipe_navigated = True
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx + 1])
        elif vel_x > 200 and idx > 0:
            self._swipe_navigated = True
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name(_PAGE_ORDER[idx - 1])

    def _dispatch_gesture_action(self, action: str) -> None:
        """Run a gesture_config action, including launch:<app_id> bindings."""
        if action.startswith('launch:'):
            self._launch_app_id(action[len('launch:'):])
            return
        if action == 'notification_shade':
            self._show_shade()
        elif action == 'search':
            # Opens the drawer with keyboard focus in the search field
            self.stack.set_visible_child_name('apps')
            self.apps_search.grab_focus()
        elif action == 'home':
            self.stack.set_visible_child_name('home')
        elif action == 'app_switcher':
            self._show_switcher()
        elif action == 'lock_screen':
            self._show_lock_screen()
        elif action == 'settings':
            self.stack.set_visible_child_name('settings')
        elif action == 'dialer':
            self._open_dialer()
        elif action == 'back':
            self._handle_back()
        elif action == 'camera':
            camera = next(
                (e for e in self.app_index.entries if 'camera' in e.name.casefold()), None)
            if camera is not None:
                self._launch_entry(camera)

    def _launch_app_id(self, app_id: str) -> None:
        from gi.repository import Gio
        try:
            info = Gio.DesktopAppInfo.new(app_id)
        except Exception:
            info = None
        if info is None:
            # Uninstalled target — behave like 'none' per Workstream 8.1
            return
        try:
            info.launch([], None)
            self.config.record_launch(app_id)
        except GLib.Error as error:
            self._show_status(f'Failed to launch: {error.message}')

    def _on_stack_swipe_drag_end(
        self, _gesture: Gtk.GestureSwipe, offset_x: float, offset_y: float
    ) -> None:
        # Called before `swipe` fires — defer snap-back check one idle tick
        GLib.idle_add(self._maybe_snap_back, offset_x, offset_y)

    def _maybe_snap_back(self, offset_x: float, offset_y: float) -> bool:
        navigated = self._swipe_navigated
        self._swipe_navigated = False
        if navigated:
            return False
        # Horizontal drag that didn't become navigation → pulse opacity as feedback
        if abs(offset_x) > 18 and abs(offset_x) > abs(offset_y):
            self.stack.set_opacity(0.72)
            GLib.timeout_add(160, self._restore_stack_opacity)
        return False

    def _restore_stack_opacity(self) -> bool:
        self.stack.set_opacity(1.0)
        return False

    def _build_home_page(self) -> Gtk.Widget:
        from home_launcher import HomeLauncher, _HOME_CSS
        from gi.repository import Gdk

        css = Gtk.CssProvider()
        css.load_from_data(_HOME_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
        )

        # --- Top 1/3: clock / date / status — vertically centered ---
        self.clock_label.set_xalign(0.5)
        self.date_label.set_xalign(0.5)
        self.status_strip.set_xalign(0.5)

        # Double-tap clock → lock screen
        double_tap = Gtk.GestureClick.new()
        double_tap.connect('pressed', self._on_clock_tapped)
        self._clock_tap_count = 0
        self._clock_tap_timer: int | None = None
        self.clock_label.add_controller(double_tap)

        # Widget order per design.md: time, date, (weather — WS 5.4), battery
        clock_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_inner.set_halign(Gtk.Align.FILL)
        clock_inner.set_hexpand(True)
        clock_inner.append(self.clock_label)
        clock_inner.append(self.date_label)
        clock_inner.append(self.status_strip)

        # Equal spacers above and below clock_inner → vertically centered in top pane
        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        sp_top = Gtk.Box()
        sp_top.set_vexpand(True)
        sp_bot = Gtk.Box()
        sp_bot.set_vexpand(True)
        top_pane.append(sp_top)
        top_pane.append(clock_inner)
        top_pane.append(sp_bot)

        # --- Bottom 2/3: launcher — vertically centered ---
        def _get_home_slots() -> list[dict]:
            return self.config.home_slots

        def _launch_slot(slot: dict) -> None:
            self._launch_slot(slot)

        self._home_launcher = HomeLauncher(
            open_dialer_fn=self._open_dialer,
            get_slots_fn=_get_home_slots,
            on_launch_slot=_launch_slot,
            on_member_long_press=lambda w, s, m: self.item_actions.show_member_menu(w, s, m),
            on_slot_move=self._move_slot,
            on_slot_remove=self._remove_slot,
            on_slot_rename=self._rename_slot,
            on_edit_action=self._on_edit_action,
        )

        launcher_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        launcher_inner.set_valign(Gtk.Align.CENTER)
        launcher_inner.set_vexpand(True)
        launcher_inner.set_hexpand(True)
        launcher_inner.append(self._home_launcher)

        launcher_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        launcher_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        launcher_scroll.set_has_frame(False)
        launcher_scroll.add_css_class('home-scroll')
        launcher_scroll.set_child(launcher_inner)

        bot_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bot_pane.set_hexpand(True)
        bot_pane.set_vexpand(True)
        bot_pane.set_margin_bottom(24)
        bot_pane.append(launcher_scroll)

        # Long-press anywhere on home → slot edit mode (design.md "Home screen")
        edit_press = Gtk.GestureLongPress.new()
        edit_press.set_touch_only(False)
        edit_press.connect('pressed', self._on_home_long_press)
        bot_pane.add_controller(edit_press)

        # --- Paned: top=1/3, bottom=2/3, ratio maintained on resize ---
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        paned.set_wide_handle(False)
        paned.set_resize_start_child(False)
        paned.set_resize_end_child(True)
        paned.set_shrink_start_child(False)
        paned.set_shrink_end_child(False)
        paned.set_start_child(top_pane)
        paned.set_end_child(bot_pane)

        # Set divider at 1/3 once the widget is realized and allocated.
        # GTK4 removed size-allocate as a connectable signal; use realize + idle_add.
        # The widget block gets margin_top = h/6 so its center sits on the 1/4
        # line of the screen ((h/3 + h/6) / 2); the slot list stays centered in
        # the bottom pane, i.e. on the 2/3 line.
        def _set_ratio_once(w: Gtk.Paned) -> None:
            def _apply() -> bool:
                h = w.get_height()
                if h > 0:
                    w.set_position(h // 3)
                    clock_inner.set_margin_top(h // 6)
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE   # retry next idle
            GLib.idle_add(_apply)
        paned.connect('realize', _set_ratio_once)

        return paned

    def _open_dialer(self) -> None:
        from dialer import Dialer
        if self._dialer and self._dialer.get_visible():
            return
        d = Dialer(dnd_state=self.dnd_state)
        d.set_application(self.get_application())
        d.present()
        self._dialer = d

    def _launch_slot(self, slot: dict) -> None:
        """Launch an app or command from a home slot."""
        # Record usage
        app_id = slot.get('app_id')
        if app_id:
            self.config.record_launch(app_id)

        # Launch based on type
        slot_type = slot.get('type')
        if slot_type == 'app':
            android_pkg = slot.get('app_id')
            cmd = slot.get('cmd')
            label = slot.get('label', '')

            if label == 'Phone' and not android_pkg and not cmd:
                # Built-in dialer
                self._open_dialer()
            elif android_pkg:
                # Android app via waydroid
                from home_launcher import _launch_android
                _launch_android(android_pkg)
            elif cmd:
                # Command
                from home_launcher import _launch_cmd
                _launch_cmd(cmd)

    def _handle_back(self) -> None:
        """Called by BackGestureLayer on edge swipe from either side."""
        import subprocess
        import os
        # 1. Dismiss notification shade if open
        if self._shade and self._shade.get_visible():
            self._shade.hide_shade()
            return
        # 2. Dismiss app switcher if open
        if self._switcher and self._switcher.get_visible():
            self._switcher.hide_switcher()
            return
        # 3. Close dialer if open
        if self._dialer and self._dialer.get_visible():
            self._dialer.close()
            return
        # 4. Navigate back within the shell stack
        current = self.stack.get_visible_child_name()
        if current in ('apps', 'settings'):
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name('home')
            return
        # 5. Nothing shell-owned is open — send back to whatever is focused
        env = {**os.environ, 'WAYLAND_DISPLAY': 'wayland-0',
               'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}'}
        try:
            subprocess.Popen(['wtype', '-k', 'Escape'], env=env,
                             close_fds=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        # Always try Android KEYCODE_BACK — exits silently if Waydroid isn't running.
        # Deliberately no status check: check_output blocks the main thread for up to 1s.
        try:
            subprocess.Popen(
                ['waydroid', 'shell', 'input', 'keyevent', '4'],
                env=env, close_fds=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _on_clock_tapped(self, _gesture: Gtk.GestureClick, n_press: int, _x: float, _y: float) -> None:
        if n_press == 2:
            self._show_lock_screen()

    def _show_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            if not lock.get_visible():
                lock.prepare()
            lock.present()
            lock.set_visible(True)
            return
        from lock_screen import LockScreen
        self._lock_screen = LockScreen(
            on_unlock=self._dismiss_lock_screen,
            get_notifications=lambda: self._ensure_shade().notifications_snapshot(),
            dnd_active_fn=lambda: self.dnd_state.is_active(),
            on_open_shade=self._show_shade,
        )
        self._lock_screen.set_application(self.get_application())
        self._lock_screen.present()

    def _dismiss_lock_screen(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None:
            lock.set_visible(False)

    def try_fingerprint_unlock(self) -> None:
        lock = getattr(self, '_lock_screen', None)
        if lock is not None and lock.get_visible():
            lock.try_fingerprint_unlock()

    def _ensure_shade(self) -> object:
        if self._shade is None:
            from notification_shade import NotificationShade
            self._shade = NotificationShade(
                dnd_state=self.dnd_state,
                on_open_settings=lambda: (
                    self.stack.set_visible_child_name('settings'), self.present()),
            )
            self._shade.set_application(self.get_application())
        return self._shade

    def _show_shade(self) -> None:
        self._ensure_shade().show_shade()

    def _show_switcher(self) -> None:
        if self._switcher is None:
            from app_switcher import AppSwitcher
            self._switcher = AppSwitcher()
            self._switcher.set_application(self.get_application())
        self._switcher.show_switcher()

    def _build_apps_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_vexpand(True)
        outer.set_margin_bottom(24)
        outer.set_margin_start(24)
        outer.set_margin_end(0)

        self._sort_mode = 'az'

        title = Gtk.Label(label='All apps', xalign=0, hexpand=True)
        title.add_css_class('section-title')

        self._sort_btn = Gtk.Button(label='A-Z')
        self._sort_btn.add_css_class('flat')
        self._sort_btn.add_css_class('action-link')
        self._sort_btn.connect('clicked', self._toggle_sort_mode)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_row.set_margin_end(24)
        header_row.append(title)
        header_row.append(self._sort_btn)

        self.apps_search.set_margin_end(24)
        self.app_count_label.set_margin_end(24)

        self.apps_scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.apps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.apps_scroller.set_child(self.apps_list)

        # A-Z jump strip on the right edge
        self._alpha_letter_rows: dict[str, int] = {}
        alpha_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        alpha_box.set_valign(Gtk.Align.CENTER)
        alpha_box.set_margin_end(4)

        for ch in '#ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            btn = Gtk.Button(label=ch)
            btn.add_css_class('flat')
            btn.add_css_class('alpha-jump')
            btn.connect('clicked', self._on_alpha_jump, ch)
            alpha_box.append(btn)

        list_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        list_row.append(self.apps_scroller)
        list_row.append(alpha_box)

        # Search lives at the bottom where the thumb is; keyboard only on tap
        outer.append(header_row)
        outer.append(self.app_count_label)
        outer.append(list_row)
        outer.append(self.apps_search)

        # The drawer leaves the top ~15% of the screen free so it reads as a
        # sheet rather than a full-screen takeover.
        spacer = Gtk.Box()
        sheet = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sheet.set_vexpand(True)
        sheet.append(spacer)
        sheet.append(outer)

        def _set_sheet_ratio(w: Gtk.Box) -> None:
            def _apply() -> bool:
                h = w.get_height()
                if h > 0:
                    spacer.set_size_request(-1, int(h * 0.15))
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            GLib.idle_add(_apply)
        sheet.connect('realize', _set_sheet_ratio)
        return sheet

    def _toggle_sort_mode(self, _btn: Gtk.Button) -> None:
        self._sort_mode = 'install' if self._sort_mode == 'az' else 'az'
        self._sort_btn.set_label('Install date' if self._sort_mode == 'install' else 'A-Z')
        self._populate_apps(self.apps_search.get_text())

    def _home_slot_app_ids(self) -> set[str]:
        ids: set[str] = set()
        for slot in self.config.home_slots:
            if slot.get('app_id'):
                ids.add(str(slot['app_id']))
            for member in slot.get('folder') or []:
                if member.get('app_id'):
                    ids.add(str(member['app_id']))
        return ids

    @staticmethod
    def _install_time(entry: AppEntry) -> float:
        try:
            filename = entry.desktop_app.get_filename()
            return Path(filename).stat().st_mtime if filename else 0.0
        except OSError:
            return 0.0

    def _on_alpha_jump(self, _btn: Gtk.Button, letter: str) -> None:
        idx = self._alpha_letter_rows.get(letter)
        if idx is None and letter == '#':
            idx = 0
        if idx is None:
            return
        row = self.apps_list.get_row_at_index(idx)
        if row is None:
            return
        alloc = row.get_allocation()
        adj = self.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, alloc.y))

    def _build_settings_page(self) -> Gtk.Widget:
        from importlib.metadata import version as get_version
        try:
            shell_version = get_version('piercing-shell')
        except Exception:
            shell_version = '0.1.0'

        scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add_css_class('settings-page')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.set_margin_top(36)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        title = Gtk.Label(label='Shell settings', xalign=0)
        title.add_css_class('section-title')

        # System updates
        system_title = Gtk.Label(label='System', xalign=0)
        system_title.add_css_class('section-title')

        system_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        system_card.add_css_class('settings-card')

        update_button = Gtk.Button(label='Update system')
        update_button.add_css_class('flat')
        update_button.add_css_class('action-link')
        update_button.connect('clicked', self._on_update_clicked)

        check_updates_button = Gtk.Button(label='Check for updates now')
        check_updates_button.add_css_class('flat')
        check_updates_button.add_css_class('action-link')
        check_updates_button.connect('clicked', self._on_check_updates_clicked)

        system_card.append(update_button)
        system_card.append(check_updates_button)

        # Backup & restore
        backup_title = Gtk.Label(label='Backup & restore', xalign=0)
        backup_title.add_css_class('section-title')

        backup_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        backup_card.add_css_class('settings-card')

        export_btn = Gtk.Button(label='Export backup')
        export_btn.add_css_class('flat')
        export_btn.add_css_class('action-link')
        export_btn.connect('clicked', lambda _btn: self._on_export_backup())

        restore_btn = Gtk.Button(label='Restore from backup')
        restore_btn.add_css_class('flat')
        restore_btn.add_css_class('action-link')
        restore_btn.connect('clicked', lambda _btn: self._on_restore_backup())

        backup_card.append(export_btn)
        backup_card.append(restore_btn)

        # Mobile data / APN
        network_title = Gtk.Label(label='Mobile data', xalign=0)
        network_title.add_css_class('section-title')

        apn_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        apn_card.add_css_class('settings-card')

        self.apn_entry = Gtk.Entry()
        self.apn_entry.set_placeholder_text('APN (e.g. internet)')
        self.apn_entry.set_text(self.config.data.get('apn', ''))

        self.apn_user_entry = Gtk.Entry()
        self.apn_user_entry.set_placeholder_text('Username (leave blank if none)')
        self.apn_user_entry.set_text(self.config.data.get('apn_user', ''))

        self.apn_pass_entry = Gtk.Entry()
        self.apn_pass_entry.set_visibility(False)
        self.apn_pass_entry.set_placeholder_text('Password (leave blank if none)')
        self.apn_pass_entry.set_text(self.config.data.get('apn_pass', ''))

        apn_save_btn = Gtk.Button(label='Save APN')
        apn_save_btn.add_css_class('flat')
        apn_save_btn.add_css_class('action-link')
        apn_save_btn.connect('clicked', self._on_apn_save)

        apn_card.append(self._settings_row('APN', self.apn_entry))
        apn_card.append(self._settings_row('Username', self.apn_user_entry))
        apn_card.append(self._settings_row('Password', self.apn_pass_entry))
        apn_card.append(apn_save_btn)

        # About
        about_title = Gtk.Label(label='About', xalign=0)
        about_title.add_css_class('section-title')

        about_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        about_card.add_css_class('settings-card')

        version_label = Gtk.Label(label=f'Version: {shell_version}', xalign=0)
        version_label.add_css_class('dim-label')

        device_label = Gtk.Label(label=f'Device: {self._get_device_name()}', xalign=0)
        device_label.add_css_class('dim-label')

        about_card.append(version_label)
        about_card.append(device_label)

        box.append(system_title)
        box.append(system_card)
        box.append(backup_title)
        box.append(backup_card)
        box.append(network_title)
        box.append(apn_card)
        box.append(about_title)
        box.append(about_card)

        scroll.set_child(box)
        return scroll


    def _gesture_row(self, gesture_key: str, current_action: str) -> Gtk.Widget:
        label = Gtk.Label(label=GESTURE_LABELS.get(gesture_key, gesture_key), xalign=0)
        label.set_hexpand(True)
        label.add_css_class('dim-label')

        action_keys = sorted(VALID_ACTIONS)
        action_labels = [ACTION_LABELS.get(a, a) for a in action_keys]
        dropdown = Gtk.DropDown.new_from_strings(action_labels)
        if current_action in action_keys:
            dropdown.set_selected(action_keys.index(current_action))

        dropdown.connect(
            'notify::selected',
            lambda dd, _p, gk=gesture_key, keys=action_keys: self._on_gesture_changed(dd, gk, keys),
        )

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(label)
        row.append(dropdown)
        return row

    def _on_gesture_changed(self, dropdown: Gtk.DropDown, gesture_key: str, action_keys: list[str]) -> None:
        action = action_keys[dropdown.get_selected()]
        try:
            self.gesture_config.set(gesture_key, action)
        except ValueError:
            pass

    def _settings_row(self, title: str, control: Gtk.Widget) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=title, xalign=0)
        label.set_hexpand(True)
        row.append(label)
        row.append(control)
        return row

    def _sync_controls_from_config(self) -> None:
        theme_keys = list(THEME_PRESETS)
        font_keys = list(FONT_FAMILIES)
        self.theme_dropdown.set_selected(theme_keys.index(self.config.theme.key))
        self.font_dropdown.set_selected(font_keys.index(next(key for key, value in FONT_FAMILIES.items() if value == self.config.font_family)))
        self.dark_mode_switch.set_active(self.config.prefer_dark)

    def _apply_theme(self) -> None:
        theme = self.config.theme
        style_manager = Adw.StyleManager.get_default()
        if self.config.prefer_dark:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)

        scale = self.config.text_size_scale
        css = f"""
        .shell-root {{
            background: {theme.background};
            color: {theme.foreground};
            font-family: '{self.config.font_family}';
            font-size: {scale}rem;
        }}

        .shell-root entry,
        .shell-root text,
        .shell-root list,
        .shell-root row,
        .shell-root button,
        .shell-root box,
        .shell-root scrolledwindow,
        .shell-root label {{
            color: {theme.foreground};
        }}

        .shell-root entry,
        .shell-root list,
        .shell-root row,
        .shell-root scrolledwindow,
        .settings-card {{
            background: {theme.surface};
            border-color: {theme.border};
        }}

        .shell-root button:hover,
        .shell-root button:focus {{
            background: {theme.surface_alt};
        }}

        .dim-label,
        .eyebrow {{
            color: {theme.muted};
        }}

        .action-link {{
            color: {theme.accent};
        }}

        /* Long-press menus render outside .shell-root; theme them directly
           so every dialog follows the launcher font and colors. */
        popover.action-menu > contents {{
            background: {theme.surface};
            color: {theme.foreground};
            font-family: '{self.config.font_family}';
            font-size: {scale}rem;
            border: 1px solid {theme.border};
        }}

        popover.action-menu button,
        popover.action-menu label {{
            color: {theme.foreground};
        }}

        popover.action-menu button:hover,
        popover.action-menu button:focus {{
            background: {theme.surface_alt};
        }}

        popover.action-menu entry {{
            background: {theme.surface_alt};
            color: {theme.foreground};
        }}
        """
        self.theme_provider.load_from_data(css.encode('utf-8'))

    def _refresh_clock(self) -> None:
        now = datetime.now()
        self.clock_label.set_text(now.strftime('%H:%M'))
        self.date_label.set_text(now.strftime('%a, %b %d').replace(' 0', ' '))

    def _tick_clock(self) -> bool:
        self._refresh_clock()
        return True

    def _refresh_status(self) -> None:
        self.status_strip.set_text(status_line())

    def _tick_status(self) -> bool:
        self._refresh_status()
        return True

    def _refresh_index(self, _button: Gtk.Button | None = None) -> None:
        self.app_index.refresh()
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._show_status('Application index rebuilt.')

    def _populate_apps(self, query: str = '') -> None:
        results = self.app_index.search(query)
        labels = self.config.app_labels
        trimmed = query.strip().casefold()

        if trimmed:
            # Search surfaces everything: hidden apps, home-slot apps, folder
            # members — plus matches against renamed labels.
            matched = {e.app_id for e in results}
            renamed_hits = [
                e for e in self.app_index.entries
                if e.app_id not in matched and trimmed in labels.get(e.app_id, '').casefold()
            ]
            results = results + renamed_hits
        else:
            # The browse list hides hidden apps and everything already on home
            browse_hidden = set(self.config.hidden_apps) | self._home_slot_app_ids()
            results = [e for e in results if e.app_id not in browse_hidden]
            if getattr(self, '_sort_mode', 'az') == 'install':
                results = sorted(results, key=self._install_time, reverse=True)
            # Pinned apps surface first, in their pinned order
            pinned_order = {app_id: i for i, app_id in enumerate(self.config.pinned)}
            pinned = sorted((e for e in results if e.app_id in pinned_order),
                            key=lambda e: pinned_order[e.app_id])
            results = pinned + [e for e in results if e.app_id not in pinned_order]

        self._last_search_results = results if trimmed else []

        # Folder rows only exist in browse mode; drop a stale expansion
        folder_slots = [] if trimmed else [
            (idx, slot) for idx, slot in enumerate(self.config.home_slots)
            if slot.get('type') == 'folder'
        ]
        if self._drawer_open_folder is not None and self._drawer_open_folder not in {
            idx for idx, _slot in folder_slots
        }:
            self._drawer_open_folder = None

        expanded_members = 0
        if self._drawer_open_folder is not None:
            slot = self.config.home_slots[self._drawer_open_folder]
            expanded_members = len(slot.get('folder') or [])
        row_offset = len(folder_slots) + expanded_members

        # Build letter→first-row-index map for A-Z jump strip
        self._alpha_letter_rows = {}
        for idx, entry in enumerate(results):
            display = labels.get(entry.app_id, entry.name)
            first = display[0].upper() if display else '#'
            letter = first if first.isalpha() else '#'
            if letter not in self._alpha_letter_rows:
                self._alpha_letter_rows[letter] = idx + row_offset

        self._replace_rows(self.apps_list, results, 'No apps matched this search.', self._make_app_row)

        # Folder drop-downs sit above the app rows (design.md "App drawer")
        self._drawer_folder_header = None
        insert_at = 0
        for idx, slot in folder_slots:
            header = self._make_drawer_folder_row(slot, idx)
            if idx == self._drawer_open_folder:
                self._drawer_folder_header = header
            self.apps_list.insert(header, insert_at)
            insert_at += 1
            if idx == self._drawer_open_folder:
                for m_idx, member in enumerate(slot.get('folder') or []):
                    self.apps_list.insert(
                        self._make_drawer_member_row(idx, m_idx, member), insert_at)
                    insert_at += 1

        # Synthetic "Launcher Settings" entry always sits at the very end
        if not trimmed or trimmed in 'launcher settings':
            self.apps_list.append(self._make_settings_row())

        # Search results anchor at the bottom, above the search field, within
        # thumb reach; a set that overflows the view still reads from the top.
        self.apps_list.set_valign(Gtk.Align.END if trimmed else Gtk.Align.FILL)
        if trimmed:
            GLib.idle_add(self._scroll_apps_to_top)

        if trimmed:
            self.app_count_label.set_text(f'{len(results)} matches')
        else:
            self.app_count_label.set_text(f'{len(results)} apps indexed')

    def _scroll_apps_to_top(self) -> bool:
        self.apps_scroller.get_vadjustment().set_value(0.0)
        return GLib.SOURCE_REMOVE

    def _refresh_after_item_action(self) -> None:
        # Keep any open folder drop-down open, mirroring the Android launcher
        self._home_launcher.refresh(preserve_folder=True)
        self._populate_apps(self.apps_search.get_text())

    # -- home edit mode ----------------------------------------------------

    def _on_home_long_press(self, gesture: Gtk.GestureLongPress, _x: float, _y: float) -> None:
        if self._home_launcher.edit_mode:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._home_launcher.set_edit_mode(True)

    def _on_edit_action(self, action: str, widget: Gtk.Widget) -> None:
        if action == 'done':
            self._home_launcher.set_edit_mode(False)
        elif action == 'settings':
            self._home_launcher.set_edit_mode(False)
            self.stack.set_visible_child_name('settings')
        elif action == 'add_app':
            self._pick_slot_mode = True
            self.stack.set_visible_child_name('apps')
            self._show_status('Tap an app to add it to home.')
        elif action == 'new_folder':
            self.item_actions._show_entry_dialog(
                widget, 'New folder', '', 'Create', self._create_folder_slot)

    def _add_app_slot(self, entry: AppEntry) -> None:
        slots = self.config.home_slots
        if len(slots) >= 8:
            self._show_status('Home is full — remove a slot first.')
            return
        display = self.config.label_for(entry.app_id, entry.name)
        slots.append({'type': 'app', 'label': display, 'app_id': entry.app_id,
                      'cmd': None, 'folder': None})
        self.config.set_home_slots(slots)
        self.stack.set_visible_child_name('home')
        self._home_launcher.refresh()
        self._populate_apps(self.apps_search.get_text())
        self._show_status(f'{display} added to home.')

    def _create_folder_slot(self, name: str) -> None:
        slots = self.config.home_slots
        if len(slots) >= 8:
            self._show_status('Home is full — remove a slot first.')
            return
        slots.append({'type': 'folder', 'label': name, 'app_id': None, 'cmd': None, 'folder': []})
        self.config.set_home_slots(slots)
        self._home_launcher.refresh()
        self._home_launcher.set_edit_mode(True)

    def _move_slot(self, idx: int, delta: int) -> None:
        slots = self.config.home_slots
        new_idx = idx + delta
        if not (0 <= idx < len(slots) and 0 <= new_idx < len(slots)):
            return
        slots[idx], slots[new_idx] = slots[new_idx], slots[idx]
        self.config.set_home_slots(slots)
        self._home_launcher.refresh(preserve_folder=True)

    def _remove_slot(self, idx: int) -> None:
        slots = self.config.home_slots
        if not (0 <= idx < len(slots)):
            return
        removed = slots.pop(idx)
        self.config.set_home_slots(slots)
        self._home_launcher.refresh(preserve_folder=True)
        self._populate_apps(self.apps_search.get_text())
        self._show_status(f'{removed.get("label", "Slot")} removed from home.')

    def _rename_slot(self, widget: Gtk.Widget, idx: int) -> None:
        slots = self.config.home_slots
        if not (0 <= idx < len(slots)):
            return

        def _commit(text: str) -> None:
            slots[idx]['label'] = text
            app_id = slots[idx].get('app_id')
            if slots[idx].get('type') == 'app' and app_id:
                self.config.set_app_label(str(app_id), text)
            self.config.set_home_slots(slots)
            self._refresh_after_item_action()

        self.item_actions._show_entry_dialog(
            widget, 'Rename', slots[idx].get('label', ''), 'Rename', _commit)

    def _make_drawer_folder_row(self, slot: dict, slot_index: int) -> Gtk.ListBoxRow:
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label=slot.get('label', ''), xalign=0)
        title.add_css_class('app-name')
        indicator = Gtk.Label(label='⌄' if slot_index == self._drawer_open_folder else '›')
        indicator.add_css_class('dim-label')
        inner.append(title)
        inner.append(indicator)
        inner.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(inner)
        btn.connect('clicked', lambda _b, i=slot_index: self._toggle_drawer_folder(i))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _make_drawer_member_row(self, slot_index: int, member_index: int, member: dict) -> Gtk.ListBoxRow:
        title = Gtk.Label(label=member.get('label', ''), xalign=0)
        title.add_css_class('app-name')
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.add_css_class('folder-member')
        btn.set_child(title)

        def _launch(_b: Gtk.Button) -> None:
            self._drawer_open_folder = None
            self._populate_apps(self.apps_search.get_text())
            self._launch_slot({'type': 'app', **member})

        btn.connect('clicked', _launch)
        self._add_long_press(
            btn, lambda w: self.item_actions.show_member_menu(w, slot_index, member_index))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _toggle_drawer_folder(self, slot_index: int) -> None:
        opening = self._drawer_open_folder != slot_index
        self._drawer_open_folder = slot_index if opening else None
        self._populate_apps(self.apps_search.get_text())
        if opening:
            GLib.idle_add(self._center_drawer_folder)

    def _center_drawer_folder(self) -> bool:
        # Scroll so the folder row plus its drop-down sit vertically centered
        header = self._drawer_folder_header
        if header is None or self._drawer_open_folder is None:
            return GLib.SOURCE_REMOVE
        slot = self.config.home_slots[self._drawer_open_folder]
        row_h = header.get_height()
        if row_h <= 0:
            return GLib.SOURCE_CONTINUE
        block_h = row_h * (1 + len(slot.get('folder') or []))
        view_h = self.apps_scroller.get_height()
        alloc = header.get_allocation()
        block_h = min(block_h, view_h)
        target = alloc.y - max(0, (view_h - block_h) // 2)
        adj = self.apps_scroller.get_vadjustment()
        adj.set_value(max(0.0, float(target)))
        return GLib.SOURCE_REMOVE

    def _add_long_press(self, widget: Gtk.Widget, callback) -> None:
        def _on_long_press(gesture: Gtk.GestureLongPress, _x: float, _y: float) -> None:
            # Claim the sequence so releasing doesn't also fire the row's click
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            callback(widget)

        long_press = Gtk.GestureLongPress.new()
        long_press.set_touch_only(False)
        long_press.connect('pressed', _on_long_press)
        widget.add_controller(long_press)
        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect('pressed', lambda _g, _n, _x, _y, w=widget: callback(w))
        widget.add_controller(right_click)

    def _make_settings_row(self) -> Gtk.ListBoxRow:
        title = Gtk.Label(label='Launcher Settings', xalign=0)
        title.add_css_class('app-name')
        title.set_hexpand(True)

        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('app-entry')
        btn.set_child(title)
        btn.connect('clicked', lambda _b: self.stack.set_visible_child_name('settings'))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(btn)
        return row

    def _replace_rows(
        self,
        list_box: Gtk.ListBox,
        entries: list[AppEntry],
        empty_message: str,
        row_maker: object = None,
    ) -> None:
        child = list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            list_box.remove(child)
            child = next_child

        if not entries:
            label = Gtk.Label(label=empty_message, xalign=0)
            label.add_css_class('dim-label')
            row = Gtk.ListBoxRow(selectable=False, activatable=False)
            row.set_child(label)
            list_box.append(row)
            return

        maker = row_maker if callable(row_maker) else self._make_app_row
        for entry in entries:
            list_box.append(maker(entry))

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        self._reset_idle_timer()
        # Power button → force redraw to wake display if HWComposer idled
        if keyval in (Gdk.KEY_PowerOff, 0x1008ff2a, 0x1008ff18):
            self.queue_draw()
            return True
        return False

    def _setup_idle_timer(self) -> None:
        if self._idle_timer_id is not None:
            GLib.source_remove(self._idle_timer_id)
            self._idle_timer_id = None
        timeout = self.config.auto_lock_timeout
        if timeout > 0:
            self._idle_timer_id = GLib.timeout_add_seconds(timeout, self._on_idle_timeout)

    def _reset_idle_timer(self) -> None:
        self._setup_idle_timer()

    def _on_idle_timeout(self) -> bool:
        self._idle_timer_id = None
        self._show_lock_screen()
        return False

    def _on_auto_lock_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        seconds = self._lock_seconds[dropdown.get_selected()]
        self.config.set_auto_lock_timeout(seconds)
        self._setup_idle_timer()

    def _on_apn_save(self, _btn: Gtk.Button) -> None:
        apn = self.apn_entry.get_text().strip()
        user = self.apn_user_entry.get_text().strip()
        pwd = self.apn_pass_entry.get_text()
        self.config.data['apn'] = apn
        self.config.data['apn_user'] = user
        self.config.data['apn_pass'] = pwd
        self.config.save()
        self._apply_apn(apn, user, pwd)
        self._show_status('APN saved.')

    def _apply_apn(self, apn: str, user: str, pwd: str) -> None:
        if not apn:
            return
        try:
            from gi.repository import Gio
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            # Get first active GSM connection from NetworkManager and update APN
            _ = bus.call_sync(
                'org.freedesktop.NetworkManager',
                '/org/freedesktop/NetworkManager',
                'org.freedesktop.NetworkManager',
                'GetAllDevices',
                None, None, Gio.DBusCallFlags.NONE, 2000, None,
            )
            # NetworkManager APN update is complex; write to /etc/ModemManager-gsm.conf fallback
            import subprocess
            subprocess.Popen(
                ['nmcli', 'connection', 'modify', 'mobile',
                 'gsm.apn', apn, 'gsm.username', user, 'gsm.password', pwd],
                close_fds=True,
            )
        except Exception:
            pass

    def _on_call_incoming(self, caller: str, number: str) -> None:
        from call_ui import CallUI, CallBar
        import sound
        if self._call_ui is None:
            self._call_ui = CallUI(on_accept=sound.stop, on_decline=sound.stop)
            self._call_ui.set_application(self.get_application())
        if self._call_bar is None:
            self._call_bar = CallBar()
            self._call_bar.set_application(self.get_application())
        # DnD: only exceptions (starred, repeat caller) ring; everyone else
        # shows silently in the call UI. Exception check runs before this
        # call is recorded so the 15-minute repeat window looks at prior calls.
        rings = not self.dnd_state.is_active() or self.dnd_state.is_exception(number)
        self.dnd_state.note_call(number)
        if rings and self.config.sound_ringtone:
            sound.play('ringtone.mp3', loop=True)
        self._call_ui.show_incoming(caller or 'Incoming call', number)

    def _on_call_answered(self, caller: str, number: str) -> None:
        import sound
        sound.stop()
        if self._call_ui is None:
            return
        self._call_ui.show_active(caller or 'Active call', number)
        if self._call_bar is not None:
            self._call_bar.show_bar(caller or number)

    def _on_call_ended(self) -> None:
        import sound
        sound.stop()
        if self._call_ui is not None:
            self._call_ui.end_call()
        if self._call_bar is not None:
            self._call_bar.hide_bar()

    def _make_app_row(self, entry: AppEntry) -> Gtk.ListBoxRow:
        display_name = self.config.label_for(entry.app_id, entry.name)
        title = Gtk.Label(label=display_name, xalign=0)
        title.add_css_class('app-name')

        subtitle = Gtk.Label(label=entry.description or entry.app_id, xalign=0, wrap=True)
        subtitle.add_css_class('app-subtitle')
        subtitle.add_css_class('dim-label')

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.append(title)
        content.append(subtitle)
        content.set_hexpand(True)

        launch_btn = Gtk.Button()
        launch_btn.add_css_class('flat')
        launch_btn.add_css_class('app-entry')
        launch_btn.set_child(content)
        launch_btn.connect('clicked', lambda _b, e=entry: self._launch_entry(e))
        self._add_long_press(
            launch_btn, lambda w, e=entry: self.item_actions.show_app_menu(w, e))

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(launch_btn)
        return row

    def _launch_entry(self, entry: AppEntry) -> None:
        if self._pick_slot_mode:
            self._pick_slot_mode = False
            self._add_app_slot(entry)
            return
        ok, error = self.app_index.launch(entry)
        if ok:
            self.config.record_launch(entry.app_id)
            self._show_status(f'Launching {entry.name}...')
        else:
            self._show_status(f'Failed to launch {entry.name}: {error}')

    def _on_apps_search_changed(self, entry: Gtk.SearchEntry) -> None:
        text = entry.get_text()
        self._populate_apps(text)
        # Auto-launch the single result while typing, when enabled
        query = text.strip()
        if (self.config.search_auto_launch and query and not query.startswith('!')
                and len(self._last_search_results) == 1):
            entry.set_text('')
            self._launch_entry(self._last_search_results[0])

    def _on_apps_search_activate(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text().strip()
        if not query:
            return
        # `!query` → web search; a query with zero app matches falls back too
        if query.startswith('!'):
            self._open_web_search(query[1:].strip())
            entry.set_text('')
            return
        results = self._last_search_results
        if results:
            entry.set_text('')
            self._launch_entry(results[0])
        else:
            self._open_web_search(query)
            entry.set_text('')

    def _open_web_search(self, query: str) -> None:
        if not query:
            return
        from gi.repository import Gio
        from urllib.parse import quote_plus
        try:
            Gio.AppInfo.launch_default_for_uri(_WEB_SEARCH_URL + quote_plus(query), None)
            self._show_status(f'Searching the web for {query}...')
        except GLib.Error as error:
            self._show_status(f'No browser available: {error.message}')

    def _on_theme_changed(self, dropdown: Gtk.DropDown, _paramspec: object) -> None:
        theme_key = list(THEME_PRESETS)[dropdown.get_selected()]
        self.config.set_theme(theme_key)
        self._apply_theme()
        self._show_status(f'Theme set to {THEME_PRESETS[theme_key].name}.')

    def _on_font_changed(self, dropdown: Gtk.DropDown, _paramspec: object) -> None:
        font_key = list(FONT_FAMILIES)[dropdown.get_selected()]
        self.config.set_font(font_key)
        self._apply_theme()
        self._show_status('Font family updated.')

    def _on_dark_mode_toggled(self, switch: Gtk.Switch, _paramspec: object) -> None:
        self.config.set_prefer_dark(switch.get_active())
        self._apply_theme()
        self._show_status('Surface mode updated.')

    def _on_size_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        scale = self._size_values[dropdown.get_selected()]
        self.config.set_text_size_scale(scale)
        self._apply_theme()
        self._show_status('Text size updated.')

    def _on_align_changed(self, dropdown: Gtk.DropDown, _p: object) -> None:
        alignment = self._align_values[dropdown.get_selected()]
        self.config.set_home_alignment(alignment)
        self._home_launcher.refresh()
        self._show_status('Home alignment updated.')

    def _on_update_clicked(self, _btn: Gtk.Button | None = None) -> None:
        from update_checker import run_update_script
        ok, detail = run_update_script(self.config.update_script)
        if ok:
            self._show_status('Update running in terminal.')
        else:
            self._show_status(detail)

    def _on_check_updates_clicked(self, _btn: Gtk.Button) -> None:
        self._show_status('Checking for updates...')
        self._update_checker.check_now(self._on_manual_check_result)

    def _on_manual_check_result(self, count: int) -> None:
        if count > 0:
            self._on_updates_available(count)
        else:
            self._show_status('System is up to date.')

    def _on_updates_available(self, count: int) -> None:
        plural = 's' if count != 1 else ''
        self._ensure_shade().add_notification(
            _UPDATE_NOTIF_ID,
            'System',
            'Updates available',
            f'{count} package{plural} can be updated.',
            actions=[
                ('Update now', self._on_update_clicked),
                ('Snooze 1 week', self._snooze_updates),
            ],
        )
        self._show_status(f'{count} update{plural} available — see notifications.')

    def _snooze_updates(self) -> None:
        self._update_checker.snooze()
        self._show_status('Update checks snoozed for 1 week.')

    def _show_status(self, message: str) -> None:
        self.status_label.set_text(message)
        self.toast_overlay.add_toast(Adw.Toast.new(message))
    def _on_export_backup(self) -> None:
        from backup import export_backup
        import json
        from datetime import datetime
        from pathlib import Path

        backup = export_backup(self.config)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'piercing-wm-backup-{timestamp}.json'
        filepath = Path.home() / filename
        
        filepath.write_text(json.dumps(backup, indent=2), encoding='utf-8')
        self._show_status(f'Backup exported to {filepath}')
    
    def _on_restore_backup(self) -> None:
        from backup import validate_backup, restore_backup
        import json
        from gi.repository import Gtk as gtk
        
        # Create a file chooser dialog
        dialog = gtk.FileChooserDialog(
            title='Restore from backup',
            parent=self,
            action=gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            '_Cancel',
            gtk.ResponseType.CANCEL,
            '_Restore',
            gtk.ResponseType.OK,
        )
        
        filter_json = gtk.FileFilter()
        filter_json.set_name('JSON files')
        filter_json.add_pattern('*.json')
        dialog.add_filter(filter_json)
        
        response = dialog.run()
        
        if response == gtk.ResponseType.OK:
            filepath = dialog.get_filename()
            dialog.destroy()
            
            try:
                payload = json.loads(Path(filepath).read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as e:
                self._show_status(f'Failed to read backup: {e}')
                return
            
            is_valid, error = validate_backup(payload)
            if not is_valid:
                self._show_status(f'Invalid backup: {error}')
                return
            
            success = restore_backup(self.config, payload)
            if success:
                self._show_status('Backup restored successfully')
                # Reload config to apply changes
                self.config.load()
                self._sync_controls_from_config()
            else:
                self._show_status('Failed to restore backup')
        else:
            dialog.destroy()
    
    def _get_device_name(self) -> str:
        import platform
        try:
            # Try to read device model from sysfs (common on Linux phones)
            with open('/sys/devices/virtual/dmi/id/product_name', 'r') as f:
                return f.read().strip()
        except Exception:
            pass
        
        try:
            with open('/sys/devices/virtual/dmi/id/sys_vendor', 'r') as f:
                vendor = f.read().strip()
            with open('/sys/devices/virtual/dmi/id/product_version', 'r') as f:
                version = f.read().strip()
            return f'{vendor} {version}'
        except Exception:
            pass
        
        # Fallback to platform info
        return f'{platform.system()} {platform.release()}'

