from __future__ import annotations

from types import SimpleNamespace
import unittest

from voiceflow_app.cuda_runtime import CudaRuntimeProbe, select_backend_candidates


class CudaRuntimeTests(unittest.TestCase):
    def test_backend_policy_preserves_cpu_and_cuda_fallback_order(self) -> None:
        self.assertEqual(
            select_backend_candidates("cpu", "float16", cuda_available=True),
            (("cpu", "int8"),),
        )
        self.assertEqual(
            select_backend_candidates("cuda", "auto", cuda_available=True),
            (
                ("cuda", "int8_float16"),
                ("cuda", "float16"),
                ("cuda", "int8"),
                ("cpu", "int8"),
            ),
        )
        self.assertEqual(
            select_backend_candidates("auto", "float16", cuda_available=False),
            (("cpu", "int8"),),
        )

    def test_missing_windows_runtime_dlls_short_circuit_to_cpu(self) -> None:
        probe = CudaRuntimeProbe(
            is_windows=True,
            dll_finder=lambda _name: None,
            process_runner=lambda *args, **kwargs: self.fail("preflight runner must not be called"),
        )
        plan = probe.plan_candidates("auto", "auto")
        self.assertEqual(plan.candidates, (("cpu", "int8"),))
        self.assertTrue(plan.missing_runtime_dlls)

        result = probe.preflight("small", "int8_float16")
        self.assertFalse(result.ok)
        self.assertIn("Missing CUDA 12 runtime DLLs", result.details)

    def test_successful_preflight_is_cached(self) -> None:
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout="VOICEFLOW_CUDA_OK\n", stderr="")

        probe = CudaRuntimeProbe(
            is_windows=True,
            dll_finder=lambda name: f"C:/fake/{name}",
            process_runner=runner,
            executable="python",
        )
        first = probe.preflight("small", "float16")
        second = probe.preflight("small", "float16")
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertTrue(second.cached)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
