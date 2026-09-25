from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "voiceflow_app" / "services"
CONTRACTS = SERVICES / "contracts.py"


def class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError(f"{class_name} not found in {path}")


class ServiceContractTests(unittest.TestCase):
    def test_concrete_services_cover_declared_protocol_methods(self) -> None:
        mappings = {
            "AudioRecorderContract": (SERVICES / "audio.py", "AudioRecorder"),
            "TranscriberContract": (SERVICES / "transcription.py", "LocalTranscriber"),
            "TextCleanerContract": (SERVICES / "text_cleaner.py", "LocalTextCleaner"),
        }
        for protocol_name, (implementation_path, implementation_name) in mappings.items():
            with self.subTest(protocol=protocol_name):
                required = class_methods(CONTRACTS, protocol_name)
                actual = class_methods(implementation_path, implementation_name)
                self.assertTrue(required <= actual, sorted(required - actual))

    def test_transcriber_constructor_exposes_environment_and_model_injection_seams(self) -> None:
        path = SERVICES / "transcription.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        transcriber = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "LocalTranscriber"
        )
        init = next(
            node for node in transcriber.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        kwonly = {arg.arg for arg in init.args.kwonlyargs}
        self.assertIn("backend_runtime", kwonly)
        self.assertIn("model_factory", kwonly)


if __name__ == "__main__":
    unittest.main()
