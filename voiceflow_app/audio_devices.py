"""Audio input-device discovery adapter.

Enumeration logic accepts a small backend surface, so it can be tested without
a real microphone or PortAudio device.
"""

from __future__ import annotations

from typing import Protocol


class AudioDeviceBackend(Protocol):
    default: object

    def query_devices(self, *args: object, **kwargs: object) -> object:
        ...

    def query_hostapis(self, index: int) -> object:
        ...


def list_input_devices(backend: AudioDeviceBackend) -> list[tuple[int, str]]:
    devices = backend.query_devices()
    try:
        default_device = getattr(backend.default, "device")
        default_input = default_device[0]
    except Exception:
        default_input = None

    result: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        max_input_channels = int(device.get("max_input_channels", 0))
        if max_input_channels <= 0:
            continue

        hostapi_name = ""
        try:
            hostapi = backend.query_hostapis(int(device.get("hostapi", 0)))
            hostapi_name = str(hostapi.get("name", "")).strip()
        except Exception:
            hostapi_name = ""

        name = str(device.get("name", f"Input {index}")).strip()
        label = f"{index}: {name}"
        if hostapi_name:
            label += f" — {hostapi_name}"
        label += f" — {max_input_channels} ch"
        if default_input is not None and index == default_input:
            label += " — по умолчанию"
        result.append((index, label))

    return result


def get_input_devices() -> list[tuple[int, str]]:
    from .dependencies import sd

    return list_input_devices(sd)
