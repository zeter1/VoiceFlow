"""Microphone capture service.

Extracted from the historical monolithic voiceflow.py without intentional
runtime behavior changes. Shared runtime dependencies live in voiceflow_app.runtime.
"""

from __future__ import annotations

from ..runtime import *  # noqa: F401,F403 - transitional compatibility namespace


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._stream: Optional[sd.InputStream] = None
        self._frames: list[np.ndarray] = []
        self._is_recording = False
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start(self) -> None:
        if self._is_recording:
            return
        self._frames.clear()

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log_warning("Audio input stream status", status=str(status))
                print(status, file=sys.stderr)
            with self._lock:
                self._frames.append(indata.copy())

        self.sample_rate = self._choose_sample_rate()
        log_info("Starting audio recorder", device=self.device, sample_rate=self.sample_rate, channels=self.channels)
        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()
        self._is_recording = True
        log_info("Audio recorder started", device=self.device, sample_rate=self.sample_rate)

    def _choose_sample_rate(self) -> int:
        if self.device is None:
            return DEFAULT_SAMPLE_RATE
        try:
            info = sd.query_devices(self.device, "input")
            default_rate = int(float(info.get("default_samplerate", DEFAULT_SAMPLE_RATE)))
            return default_rate or DEFAULT_SAMPLE_RATE
        except Exception:
            return DEFAULT_SAMPLE_RATE

    def stop_discard(self) -> None:
        """Stop recording and discard buffered audio without creating a final WAV."""
        self.stop_stream_keep_frames()
        self.discard_frames()

    def stop_stream_keep_frames(self) -> None:
        """Stop the microphone stream but keep buffered frames for final processing."""
        if not self._is_recording:
            return
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._is_recording = False
        log_info("Audio stream stopped; buffered frames kept", frames=self.frames_count(), sample_rate=self.sample_rate)

    def discard_frames(self) -> None:
        with self._lock:
            discarded = len(self._frames)
            self._frames.clear()
        log_info("Audio frames discarded", frames=discarded)

    def stop_to_wav(self) -> Path:
        if not self._is_recording:
            raise RuntimeError("Recording is not active")
        assert self._stream is not None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._is_recording = False

        with self._lock:
            if not self._frames:
                raise RuntimeError("Пустая запись: звук не был записан")
            audio = np.concatenate(self._frames, axis=0)

        if audio.size == 0:
            raise RuntimeError("Пустая запись: звук не был записан")

        audio = self._prepare_audio_for_whisper(audio)
        audio, wav_sample_rate = self._resample_for_whisper(audio, self.sample_rate)

        tmp_dir = Path(tempfile.gettempdir()) / "voiceflow_offline"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = tmp_dir / f"recording_{int(time.time())}.wav"

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(wav_sample_rate)
            wf.writeframes(audio.tobytes())

        log_info(
            "Recording saved to wav",
            wav_path=wav_path,
            source_sample_rate=self.sample_rate,
            wav_sample_rate=wav_sample_rate,
            samples=int(audio.size),
        )
        return wav_path

    def frames_count(self) -> int:
        with self._lock:
            return len(self._frames)

    def get_frames_since(self, frame_index: int) -> tuple[list[np.ndarray], int, int]:
        """Return a copy of recorded frames after frame_index for pseudo-streaming."""
        with self._lock:
            total = len(self._frames)
            safe_index = max(0, min(frame_index, total))
            frames = [frame.copy() for frame in self._frames[safe_index:total]]
            sample_rate = self.sample_rate
        return frames, total, sample_rate

    def frames_to_wav(self, frames: list[np.ndarray], prefix: str = "stream_chunk") -> Optional[Path]:
        """Save selected in-memory frames to a temporary WAV file for chunk transcription."""
        if not frames:
            return None
        audio = np.concatenate(frames, axis=0)
        if audio.size == 0:
            return None

        try:
            prepared = self._prepare_audio_for_whisper(audio)
        except Exception:
            # A streaming chunk can be only silence or too short; skip it quietly.
            return None
        prepared, wav_sample_rate = self._resample_for_whisper(prepared, self.sample_rate)

        tmp_dir = Path(tempfile.gettempdir()) / "voiceflow_offline"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = tmp_dir / f"{prefix}_{int(time.time() * 1000)}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(wav_sample_rate)
            wf.writeframes(prepared.tobytes())
        return wav_path

    def _resample_for_whisper(self, audio: np.ndarray, source_rate: int) -> tuple[np.ndarray, int]:
        """Convert normalized int16 audio to Whisper's native 16 kHz mono."""
        target_rate = DEFAULT_SAMPLE_RATE
        if source_rate <= 0 or source_rate == target_rate or audio.size == 0:
            return audio.astype(np.int16, copy=False), source_rate or target_rate

        source = audio.astype(np.float32)
        new_size = max(1, int(round(source.size * target_rate / float(source_rate))))
        old_positions = np.arange(source.size, dtype=np.float32)
        new_positions = np.linspace(0, source.size - 1, new_size, dtype=np.float32)
        resampled = np.interp(new_positions, old_positions, source)
        resampled = np.clip(resampled, -32768.0, 32767.0).astype(np.int16)
        return resampled, target_rate

    def _prepare_audio_for_whisper(self, audio: np.ndarray) -> np.ndarray:
        """Normalize microphone audio before transcription.

        This keeps the app dependency-light but improves accuracy noticeably:
        mono conversion, DC offset removal, conservative silence trimming and
        peak normalization. It does not apply aggressive noise gates, because
        those can destroy quiet consonants in Russian speech.
        """
        if audio.ndim > 1:
            audio = audio.astype(np.float32).mean(axis=1)
        else:
            audio = audio.astype(np.float32).reshape(-1)

        if audio.size == 0:
            raise RuntimeError("Пустая запись: звук не был записан")

        # Convert int16-like samples to -1..1.
        if np.nanmax(np.abs(audio)) > 2.0:
            audio = audio / 32768.0

        # Remove DC offset and trim long leading/trailing silence.
        audio = audio - float(np.mean(audio))
        abs_audio = np.abs(audio)
        peak = float(np.max(abs_audio)) if abs_audio.size else 0.0
        if peak < 0.002:
            raise RuntimeError("Запись слишком тихая: микрофон почти ничего не записал")

        # Trim only clear silence, with padding, so Whisper receives less noise.
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        threshold = max(0.003, min(0.025, rms * 0.45))
        active = np.where(abs_audio > threshold)[0]
        if active.size:
            pad = int(self.sample_rate * 0.20)
            start = max(0, int(active[0]) - pad)
            end = min(audio.size, int(active[-1]) + pad)
            if end > start:
                audio = audio[start:end]

        # Normalize to a safe peak.
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 0:
            audio = audio / peak * 0.92

        audio = np.clip(audio, -1.0, 1.0)
        return (audio * 32767.0).astype(np.int16)
