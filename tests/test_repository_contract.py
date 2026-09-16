from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "voiceflow.py"


class RepositoryContractTests(unittest.TestCase):
    def test_main_source_remains_valid_python(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        ast.parse(source, filename=str(SOURCE))

    def test_runtime_user_data_remains_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        required_patterns = {
            "voiceflow_logs/",
            "voiceflow_settings/",
            "dictation_text.txt",
            "*.wav",
        }
        missing = sorted(pattern for pattern in required_patterns if pattern not in gitignore)
        self.assertFalse(missing, f"Runtime/private data must stay ignored: {missing}")

    def test_core_local_speech_dependencies_are_declared(self) -> None:
        requirements = {
            line.strip().lower()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        for dependency in {"faster-whisper", "sounddevice", "numpy"}:
            self.assertIn(dependency, requirements)


if __name__ == "__main__":
    unittest.main()
