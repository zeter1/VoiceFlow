"""Compatibility facade for historical Windows helpers.

Internal implementation is split into startup-registry and text-insertion
adapters. Existing imports from voiceflow_app.windows remain supported.
"""

from __future__ import annotations

from .windows_insertion import (
    PasteTarget,
    get_paste_target,
    is_paste_target_active,
    restore_paste_target,
    send_ctrl_v_native,
)
from .windows_startup import (
    get_current_script_path,
    get_startup_command,
    is_windows_startup_enabled,
    set_windows_startup_enabled,
)
