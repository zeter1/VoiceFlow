from __future__ import annotations

import unittest

from voiceflow_app.audio_devices import list_input_devices


class _Default:
    def __init__(self, input_index: int):
        self.device = (input_index, None)


class FakeAudioBackend:
    def __init__(self):
        self.default = _Default(2)
        self._devices = [
            {"name": "Speakers", "max_input_channels": 0, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 1, "hostapi": 0},
            {"name": "Studio Mic", "max_input_channels": 2, "hostapi": 1},
        ]
        self._hostapis = {
            0: {"name": "MME"},
            1: {"name": "WASAPI"},
        }

    def query_devices(self, *args, **kwargs):
        return list(self._devices)

    def query_hostapis(self, index: int):
        return self._hostapis[index]


class AudioDeviceCatalogTests(unittest.TestCase):
    def test_lists_only_input_devices_with_stable_labels(self) -> None:
        result = list_input_devices(FakeAudioBackend())
        self.assertEqual(
            result,
            [
                (1, "1: USB Mic — MME — 1 ch"),
                (2, "2: Studio Mic — WASAPI — 2 ch — по умолчанию"),
            ],
        )

    def test_hostapi_failure_does_not_hide_microphone(self) -> None:
        backend = FakeAudioBackend()

        def broken_hostapi(_index: int):
            raise RuntimeError("host API unavailable")

        backend.query_hostapis = broken_hostapi
        result = list_input_devices(backend)
        self.assertEqual(result[0], (1, "1: USB Mic — 1 ch"))
        self.assertTrue(result[1][1].endswith("— по умолчанию"))


if __name__ == "__main__":
    unittest.main()
