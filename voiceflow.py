"""Minimal compatibility entrypoint for VoiceFlow.

All startup logic lives in voiceflow_app.entrypoint. Keeping this file free of
application imports lets packaged --self-test report import failures instead of
hanging behind a windowed PyInstaller error dialog.
"""

from voiceflow_app.entrypoint import main, run_self_test


if __name__ == "__main__":
    main()
