"""Windows autostart registry adapter."""

from __future__ import annotations

from pathlib import Path
import sys

from .config import IS_WINDOWS, STARTUP_REG_PATH, STARTUP_VALUE_NAME

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover
    winreg = None  # type: ignore


def _quote_cmd_part(value: str) -> str:
    return '"' + value.replace('"', '\"') + '"'


def get_current_script_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def get_startup_command() -> str:
    """Build a Windows startup command for the current script/exe."""
    if getattr(sys, "frozen", False):
        return f'{_quote_cmd_part(str(Path(sys.executable).resolve()))} --startup'

    script_path = get_current_script_path()
    python_exe = Path(sys.executable).resolve()
    # Prefer pythonw.exe to avoid a console window on Windows startup.
    if python_exe.name.lower() == "python.exe":
        pythonw = python_exe.with_name("pythonw.exe")
        if pythonw.exists():
            python_exe = pythonw
    return f'{_quote_cmd_part(str(python_exe))} {_quote_cmd_part(str(script_path))} --startup'


def is_windows_startup_enabled() -> bool:
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            value, _typ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
        current_script = str(get_current_script_path()).lower()
        return current_script in str(value).lower()
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_windows_startup_enabled(enabled: bool) -> None:
    if not IS_WINDOWS or winreg is None:
        raise RuntimeError("Автозапуск доступен только на Windows")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, get_startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass
