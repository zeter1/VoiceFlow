from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import unittest

from voiceflow_app import config


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "voiceflow_app"


class StartupBootstrapTests(unittest.TestCase):
    def test_problem_log_folder_is_canonical(self) -> None:
        self.assertEqual(config.LOG_DIR.name, "Логи проблем")
        self.assertEqual(config.LOG_DIR.parent, config.APP_DIR)

    def test_entrypoint_import_does_not_load_heavy_runtime_or_diagnostics(self) -> None:
        code = (
            "import sys; import voiceflow_app.entrypoint; "
            "assert 'voiceflow_app.dependencies' not in sys.modules; "
            "assert 'voiceflow_app.diagnostics' not in sys.modules; "
            "assert 'voiceflow_app.app.main_window' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_main_acquires_mutex_before_diagnostics_and_gui_imports(self) -> None:
        source = (PACKAGE / "entrypoint.py").read_text(encoding="utf-8")
        main_source = source[source.index("def main()"):]
        lock_pos = main_source.index("acquire_single_instance_lock()")
        diagnostics_pos = main_source.index("from .diagnostics import")
        app_pos = main_source.index("from .app.main_window import")
        self.assertLess(lock_pos, diagnostics_pos)
        self.assertLess(diagnostics_pos, app_pos)

    def test_diagnostics_has_no_import_time_logging_or_snapshot_writes(self) -> None:
        path = PACKAGE / "diagnostics.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = {"configure_logging", "write_diagnostics_snapshot"}
        top_level_calls = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id in forbidden:
                    top_level_calls.append(func.id)
        self.assertFalse(
            top_level_calls,
            f"diagnostics import must be side-effect free: {top_level_calls}",
        )


if __name__ == "__main__":
    unittest.main()
