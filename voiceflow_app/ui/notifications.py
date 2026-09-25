"""Non-blocking notification UI manager."""

from __future__ import annotations

import ctypes
import tkinter as tk
from typing import Callable, Optional

from ..config import IS_WINDOWS
from ..diagnostics import log_category, log_exception, log_info


class NotificationManager:
    """Small no-activate toast notification for recording/transcription states."""

    COLORS = {
        "idle": ("#111827", "#F9FAFB"),
        "recording": ("#B91C1C", "#FFFFFF"),
        "stopped": ("#374151", "#FFFFFF"),
        "processing": ("#1D4ED8", "#FFFFFF"),
        "success": ("#047857", "#FFFFFF"),
        "warning": ("#92400E", "#FFFFFF"),
        "error": ("#991B1B", "#FFFFFF"),
    }

    def __init__(self, root: tk.Tk):
        self.root = root
        self.window: Optional[tk.Toplevel] = None
        self.label: Optional[tk.Label] = None
        self.after_id: Optional[str] = None
        self._last_kind = "idle"
        self.manual_position: Optional[tuple[int, int]] = None
        self.on_position_changed: Optional[Callable[[Optional[tuple[int, int]]], None]] = None
        self._drag_pointer_start: Optional[tuple[int, int]] = None
        self._drag_window_start: Optional[tuple[int, int]] = None
        self._dragging = False

    def show(
        self,
        message: str,
        kind: str = "idle",
        duration_ms: Optional[int] = 2200,
        force_recreate: bool = False,
    ) -> None:
        self._last_kind = kind
        bg, fg = self.COLORS.get(kind, self.COLORS["idle"])

        if force_recreate and self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.label = None

        if self.window is None or not self.window.winfo_exists():
            self.window = tk.Toplevel(self.root)
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            try:
                self.window.attributes("-alpha", 0.96)
            except Exception:
                pass

            frame = tk.Frame(self.window, bg=bg, padx=18, pady=12)
            frame.pack(fill=tk.BOTH, expand=True)
            self.label = tk.Label(
                frame,
                text=message,
                bg=bg,
                fg=fg,
                font=("Segoe UI", 11, "bold"),
                justify="left",
                anchor="w",
            )
            self.label.pack(fill=tk.BOTH, expand=True)
            self._bind_drag_handlers(self.window)
            self._bind_drag_handlers(frame)
            self._bind_drag_handlers(self.label)
            if not self._dragging:
                self._position_window()
            self._make_no_activate_on_windows()
            self._show_no_activate()
        else:
            assert self.label is not None
            parent = self.label.master
            parent.configure(bg=bg)
            self.label.configure(text=message, bg=bg, fg=fg)
            # During a drag the timer updates this notification every second.
            # Do not reposition the toast while the mouse is dragging it, or
            # the window can jump back and feel impossible to move.
            if not self._dragging:
                self._position_window()
            self._show_no_activate()

        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

        if duration_ms is not None:
            self.after_id = self.root.after(duration_ms, self.hide)
        log_category(
            "notifications",
            "shown",
            kind=kind,
            duration_ms=duration_ms,
            message=message,
            manual_position=self.manual_position,
            force_recreate=force_recreate,
        )

    def hide(self) -> None:
        self.after_id = None
        if self.window is not None and self.window.winfo_exists():
            try:
                self.window.withdraw()
                log_category("notifications", "hidden", kind=self._last_kind)
            except Exception:
                pass

    def destroy(self) -> None:
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None
            self.label = None

    def _position_window(self) -> None:
        if self.window is None:
            return
        if self._dragging:
            return
        self.window.update_idletasks()
        width = max(360, self.window.winfo_reqwidth())
        height = max(70, self.window.winfo_reqheight())
        if self.manual_position is not None:
            x, y = self.manual_position
        else:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            x = max(20, screen_w - width - 28)
            y = max(20, screen_h - height - 72)
        self.window.geometry(f"{width}x{height}{self._format_geometry_position(x, y)}")

    def _format_geometry_position(self, x: int, y: int) -> str:
        return f"{int(x):+d}{int(y):+d}"

    def reset_manual_position_if_mostly_offscreen(self, min_visible_width: int = 10, min_visible_height: int = 10) -> None:
        """Keep a manually dragged toast position unless it is fully unreachable.

        Older builds reset the position when the toast was close to the screen
        edge/taskbar. That made the recording notification jump back while the
        user was dictating. Now a dragged position is respected; we only clamp
        it if almost the whole toast is outside the visible screen. Double-click
        the toast to intentionally reset it to the default corner.
        """
        if self.manual_position is None:
            return
        try:
            x, y = self.manual_position
            if self.window is not None and self.window.winfo_exists():
                self.window.update_idletasks()
                width = max(360, self.window.winfo_reqwidth())
                height = max(70, self.window.winfo_reqheight())
            else:
                width = 360
                height = 70
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            visible_w = max(0, min(x + width, screen_w) - max(x, 0))
            visible_h = max(0, min(y + height, screen_h) - max(y, 0))
            if visible_w < min_visible_width or visible_h < min_visible_height:
                new_x = min(max(x, -(width - min_visible_width)), max(0, screen_w - min_visible_width))
                new_y = min(max(y, -(height - min_visible_height)), max(0, screen_h - min_visible_height))
                self.manual_position = (int(new_x), int(new_y))
                log_info(
                    "Notification manual position clamped because it was nearly offscreen",
                    old_x=x,
                    old_y=y,
                    new_x=new_x,
                    new_y=new_y,
                    width=width,
                    height=height,
                    visible_width=visible_w,
                    visible_height=visible_h,
                )
                self._emit_position_changed()
        except Exception as exc:
            log_exception("Could not validate notification position", exc)

    def _emit_position_changed(self) -> None:
        callback = self.on_position_changed
        if callback is None:
            return
        try:
            callback(self.manual_position)
        except Exception as exc:
            log_exception("Notification position callback failed", exc, manual_position=self.manual_position)

    def _bind_drag_handlers(self, widget: tk.Widget) -> None:
        widget.bind("<ButtonPress-1>", self._start_drag, add="+")
        widget.bind("<B1-Motion>", self._drag_to, add="+")
        widget.bind("<ButtonRelease-1>", self._finish_drag, add="+")
        widget.bind("<Double-Button-1>", self._reset_manual_position, add="+")

    def _start_drag(self, event: tk.Event) -> str:
        if self.window is None or not self.window.winfo_exists():
            return "break"
        self._dragging = True
        self._drag_pointer_start = (int(event.x_root), int(event.y_root))
        self._drag_window_start = (self.window.winfo_x(), self.window.winfo_y())
        try:
            self._show_no_activate()
        except Exception:
            pass
        return "break"

    def _drag_to(self, event: tk.Event) -> str:
        if (
            self.window is None
            or not self.window.winfo_exists()
            or self._drag_pointer_start is None
            or self._drag_window_start is None
        ):
            return "break"
        pointer_x, pointer_y = self._drag_pointer_start
        window_x, window_y = self._drag_window_start
        new_x = window_x + int(event.x_root) - pointer_x
        new_y = window_y + int(event.y_root) - pointer_y
        self.manual_position = (new_x, new_y)
        self.window.geometry(self._format_geometry_position(new_x, new_y))
        return "break"

    def _finish_drag(self, event: tk.Event) -> str:
        if self.window is not None and self.window.winfo_exists():
            self.manual_position = (self.window.winfo_x(), self.window.winfo_y())
            log_info("Notification position changed", x=self.manual_position[0], y=self.manual_position[1])
            self._emit_position_changed()
        self._dragging = False
        self._drag_pointer_start = None
        self._drag_window_start = None
        return "break"

    def _reset_manual_position(self, event: tk.Event) -> str:
        self.manual_position = None
        self._dragging = False
        self._drag_pointer_start = None
        self._drag_window_start = None
        self._position_window()
        log_info("Notification position reset")
        self._emit_position_changed()
        return "break"

    def _show_no_activate(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            self._make_no_activate_on_windows()
            if IS_WINDOWS:
                self.window.update_idletasks()
                hwnd = int(self.window.winfo_id())
                user32 = ctypes.windll.user32
                SW_SHOWNOACTIVATE = 4
                HWND_TOPMOST = -1
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            else:
                self.window.deiconify()
                self.window.lift()
        except Exception:
            try:
                self.window.deiconify()
            except Exception:
                pass

    def _make_no_activate_on_windows(self) -> None:
        if not IS_WINDOWS or self.window is None:
            return
        try:
            self.window.update_idletasks()
            hwnd = int(self.window.winfo_id())
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            style = int(get_style(hwnd, GWL_EXSTYLE))
            set_style(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass
