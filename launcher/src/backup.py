"""
Backup and restore functionality for Piercing WM.
Exports and imports shell configuration as JSON.
"""
from __future__ import annotations


def export_backup(config) -> dict:
    """
    Export current configuration to a backup dictionary.
    
    Returns a dict with:
        version: 1
        home_slots: list of slot dicts
        app_labels: {app_id: label}
        hidden_apps: list of app_ids
        widgets: widget config
        theme: theme key or 'custom'
        custom_background: hex color if custom theme
        font: font family key or 'custom'
        text_size_scale: float
        home_alignment: str
        auto_lock_timeout: int
        gestures: gesture config
        search_auto_launch: bool
        hide_home_items_from_search: bool
        hide_folder_members_from_drawer: bool
    """
    backup = {
        'version': 1,
        'home_slots': config.home_slots,
        'app_labels': config.data.get('app_labels', {}),
        'hidden_apps': config.hidden_apps,
        'widgets': config.data.get('widgets', {}),
        'theme': config.data.get('theme', 'graphite'),
        'text_size_scale': config.text_size_scale,
        'home_alignment': config.home_alignment,
        'auto_lock_timeout': config.auto_lock_timeout,
        'search_auto_launch': config.data.get('search_auto_launch', False),
        'hide_home_items_from_search': config.data.get('hide_home_items_from_search', False),
        'hide_folder_members_from_drawer': config.data.get('hide_folder_members_from_drawer', False),
    }
    
    # Add custom background if applicable
    if config.data.get('theme') == 'custom' and config.custom_background:
        backup['custom_background'] = config.custom_background
    
    # Add custom font if applicable
    if config.data.get('font') == 'custom':
        backup['font'] = 'custom'
        backup['custom_font_family'] = config.data.get('custom_font_family', '')

    # Add gesture config
    try:
        from gesture_config import GestureConfig
        backup['gestures'] = dict(GestureConfig().all())
    except Exception:
        pass

    return backup


def validate_backup(payload: dict) -> tuple[bool, str | None]:
    """
    Validate a backup payload against the schema.
    
    Returns (is_valid, error_message).
    """
    if not isinstance(payload, dict):
        return False, "Payload must be a dictionary"
    
    if payload.get('version') != 1:
        return False, f"Unsupported version: {payload.get('version')}"
    
    # Validate home_slots
    home_slots = payload.get('home_slots', [])
    if not isinstance(home_slots, list):
        return False, "home_slots must be a list"
    if len(home_slots) > 8:
        return False, "home_slots must have at most 8 entries"
    for i, slot in enumerate(home_slots):
        if not isinstance(slot, dict):
            return False, f"home_slots[{i}] must be a dict"
        if slot.get('type') not in ('app', 'folder'):
            return False, f"home_slots[{i}].type must be 'app' or 'folder'"
    
    # Validate widgets
    widgets = payload.get('widgets', {})
    if not isinstance(widgets, dict):
        return False, "widgets must be a dict"
    for widget_name, widget_config in widgets.items():
        if not isinstance(widget_config, dict):
            return False, f"widgets['{widget_name}'] must be a dict"
    
    # Validate theme
    theme = payload.get('theme')
    if not isinstance(theme, str):
        return False, "theme must be a string"
    
    # Validate custom_background if present
    if payload.get('theme') == 'custom':
        custom_bg = payload.get('custom_background')
        if not custom_bg or not isinstance(custom_bg, str):
            return False, "custom_background must be a string when theme is 'custom'"
        import re
        if not re.match(r'^#[0-9a-fA-F]{6}$', custom_bg):
            return False, "custom_background must be a valid hex color (#RRGGBB)"
    
    # Validate font
    font = payload.get('font')
    if not isinstance(font, str):
        return False, "font must be a string"
    
    # Validate text_size_scale
    try:
        scale = float(payload.get('text_size_scale', 1.0))
        if not (0.5 <= scale <= 2.0):
            return False, "text_size_scale must be between 0.5 and 2.0"
    except (TypeError, ValueError):
        return False, "text_size_scale must be a number"
    
    # Validate auto_lock_timeout
    try:
        timeout = int(payload.get('auto_lock_timeout', 120))
        if timeout < 0:
            return False, "auto_lock_timeout must be non-negative"
    except (TypeError, ValueError):
        return False, "auto_lock_timeout must be an integer"

    # Validate gestures
    gestures = payload.get('gestures', {})
    if not isinstance(gestures, dict):
        return False, "gestures must be a dict"
    from gesture_config import _DEFAULTS, is_valid_action
    for key, action in gestures.items():
        if key not in _DEFAULTS:
            return False, f"unknown gesture: {key}"
        if not isinstance(action, str) or not is_valid_action(action):
            return False, f"unknown gesture action: {action}"

    return True, None


def restore_backup(config, payload: dict) -> bool:
    """
    Restore configuration from a backup payload.
    
    Only applies changes if validation passes. Returns True on success.
    """
    is_valid, error = validate_backup(payload)
    if not is_valid:
        return False
    
    # Apply validated data
    config.set_home_slots(payload.get('home_slots', []))
    
    if 'app_labels' in payload:
        config.data['app_labels'] = payload['app_labels']
    
    if 'hidden_apps' in payload:
        config.set_hidden_apps(payload['hidden_apps'])
    
    if 'widgets' in payload:
        config.data['widgets'] = payload['widgets']
    
    if 'theme' in payload:
        config.data['theme'] = payload['theme']
    
    if 'custom_background' in payload:
        config.data['custom_background'] = payload['custom_background']
    
    if 'font' in payload:
        config.data['font'] = payload['font']
    
    if 'custom_font_family' in payload:
        config.data['custom_font_family'] = payload['custom_font_family']
    
    if 'text_size_scale' in payload:
        config.set_text_size_scale(float(payload['text_size_scale']))
    
    if 'home_alignment' in payload:
        config.set_home_alignment(payload['home_alignment'])
    
    if 'auto_lock_timeout' in payload:
        config.set_auto_lock_timeout(int(payload['auto_lock_timeout']))
    
    if 'search_auto_launch' in payload:
        config.data['search_auto_launch'] = payload['search_auto_launch']
    
    if 'hide_home_items_from_search' in payload:
        config.data['hide_home_items_from_search'] = payload['hide_home_items_from_search']
    
    if 'hide_folder_members_from_drawer' in payload:
        config.data['hide_folder_members_from_drawer'] = payload['hide_folder_members_from_drawer']
    
    # Save changes
    config.save()
    
    # Apply gestures if present (already validated above)
    if 'gestures' in payload:
        try:
            from gesture_config import GestureConfig
            gc = GestureConfig()
            for key, action in payload['gestures'].items():
                gc.set(key, action)
        except Exception:
            pass

    return True
