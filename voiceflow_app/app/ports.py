"""Application-facing UI adapter ports.

The module is stdlib-only so headless tests can construct application services
without importing Tkinter, pystray or other desktop implementations.
"""

from __future__ import annotations

from typing import Callable, Optional, Protocol


Position = Optional[tuple[int, int]]


class NotificationPort(Protocol):
    _last_kind: str
    manual_position: Position
    on_position_changed: Optional[Callable[[Position], None]]

    def show(
        self,
        message: str,
        kind: str = "idle",
        duration_ms: Optional[int] = 2200,
        force_recreate: bool = False,
    ) -> None:
        ...

    def hide(self) -> None:
        ...

    def destroy(self) -> None:
        ...

    def reset_manual_position_if_mostly_offscreen(self) -> None:
        ...


class TrayPort(Protocol):
    @property
    def available(self) -> bool:
        ...

    def start(self) -> bool:
        ...

    def stop(self) -> None:
        ...


NotificationFactory = Callable[[object], NotificationPort]
TrayFactory = Callable[[object], TrayPort]
