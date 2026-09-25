"""System tray integration."""

from __future__ import annotations

import queue
import threading
from typing import Optional

from ..config import APP_NAME
from ..dependencies import Image, ImageDraw, pystray
from ..diagnostics import log_category, log_exception, log_info, log_warning
from ..windows import get_paste_target


class TrayManager:
    """Optional Windows tray icon powered by pystray when available."""

    def __init__(self, app: "VoiceFlowOfflineApp"):
        self.app = app
        self.icon: object = None
        self.thread: Optional[threading.Thread] = None

    @property
    def available(self) -> bool:
        return pystray is not None and Image is not None and ImageDraw is not None

    def start(self) -> bool:
        if not self.available:
            log_warning(
                "Tray icon is not available; install optional dependencies",
                install="pip install pystray pillow",
            )
            return False
        if self.icon is not None:
            return True

        menu = pystray.Menu(  # type: ignore[union-attr]
            pystray.MenuItem("Открыть окно", self._open_window, default=True),  # type: ignore[union-attr]
            pystray.MenuItem("Скрыть окно", self._hide_window),  # type: ignore[union-attr]
            pystray.MenuItem("Начать / остановить запись", self._toggle_recording),  # type: ignore[union-attr]
            pystray.MenuItem("Выход", self._exit_app),  # type: ignore[union-attr]
        )
        self.icon = pystray.Icon(  # type: ignore[union-attr]
            "VoiceFlowOffline",
            self._create_icon_image(),
            APP_NAME,
            menu,
        )
        self.thread = threading.Thread(target=self.icon.run, name="voiceflow-tray", daemon=True)
        self.thread.start()
        log_info("Tray icon started")
        return True

    def stop(self) -> None:
        icon = self.icon
        self.icon = None
        if icon is not None:
            try:
                icon.stop()
                log_info("Tray icon stopped")
            except Exception as exc:
                log_exception("Could not stop tray icon", exc)

    def _create_icon_image(self) -> object:
        image = Image.new("RGBA", (64, 64), (14, 116, 144, 255))  # type: ignore[union-attr]
        draw = ImageDraw.Draw(image)  # type: ignore[union-attr]
        draw.ellipse((8, 8, 56, 56), fill=(8, 145, 178, 255))
        draw.rounded_rectangle((27, 14, 37, 39), radius=5, fill=(255, 255, 255, 255))
        draw.rectangle((30, 39, 34, 49), fill=(255, 255, 255, 255))
        draw.arc((18, 25, 46, 49), start=0, end=180, fill=(255, 255, 255, 255), width=4)
        draw.rectangle((22, 50, 42, 54), fill=(255, 255, 255, 255))
        return image

    def _open_window(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_open_window_clicked")
            self.app.worker_queue.put(("external_show_window", "tray_menu"))
        except Exception as exc:
            log_exception("Tray open-window action failed", exc)

    def _hide_window(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_hide_window_clicked")
            self.app.worker_queue.put(("external_hide_window", "tray_menu"))
        except Exception as exc:
            log_exception("Tray hide-window action failed", exc)

    def _toggle_recording(self, _icon: object = None, _item: object = None) -> None:
        try:
            target = get_paste_target()
            log_category(
                "hotkey_trace",
                "tray_toggle_clicked",
                target={"foreground_hwnd": target.foreground_hwnd, "focus_hwnd": target.focus_hwnd},
            )
            # Put the action into the Tk-polled worker queue instead of calling
            # root.after from the pystray thread. On some systems pystray callbacks
            # can arrive from a non-Tk thread and the menu click then looks ignored.
            self.app.worker_queue.put(("external_toggle_recording", ("tray_menu", target)))
        except Exception as exc:
            log_exception("Tray toggle action failed", exc)

    def _exit_app(self, _icon: object = None, _item: object = None) -> None:
        try:
            log_category("hotkey_trace", "tray_exit_clicked")
            self.app.worker_queue.put(("external_exit", "tray_menu"))
        except Exception as exc:
            log_exception("Tray exit action failed", exc)
