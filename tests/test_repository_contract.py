from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "voiceflow.py"
PACKAGE = ROOT / "voiceflow_app"

EXPECTED_MODULES = {
    "runtime.py",
    "config.py",
    "dependencies.py",
    "diagnostics.py",
    "hotkey_config.py",
    "settings.py",
    "voice_commands.py",
    "windows.py",
    "entrypoint.py",
    "core/realtime.py",
    "core/recording_state.py",
    "core/hotkey_state.py",
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

    def test_pure_core_stays_independent_from_runtime_and_ui(self) -> None:
        forbidden = {"voiceflow_app.runtime", "voiceflow_app.context", "tkinter"}
        for relative in ("core/realtime.py", "core/recording_state.py", "core/hotkey_state.py"):
            path = PACKAGE / relative
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            bad = sorted(
                name
                for name in imported
                if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
            )
            self.assertFalse(bad, f"{relative} must remain pure/testable; forbidden imports: {bad}")

    def test_context_bridge_is_removed(self) -> None:
        self.assertFalse((PACKAGE / "context.py").exists())

    def test_app_and_entrypoint_do_not_use_context_or_wildcard_imports(self) -> None:
        checked = [
            PACKAGE / "entrypoint.py",
            *sorted((PACKAGE / "app").glob("*.py")),
        ]
        for path in checked:
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertNotIn("context import", source)
                tree = ast.parse(source, filename=str(path))
                wildcard_imports = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom)
                    and any(alias.name == "*" for alias in node.names)
                ]
                self.assertFalse(wildcard_imports, "App orchestration must use explicit imports")

    def test_runtime_is_a_small_compatibility_facade(self) -> None:
        runtime = PACKAGE / "runtime.py"
        source = runtime.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 180)
        self.assertIn("Backward-compatible runtime facade", source)

    def test_explicit_runtime_compat_imports_are_exported(self) -> None:
        runtime_path = PACKAGE / "runtime.py"
        runtime_tree = ast.parse(runtime_path.read_text(encoding="utf-8"), filename=str(runtime_path))
        exported = set()
        for node in runtime_tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    exported.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        exported.add(alias.asname or alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                exported.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        exported.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                exported.add(node.target.id)

        missing = {}
        for path in sorted((PACKAGE / "app").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            requested = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "runtime" and node.level == 2:
                    requested.update(alias.name for alias in node.names if alias.name != "*")
            unresolved = sorted(requested - exported)
            if unresolved:
                missing[str(path.relative_to(ROOT))] = unresolved

        self.assertFalse(
            missing,
            f"Compatibility runtime must export every symbol still imported by app modules: {missing}",
        )

    def test_source_mode_runtime_data_stays_at_repository_root(self) -> None:
        source = (PACKAGE / "config.py").read_text(encoding="utf-8")
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
