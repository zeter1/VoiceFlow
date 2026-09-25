"""Shared compatibility namespace for VoiceFlow application mixins.

It re-exports the stable runtime surface plus extracted services so methods
moved out of the former monolith keep the same global names. New code should
prefer direct imports instead of expanding this namespace without need.
"""

from .runtime import *  # noqa: F401,F403
from .services.audio import AudioRecorder
from .services.transcription import LocalTranscriber
from .services.text_cleaner import LocalTextCleaner
from .ui.notifications import NotificationManager
from .ui.tray import TrayManager
