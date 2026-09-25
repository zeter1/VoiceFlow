"""Application composition root.

Concrete desktop services are imported lazily by the default builder so this
module stays importable in offline/headless contract tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from .app.ports import (
    NotificationFactory,
    TextInsertionPort,
    TrayFactory,
    VoiceActionPort,
)
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
    text_inserter: TextInsertionPort
    voice_action_executor: VoiceActionPort


def build_default_application_services() -> ApplicationServices:
    from .desktop_delivery import (
        CurrentTargetTextInsertionAdapter,
        CurrentTargetVoiceActionAdapter,
    )
    from .services.audio import AudioRecorder
    from .services.text_cleaner import LocalTextCleaner
    from .services.transcription import LocalTranscriber
    from .ui.notifications import NotificationManager
    from .ui.tray import TrayManager

    text_inserter = CurrentTargetTextInsertionAdapter()
    return ApplicationServices(
        recorder=AudioRecorder(),
        transcriber=LocalTranscriber(),
        cleaner=LocalTextCleaner(),
        notification_factory=NotificationManager,
        tray_factory=TrayManager,
        text_inserter=text_inserter,
        voice_action_executor=CurrentTargetVoiceActionAdapter(text_inserter),
    )
