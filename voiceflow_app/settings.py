"""Persisted application settings and immutable processing snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Optional

from .config import (
    DEFAULT_HOTKEY,
    LEGACY_SETTINGS_PATH,
    LOCAL_WHISPER_MODEL,
    SETTINGS_DIR,
    SETTINGS_PATH,
)
from .diagnostics import log_exception, log_info, log_warning
from .hotkey_config import repair_unreliable_modifier_only_hotkey


@dataclass
class AppSettings:
    mode: str = "Чистый текст"
    language: str = "auto"
    privacy_mode: bool = True
    microphone_label: str = ""
    hotkey: str = DEFAULT_HOTKEY
    auto_paste_after_hotkey: bool = True
    # Realtime-only mode: this flag is kept for compatibility with older saved settings,
    # but final paste after stop is intentionally disabled.
    insert_edited_text: bool = True
    show_notifications: bool = True
    whisper_model: str = LOCAL_WHISPER_MODEL
    recognition_quality: str = "Максимальная точность"
    inference_device: str = "auto"
    compute_type: str = "auto"
    use_vad_filter: bool = True
    custom_terms: str = "ChatGPT, OpenAI, Telegram, WhatsApp, Gmail, Python, JavaScript, TypeScript, Make, n8n, Tilda, Reels, Instagram, PowerShell, Whisper"
    deep_grammar: bool = True
    realtime_streaming_mode: str = "Вставлять фрагментами"
    realtime_chunk_seconds: int = 2
    realtime_fast_quality: bool = True
    realtime_speed_profile: str = "Быстрее"
    launch_at_startup: bool = False
    window_geometry: str = "980x780"
    notification_x: Optional[int] = None
    notification_y: Optional[int] = None


@dataclass(frozen=True)
class RuntimeSettings:
    mode: str
    language: str
    whisper_model: str
    recognition_quality: str
    inference_device: str
    compute_type: str
    use_vad_filter: bool
    custom_terms: str
    deep_grammar: bool
    realtime_chunk_seconds: int
    realtime_fast_quality: bool
    realtime_speed_profile: str


class SettingsStore:
    """Persistent settings stored next to the program in voiceflow_settings/settings.json."""

    @staticmethod
    def _ensure_settings_dir() -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, object]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("settings file must contain a JSON object")
        return data

    @staticmethod
    def _settings_from_data(data: dict[str, object]) -> AppSettings:
        defaults = asdict(AppSettings())
        defaults.update({key: value for key, value in data.items() if key in defaults})
        repaired_hotkey, was_repaired = repair_unreliable_modifier_only_hotkey(str(defaults.get("hotkey", DEFAULT_HOTKEY)))
        if was_repaired:
            log_warning(
                "Saved modifier-only hotkey repaired to reliable default",
                saved_hotkey=defaults.get("hotkey"),
                repaired_hotkey=repaired_hotkey,
                reason="Modifier-only hotkeys like Ctrl+Win can stop firing repeatedly on Windows",
            )
        defaults["hotkey"] = repaired_hotkey
        return AppSettings(**defaults)

    @staticmethod
    def _backup_broken_settings(path: Path) -> Optional[Path]:
        try:
            if not path.exists():
                return None
            SettingsStore._ensure_settings_dir()
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = SETTINGS_DIR / f"{path.stem}.broken_{stamp}{path.suffix or '.json'}"
            shutil.copy2(path, backup_path)
            return backup_path
        except Exception as exc:
            log_exception("Could not backup broken settings file", exc, settings_path=path)
            return None

    @staticmethod
    def load() -> AppSettings:
        SettingsStore._ensure_settings_dir()

        if SETTINGS_PATH.exists():
            try:
                data = SettingsStore._read_json_file(SETTINGS_PATH)
                log_info("Settings loaded", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
                return SettingsStore._settings_from_data(data)
            except Exception as exc:
                backup_path = SettingsStore._backup_broken_settings(SETTINGS_PATH)
                log_exception(
                    "Could not load settings; using defaults",
                    exc,
                    settings_path=SETTINGS_PATH,
                    backup_path=backup_path,
                )
                return AppSettings()

        if LEGACY_SETTINGS_PATH.exists():
            try:
                data = SettingsStore._read_json_file(LEGACY_SETTINGS_PATH)
                settings = SettingsStore._settings_from_data(data)
                SettingsStore.save(settings)
                log_info(
                    "Legacy settings migrated to app settings folder",
                    legacy_settings_path=LEGACY_SETTINGS_PATH,
                    settings_path=SETTINGS_PATH,
                    settings_dir=SETTINGS_DIR,
                )
                return settings
            except Exception as exc:
                log_exception(
                    "Could not migrate legacy settings; using defaults",
                    exc,
                    legacy_settings_path=LEGACY_SETTINGS_PATH,
                    settings_path=SETTINGS_PATH,
                )
                return AppSettings()

        log_info("Settings file not found; using defaults", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
        return AppSettings()

    @staticmethod
    def save(settings: AppSettings) -> None:
        try:
            SettingsStore._ensure_settings_dir()
            data = asdict(settings)
            tmp_path = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, SETTINGS_PATH)
            log_info("Settings saved", settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
        except Exception as exc:
            log_exception("Could not save settings", exc, settings_path=SETTINGS_PATH, settings_dir=SETTINGS_DIR)
            print(f"Could not save settings: {exc}", file=sys.stderr)

