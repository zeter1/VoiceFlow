"""Lightweight process-level single-instance guard.

This module must stay free from logging, GUI and third-party imports so the
Windows mutex can be acquired before expensive runtime imports or log-folder
mutation.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
from typing import Optional

from .config import APP_DIR, IS_WINDOWS


_SINGLE_INSTANCE_MUTEX_HANDLE: Optional[int] = None
_SINGLE_INSTANCE_LAST_ERROR: Optional[str] = None
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\VoiceFlowOffline_" + hashlib.sha256(
    str(APP_DIR).lower().encode("utf-8", errors="ignore")
).hexdigest()[:16]


def single_instance_mutex_name() -> str:
    return _SINGLE_INSTANCE_MUTEX_NAME


def single_instance_last_error() -> Optional[str]:
    return _SINGLE_INSTANCE_LAST_ERROR


def acquire_single_instance_lock() -> bool:
    """Return False when another VoiceFlow from this folder is already running."""

    global _SINGLE_INSTANCE_MUTEX_HANDLE, _SINGLE_INSTANCE_LAST_ERROR
    _SINGLE_INSTANCE_LAST_ERROR = None
    if not IS_WINDOWS:
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            _SINGLE_INSTANCE_LAST_ERROR = "CreateMutexW returned a null handle"
            return True

        _SINGLE_INSTANCE_MUTEX_HANDLE = handle
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
            _SINGLE_INSTANCE_MUTEX_HANDLE = None
            return False
        return True
    except Exception as exc:
        _SINGLE_INSTANCE_LAST_ERROR = f"{type(exc).__name__}: {exc}"
        return True
