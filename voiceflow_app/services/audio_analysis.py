"""Realtime audio statistics without eager audio/runtime imports.

Pure numeric policy helpers are testable without NumPy. The production wrapper
imports the configured NumPy backend lazily only when real frames are analysed.
"""

from __future__ import annotations

from typing import Optional


def activity_threshold_for_rms(rms: float) -> float:
    return max(0.006, min(0.028, float(rms) * 0.72))


def adaptive_speech_threshold(
    noise_floor_rms: float,
    median_rms: float,
    speech_high_rms: float,
) -> float:
    return max(
        0.006,
        min(
            0.075,
            max(
                float(noise_floor_rms) * 2.1,
                float(median_rms) * 1.25,
                float(speech_high_rms) * 0.32,
            ),
        ),
    )


def build_audio_stats(
    *,
    sample_count: int,
    sample_rate: int,
    peak: float,
    rms: float,
    active_count: int,
    last_active_sample: Optional[int],
    activity_threshold: float,
    window_size: int = 0,
    window_count: int = 0,
    speech_threshold: Optional[float] = None,
    noise_floor_rms: float = 0.0,
    speech_high_rms: float = 0.0,
    last_speech_window: Optional[int] = None,
) -> dict[str, float]:
    if sample_count <= 0 or sample_rate <= 0:
        return {
            "duration": 0.0,
            "rms": 0.0,
            "peak": 0.0,
            "peak_to_rms": 0.0,
            "active_ratio": 0.0,
            "trailing_silence": 0.0,
            "speech_trailing_silence": 0.0,
            "pause_seconds": 0.0,
            "speech_threshold": float(activity_threshold),
            "noise_floor_rms": 0.0,
            "speech_high_rms": 0.0,
        }

    duration = float(sample_count) / float(sample_rate)
    trailing_silence = duration
    if last_active_sample is not None:
        trailing_silence = max(
            0.0,
            float(sample_count - int(last_active_sample) - 1) / float(sample_rate),
        )

    resolved_speech_threshold = (
        float(speech_threshold)
        if speech_threshold is not None
        else float(activity_threshold)
    )
    speech_trailing_silence = trailing_silence
    if window_count >= 4 and window_size > 0:
        if last_speech_window is None:
            speech_trailing_silence = duration
        else:
            silent_windows = max(0, int(window_count - int(last_speech_window) - 1))
            speech_trailing_silence = (
                silent_windows * int(window_size) / float(sample_rate)
            )

    pause_seconds = max(trailing_silence, speech_trailing_silence)
    return {
        "duration": duration,
        "rms": float(rms),
        "peak": float(peak),
        "peak_to_rms": float(peak) / max(float(rms), 1e-9),
        "active_ratio": float(active_count) / float(sample_count),
        "trailing_silence": trailing_silence,
        "speech_trailing_silence": speech_trailing_silence,
        "pause_seconds": pause_seconds,
        "speech_threshold": resolved_speech_threshold,
        "noise_floor_rms": float(noise_floor_rms),
        "speech_high_rms": float(speech_high_rms),
    }


def stream_audio_stats(frames: list[object], sample_rate: int) -> dict[str, float]:
    """Analyse production NumPy frames while keeping NumPy out of import-time."""

    from ..dependencies import np

    if not frames or sample_rate <= 0:
        return build_audio_stats(
            sample_count=0,
            sample_rate=sample_rate,
            peak=0.0,
            rms=0.0,
            active_count=0,
            last_active_sample=None,
            activity_threshold=0.006,
        )

    audio = np.concatenate(frames, axis=0)
    if audio.ndim > 1:
        audio = audio.astype(np.float32).mean(axis=1)
    else:
        audio = audio.astype(np.float32).reshape(-1)
    if audio.size and np.nanmax(np.abs(audio)) > 2.0:
        audio = audio / 32768.0
    if audio.size:
        audio = audio - float(np.mean(audio))
    if audio.size == 0:
        return build_audio_stats(
            sample_count=0,
            sample_rate=sample_rate,
            peak=0.0,
            rms=0.0,
            active_count=0,
            last_active_sample=None,
            activity_threshold=0.006,
        )

    abs_audio = np.abs(audio)
    peak = float(np.max(abs_audio)) if abs_audio.size else 0.0
    rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
    threshold = activity_threshold_for_rms(rms)
    active = np.where(abs_audio > threshold)[0]
    last_active_sample = int(active[-1]) if active.size else None

    window_size = 0
    window_count = 0
    speech_threshold: Optional[float] = None
    noise_floor_rms = 0.0
    speech_high_rms = 0.0
    last_speech_window: Optional[int] = None
    try:
        window_size = max(1, int(sample_rate * 0.06))
        usable_size = (audio.size // window_size) * window_size
        if usable_size >= window_size * 4:
            windowed = audio[:usable_size].reshape(-1, window_size)
            win_rms = np.sqrt(np.mean(windowed * windowed, axis=1))
            window_count = int(win_rms.size)
            if win_rms.size:
                noise_floor_rms = float(np.percentile(win_rms, 20))
                speech_high_rms = float(np.percentile(win_rms, 85))
                median_rms = float(np.median(win_rms))
                speech_threshold = adaptive_speech_threshold(
                    noise_floor_rms,
                    median_rms,
                    speech_high_rms,
                )
                speech_windows = np.where(win_rms > speech_threshold)[0]
                if speech_windows.size:
                    last_speech_window = int(speech_windows[-1])
    except Exception:
        window_size = 0
        window_count = 0
        speech_threshold = None
        noise_floor_rms = 0.0
        speech_high_rms = 0.0
        last_speech_window = None

    return build_audio_stats(
        sample_count=int(audio.size),
        sample_rate=sample_rate,
        peak=peak,
        rms=rms,
        active_count=int(active.size),
        last_active_sample=last_active_sample,
        activity_threshold=threshold,
        window_size=window_size,
        window_count=window_count,
        speech_threshold=speech_threshold,
        noise_floor_rms=noise_floor_rms,
        speech_high_rms=speech_high_rms,
        last_speech_window=last_speech_window,
    )
