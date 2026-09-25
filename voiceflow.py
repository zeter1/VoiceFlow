"""Compatibility entrypoint for VoiceFlow.

Implementation lives in voiceflow_app. Keeping this file thin preserves
"python voiceflow.py" and the PyInstaller entrypoint used by GitHub Actions.
"""

from voiceflow_app.runtime import *  # noqa: F401,F403
from voiceflow_app.services.audio import AudioRecorder
from voiceflow_app.services.transcription import LocalTranscriber
from voiceflow_app.services.text_cleaner import LocalTextCleaner
from voiceflow_app.ui.notifications import NotificationManager
from voiceflow_app.ui.tray import TrayManager
from voiceflow_app.app.main_window import VoiceFlowOfflineApp
from voiceflow_app.entrypoint import main, run_self_test


if __name__ == "__main__":
    main()
