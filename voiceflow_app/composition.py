"""Application composition root.

Concrete desktop services are imported lazily by the default builder so this
module stays importable in offline/headless contract tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from .app.ports import NotificationFactory, TrayFactory
from .services.contracts import (
    AudioRecorderContract,
    TextCleanerContract,
    TranscriberContract,
)


@dataclass(frozen=True)
class ApplicationServices:
    recorder: AudioRecorderContract
    transcriber: TranscriberContract
    cleaner: TextCleanerContract
    notification_factory: NotificationFactory
    tray_factory: TrayFactory


def build_default_application_services() -> ApplicationServices:
    from .services.audio import AudioRecorder
    from .services.text_cleaner import LocalTextCleaner
    from .services.transcription import LocalTranscriber
    from .ui.notifications import NotificationManager
    from .ui.tray import TrayManager

    return ApplicationServices(
        recorder=AudioRecorder(),
        transcriber=LocalTranscriber(),
        cleaner=LocalTextCleaner(),
        notification_factory=NotificationManager,
        tray_factory=TrayManager,
    )
