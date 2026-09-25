from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "voiceflow.py"
PACKAGE = ROOT / "voiceflow_app"

EXPECTED_MODULES = {
    "runtime.py",
    "composition.py",
    "worker_messages.py",
    "config.py",
    "dependencies.py",
    "desktop_delivery.py",
    "audio_devices.py",
    "cuda_runtime.py",
    "diagnostics.py",
    "hotkey_config.py",
    "settings.py",
    "single_instance.py",
    "voice_commands.py",
    "windows.py",
    "windows_insertion.py",
    "windows_startup.py",
    "entrypoint.py",
    "core/realtime.py",
    "core/realtime_policy.py",
    "core/recording_state.py",
    "core/hotkey_state.py",
    "services/audio.py",
    "services/audio_analysis.py",
    "services/contracts.py",
    "services/transcription.py",
    "services/text_cleaner.py",
    "ui/notifications.py",
    "ui/tray.py",
    "app/main_window.py",
    "app/ports.py",
    "app/session_controller.py",
    "app/ui.py",
    "app/controls.py",
    "app/hotkeys.py",
    "app/recording.py",
    "app/realtime_delivery.py",
    "app/realtime_worker.py",
    "app/realtime_text_pipeline.py",
    "app/streaming.py",
    "app/worker_dispatch.py",
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
        for relative in ("core/realtime.py", "core/realtime_policy.py", "core/recording_state.py", "core/hotkey_state.py"):
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

    def test_package_uses_explicit_imports_only(self) -> None:
        checked = [ENTRYPOINT, *sorted(PACKAGE.rglob("*.py"))]
        for path in checked:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            wildcard_imports = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
            ]
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertFalse(wildcard_imports, "VoiceFlow package must use explicit imports")

    def test_internal_modules_do_not_depend_on_runtime_facade(self) -> None:
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            if path.name == "runtime.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module == "runtime" and node.level in {1, 2}:
                        offenders.append(str(path.relative_to(ROOT)))
                    if node.module == "voiceflow_app.runtime":
                        offenders.append(str(path.relative_to(ROOT)))
        self.assertFalse(
            sorted(set(offenders)),
            f"Internal modules must import focused owners, not runtime.py: {sorted(set(offenders))}",
        )

    def test_runtime_is_reexport_only_external_compatibility_facade(self) -> None:
        runtime = PACKAGE / "runtime.py"
        source = runtime.read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 140)
        self.assertIn("External compatibility facade", source)
        tree = ast.parse(source, filename=str(runtime))
        unexpected = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            if isinstance(node, ast.ImportFrom):
                if node.module == "__future__":
                    continue
                if node.level == 1:
                    continue
            unexpected.append(type(node).__name__)
        self.assertFalse(
            unexpected,
            f"runtime.py must contain only package re-exports, not implementation: {unexpected}",
        )

    def test_windows_facade_is_reexport_only(self) -> None:
        path = PACKAGE / "windows.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        implementations = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        self.assertFalse(implementations, "windows.py must stay a compatibility re-export facade")

    def test_audio_device_discovery_is_not_owned_by_dependencies(self) -> None:
        source = (PACKAGE / "dependencies.py").read_text(encoding="utf-8")
        self.assertNotIn("def get_input_devices", source)
        self.assertTrue((PACKAGE / "audio_devices.py").is_file())

    def test_transcriber_does_not_probe_cuda_environment_directly(self) -> None:
        source = (PACKAGE / "services" / "transcription.py").read_text(encoding="utf-8")
        self.assertNotIn("find_windows_dll", source)
        self.assertNotIn("subprocess.run", source)
        self.assertIn("CudaRuntimeProbe", source)

    def test_composition_module_has_no_eager_concrete_service_imports(self) -> None:
        path = PACKAGE / "composition.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        eager = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.endswith(("services.audio", "services.transcription", "services.text_cleaner", "ui.notifications", "ui.tray")):
                    eager.append(node.module)
        self.assertFalse(eager, f"Composition defaults must stay lazy for headless imports: {eager}")

    def test_recording_uses_public_transcriber_backend_contract(self) -> None:
        source = (PACKAGE / "app" / "recording.py").read_text(encoding="utf-8")
        self.assertNotIn("_windows_missing_cuda_dlls", source)
        self.assertIn("backend_candidates(", source)

    def test_realtime_worker_text_pipeline_and_dispatch_are_extracted(self) -> None:
        streaming = (PACKAGE / "app" / "streaming.py").read_text(encoding="utf-8")
        engine = (PACKAGE / "app" / "realtime_worker.py").read_text(encoding="utf-8")
        text_pipeline = (PACKAGE / "app" / "realtime_text_pipeline.py").read_text(encoding="utf-8")
        dispatch = (PACKAGE / "app" / "worker_dispatch.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(streaming.splitlines()), 420)
        self.assertLessEqual(len(engine.splitlines()), 470)
        self.assertLessEqual(len(text_pipeline.splitlines()), 220)
        self.assertLessEqual(len(dispatch.splitlines()), 370)
        self.assertNotIn("def _frames_to_float_mono", streaming)
        self.assertNotIn("def _handle_worker_message", streaming)
        self.assertNotIn("SessionTranscriptionRequest(", streaming)
        self.assertNotIn("def process_frames", streaming)
        self.assertNotIn("core_prepare_stream_chunk_for_paste", streaming)
        self.assertNotIn("def _clean_stream_chunk_for_commit", streaming)
        self.assertNotIn("def _split_stream_voice_command", streaming)
        self.assertNotIn("def _get_missing_final_tail", streaming)
        self.assertIn("RealtimeWorkerEngine(", streaming)
        self.assertIn("RealtimeTextPipeline", (PACKAGE / "app" / "main_window.py").read_text(encoding="utf-8"))
        self.assertIn("plan_stream_result(", dispatch)
        self.assertIn("commit_meta=commit_meta", dispatch)
        self.assertIn("decide_chunk_commit(", engine)
        self.assertIn("coerce_worker_message(", dispatch)

    def test_realtime_text_pipeline_is_headless(self) -> None:
        path = PACKAGE / "app" / "realtime_text_pipeline.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"tkinter", "dependencies", "voiceflow_app.dependencies", "windows", "pyautogui"}
        self.assertFalse(sorted(imported & forbidden), f"Realtime text pipeline must stay headless: {sorted(imported & forbidden)}")

    def test_realtime_delivery_uses_ports_and_streaming_has_no_desktop_delivery_imports(self) -> None:
        delivery = (PACKAGE / "app" / "realtime_delivery.py").read_text(encoding="utf-8")
        streaming = (PACKAGE / "app" / "streaming.py").read_text(encoding="utf-8")
        actions = (PACKAGE / "app" / "actions.py").read_text(encoding="utf-8")
        dispatch = (PACKAGE / "app" / "worker_dispatch.py").read_text(encoding="utf-8")
        messages = (PACKAGE / "worker_messages.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(streaming.splitlines()), 330)
        self.assertLessEqual(len(delivery.splitlines()), 120)
        self.assertNotIn("pyautogui", streaming)
        self.assertNotIn("get_paste_target", streaming)
        self.assertNotIn("send_ctrl_v_native", streaming)
        self.assertNotIn("paste_text_to_current_target", streaming)
        self.assertNotIn("get_paste_target", actions)
        self.assertNotIn("send_ctrl_v_native", actions)
        desktop = (PACKAGE / "desktop_delivery.py").read_text(encoding="utf-8")
        self.assertNotIn("from .dependencies", desktop)
        desktop_tree = ast.parse(desktop, filename="desktop_delivery.py")
        eager_from_modules = {
            node.module
            for node in desktop_tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("windows_insertion", eager_from_modules)
        self.assertNotIn("def _process_audio_worker", streaming)
        self.assertNotIn('msg_type == "result"', dispatch)
        self.assertNotIn('msg_type == "error"', dispatch)
        self.assertNotIn("STREAM_FINISHED", messages)
        self.assertNotIn("STREAM_FINISH_TIMEOUT", messages)
        tree = ast.parse(delivery, filename="realtime_delivery.py")
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"tkinter", "dependencies", "voiceflow_app.dependencies", "windows", "pyautogui"}
        self.assertFalse(sorted(imported & forbidden), f"Realtime delivery controller must stay headless: {sorted(imported & forbidden)}")

    def test_realtime_worker_engine_is_headless(self) -> None:
        path = PACKAGE / "app" / "realtime_worker.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertNotIn("tkinter", imported)
        self.assertNotIn("dependencies", imported)
        self.assertNotIn("voiceflow_app.dependencies", imported)

    def test_queue_producers_use_typed_worker_contract(self) -> None:
        for relative in ("app/streaming.py", "app/hotkeys.py", "ui/tray.py"):
            source = (PACKAGE / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertNotIn("worker_queue.put((", source)

    def test_audio_analysis_has_no_eager_runtime_dependency_import(self) -> None:
        path = PACKAGE / "services" / "audio_analysis.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        eager = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "dependencies":
                eager.append(node.module)
        self.assertFalse(eager, "audio_analysis must import NumPy lazily inside production analysis")

    def test_source_mode_runtime_data_stays_at_repository_root(self) -> None:
        source = (PACKAGE / "config.py").read_text(encoding="utf-8")
        self.assertIn('if package_dir.name == "voiceflow_app":', source)
        self.assertIn("return package_dir.parent", source)
        self.assertIn('LOG_DIR = APP_DIR / "Логи проблем"', source)

    def test_runtime_user_data_remains_ignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        required = {"Логи проблем/", "voiceflow_logs/", "voiceflow_settings/", "dictation_text.txt", "*.wav"}
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
