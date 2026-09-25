from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "voiceflow.py"
PACKAGE = ROOT / "voiceflow_app"

EXPECTED_MODULES = {
    "runtime.py",
    "context.py",
    "entrypoint.py",
    "services/audio.py",
    "services/transcription.py",
    "services/text_cleaner.py",
    "ui/notifications.py",
    "ui/tray.py",
    "app/main_window.py",
    "app/ui.py",
    "app/controls.py",
    "app/hotkeys.py",
    "app/recording.py",
    "app/streaming.py",
    "app/actions.py",
}


class RepositoryContractTests(unittest.TestCase):
    def test_all_repository_python_sources_parse(self) -> None:
        sources = [ENTRYPOINT, *sorted(PACKAGE.rglob("*.py")), *sorted((ROOT / "tests").glob("*.py"))]
        for source_path in sources:
            source = source_path.read_text(encoding="utf-8")
            with self.subTest(path=source_path.relative_to(ROOT)):
                ast.parse(source, filename=str(source_path))

    def test_voiceflow_is_a_thin_compatibility_entrypoint(self) -> None:
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("voiceflow_app.entrypoint", source)
        self.assertLessEqual(len(source.splitlines()), 40)

    def test_modular_architecture_boundaries_exist(self) -> None:
        actual = {str(path.relative_to(PACKAGE)).replace("\\", "/") for path in PACKAGE.rglob("*.py")}
        missing = sorted(EXPECTED_MODULES - actual)
        self.assertFalse(missing, f"Missing modular VoiceFlow modules: {missing}")

    def test_refactor_does_not_recreate_a_single_giant_module(self) -> None:
        oversized = {}
        for path in PACKAGE.rglob("*.py"):
            count = len(path.read_text(encoding="utf-8").splitlines())
            if count > 1800:
                oversized[str(path.relative_to(ROOT))] = count
        self.assertFalse(oversized, f"Unexpected monolithic module(s): {oversized}")

    def test_source_mode_runtime_data_stays_at_repository_root(self) -> None:
        source = (PACKAGE / "runtime.py").read_text(encoding="utf-8")
        self.assertIn('if package_dir.name == "voiceflow_app":', source)
        self.assertIn("return package_dir.parent", source)

    def test_runtime_user_data_remains_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        required = {"voiceflow_logs/", "voiceflow_settings/", "dictation_text.txt", "*.wav"}
        missing = sorted(item for item in required if item not in gitignore)
        self.assertFalse(missing, f"Runtime/private data must stay ignored: {missing}")

    def test_core_local_speech_dependencies_are_declared(self) -> None:
        requirements = {
            line.strip().lower()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        for dependency in {"faster-whisper", "sounddevice", "numpy"}:
            self.assertIn(dependency, requirements)

    def test_ai_navigation_documentation_is_present(self) -> None:
        for relative in ("AGENTS.md", "docs/AI_CONTEXT.md", "docs/ARCHITECTURE.md", "docs/DEVELOPMENT.md"):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
