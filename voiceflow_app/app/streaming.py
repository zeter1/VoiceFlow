"""Realtime speech streaming, deduplication and worker queue.

Methods keep their original implementation while the physical module boundary
makes navigation, review and future extraction safer.
"""

from __future__ import annotations

from ..context import *  # noqa: F401,F403 - compatibility surface for extracted methods


class StreamingMixin:
    def _start_realtime_streaming_if_enabled(
        self,
        origin: str,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        # Realtime-only behavior. Hotkey recording pastes fragments into whatever
        # input field is focused at the moment each fragment is ready. This lets
        # the user move the caret between windows while one dictation session is running.
        # Main-window recording can only preview because it has no external caret.
        mode = "Вставлять фрагментами"
        self.realtime_streaming_mode_var.set(mode)
        if origin != "hotkey":
            mode = "Только превью"

        streaming_model_name = self._streaming_model_name(runtime_settings)
        if streaming_model_name != runtime_settings.whisper_model:
            log_info(
                "Realtime model optimized for faster CPU streaming",
                selected_model=runtime_settings.whisper_model,
                streaming_model=streaming_model_name,
                inference_device=runtime_settings.inference_device,
                speed_profile=runtime_settings.realtime_speed_profile,
            )

        self.streaming_stop_event = threading.Event()
        self.stream_context_reset_event.clear()
        self.stream_last_frame_index = self.recorder.frames_count()
        self.stream_inserted_any = False
        self.stream_inserted_text = ""
        self.stream_preview_raw_text = ""
        self.stream_preview_clean_text = ""
        self.streaming_thread = threading.Thread(
            target=self._realtime_stream_worker,
            args=(origin, mode, self.streaming_stop_event, session_id, runtime_settings),
            daemon=True,
        )
        self.streaming_thread.start()
        log_info(
            "Realtime streaming worker started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )
        self.log_state(
            "streaming",
            "streaming_worker_started",
            origin=origin,
            mode=mode,
            session_id=session_id,
            realtime_chunk_seconds=runtime_settings.realtime_chunk_seconds,
            realtime_speed_profile=runtime_settings.realtime_speed_profile,
            streaming_model=streaming_model_name,
        )

    def _stop_realtime_streaming(self, wait: bool = False) -> None:
        try:
            self.streaming_stop_event.set()
        except Exception:
            pass
        if wait and self.streaming_thread is not None and self.streaming_thread.is_alive():
            self.status_var.set("Обрабатываю последний фрагмент...")
            log_info("Waiting for realtime streaming worker to finish", session_id=self.recording_session_id)
            self.streaming_thread.join()
            self.streaming_thread = None

    def _finish_streaming_after_stop_async(
        self,
        session_id: int,
        finishing_thread: Optional[threading.Thread],
    ) -> None:
        def waiter() -> None:
            finish_message_sent = False
            try:
                if finishing_thread is not None and finishing_thread.is_alive():
                    log_info("Waiting for realtime streaming worker in background", session_id=session_id)
                    finishing_thread.join(timeout=STREAM_FINISH_TIMEOUT_SECONDS)
                    if finishing_thread.is_alive():
                        log_warning(
                            "Realtime streaming worker finish timed out; releasing UI",
                            session_id=session_id,
                            timeout_seconds=STREAM_FINISH_TIMEOUT_SECONDS,
                        )
                        self.worker_queue.put(("stream_finish_timeout", session_id))
                        finish_message_sent = True
                        return
            except BaseException as exc:
                log_exception("Background wait for realtime worker failed", exc, session_id=session_id)
            finally:
                if not finish_message_sent:
                    self.worker_queue.put(("stream_finished", session_id))

        threading.Thread(target=waiter, name="voiceflow-stream-finalizer", daemon=True).start()

    def _frames_to_float_mono(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(frames, axis=0)
        if audio.ndim > 1:
            audio = audio.astype(np.float32).mean(axis=1)
        else:
            audio = audio.astype(np.float32).reshape(-1)
        if audio.size and np.nanmax(np.abs(audio)) > 2.0:
            audio = audio / 32768.0
        if audio.size:
            audio = audio - float(np.mean(audio))
        return audio

    def _stream_audio_stats(self, frames: list[np.ndarray], sample_rate: int) -> dict[str, float]:
        audio = self._frames_to_float_mono(frames)
        if audio.size == 0 or sample_rate <= 0:
            return {"duration": 0.0, "rms": 0.0, "peak": 0.0, "trailing_silence": 0.0}
        duration = float(audio.size) / float(sample_rate)
        abs_audio = np.abs(audio)
        peak = float(np.max(abs_audio)) if abs_audio.size else 0.0
        rms = float(np.sqrt(np.mean(audio * audio))) if audio.size else 0.0
        threshold = max(0.006, min(0.028, rms * 0.72))
        active = np.where(abs_audio > threshold)[0]
        active_ratio = float(active.size) / float(audio.size) if audio.size else 0.0
        peak_to_rms = peak / max(rms, 1e-9)
        trailing_silence = duration
        if active.size:
            trailing_silence = float(audio.size - int(active[-1]) - 1) / float(sample_rate)

        # A second, adaptive pause detector for noisy rooms. The old detector
        # used an absolute-ish threshold and often saw PC fan / air purifier
        # noise as continuous activity, so trailing_silence stayed near 0 even
        # after the user paused for a sentence. Here we measure short-window
        # RMS, estimate the noise floor, and detect when speech-level energy
        # stopped while background noise continues.
        speech_trailing_silence = trailing_silence
        speech_threshold = threshold
        noise_floor_rms = 0.0
        speech_high_rms = 0.0
        try:
            window = max(1, int(sample_rate * 0.06))  # 60 ms windows
            usable_size = (audio.size // window) * window
            if usable_size >= window * 4:
                windowed = audio[:usable_size].reshape(-1, window)
                win_rms = np.sqrt(np.mean(windowed * windowed, axis=1))
                if win_rms.size:
                    noise_floor_rms = float(np.percentile(win_rms, 20))
                    speech_high_rms = float(np.percentile(win_rms, 85))
                    median_rms = float(np.median(win_rms))
                    speech_threshold = max(
                        0.006,
                        min(0.075, max(noise_floor_rms * 2.1, median_rms * 1.25, speech_high_rms * 0.32)),
                    )
                    speech_windows = np.where(win_rms > speech_threshold)[0]
                    if speech_windows.size:
                        silent_windows = int(win_rms.size - int(speech_windows[-1]) - 1)
                        speech_trailing_silence = max(0.0, silent_windows * window / float(sample_rate))
                    else:
                        speech_trailing_silence = duration
        except Exception:
            speech_trailing_silence = trailing_silence

        pause_seconds = max(trailing_silence, speech_trailing_silence)
        return {
            "duration": duration,
            "rms": rms,
            "peak": peak,
            "peak_to_rms": peak_to_rms,
            "active_ratio": active_ratio,
            "trailing_silence": trailing_silence,
            "speech_trailing_silence": speech_trailing_silence,
            "pause_seconds": pause_seconds,
            "speech_threshold": speech_threshold,
            "noise_floor_rms": noise_floor_rms,
            "speech_high_rms": speech_high_rms,
        }

    def _is_probably_speech(self, frames: list[np.ndarray], sample_rate: int) -> bool:
        stats = self._stream_audio_stats(frames, sample_rate)
        return stats["duration"] >= 0.35 and stats["peak"] >= 0.010 and stats["rms"] >= 0.0025

    def _stream_quality(self, runtime_settings: RuntimeSettings) -> str:
        profile = runtime_settings.realtime_speed_profile
        if runtime_settings.realtime_fast_quality or profile == "Быстрее":
            return "Быстро"
        if profile == "Баланс":
            return "Точно"
        if self._cpu_realtime_path_expected(runtime_settings):
            return "Точно"
        return runtime_settings.recognition_quality

    def _normalize_stream_words(self, text: str) -> list[str]:
        cleaned = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", " ", text.lower())
        return [w for w in cleaned.split() if w]

    def _dedupe_stream_chunk(self, previous_text: str, new_text: str) -> str:
        """Remove repeated prefix from a stream chunk using token overlap.

        Voice-control commands are allowed to repeat. Without this exception,
        a command like "новая строка" can work once and then be swallowed as
        an already-seen duplicate later in the same dictation session.
        """
        new_text = re.sub(r"\s+", " ", new_text or "").strip()
        previous_text = re.sub(r"\s+", " ", previous_text or "").strip()
        if not new_text:
            return ""
        if voice_control_command_from_text(new_text):
            return new_text
        if not previous_text:
            return new_text
        prev_words = self._normalize_stream_words(previous_text)
        new_words = self._normalize_stream_words(new_text)
        if not prev_words or not new_words:
            return new_text
        max_overlap = min(14, len(prev_words), len(new_words))
        best = 0
        for size in range(max_overlap, 0, -1):
            if prev_words[-size:] == new_words[:size]:
                best = size
                break
        if best <= 0:
            normalized_prev = " ".join(prev_words)
            normalized_new = " ".join(new_words)
            if normalized_new and normalized_new in normalized_prev:
                return ""
            return new_text
        original_tokens = new_text.split()
        if best >= len(original_tokens):
            return ""
        return " ".join(original_tokens[best:]).strip()

    def _is_bad_stream_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized or len(normalized) < 2:
            return True
        punctuation_only = re.sub(r"\s+", "", normalized)
        # faster-whisper can hallucinate chunks like ".. .." or "..." during
        # silence/noise. They were cleaned into "..." / "....." and inserted
        # into the target field. Keep spoken punctuation commands as words
        # ("точка", "знак вопроса", "знак внимания") but reject punctuation-only chunks.
        if re.fullmatch(r"[\.。…]+", punctuation_only) or re.fullmatch(r"[\.,;:!?…\-—]+", punctuation_only):
            return True
        bad_phrases = [
            "спасибо за просмотр", "спасибо за внимание", "продолжение следует",
            "субтитры сделал", "субтитры создал", "субтитры создала", "субтитры создавал",
            "субтитры подготовил", "редактор субтитров", "подписывайтесь", "смотрите далее",
            "thank you for watching", "subtitles by", "captioned by", "dima torzok", "dimatorzok",
            "дима торжок", "диматорзок", "субтитры dima", "субтитры dimatorzok",
        ]
        if any(phrase in normalized for phrase in bad_phrases):
            return True
        words = normalized.split()
        if len(words) >= 4 and len(set(words)) <= 2:
            return True
        return False

    def _soften_open_stream_text(self, text: str) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()
        if not text:
            return ""
        # If a realtime chunk was cut mid-phrase, punctuation at the end is
        # usually Whisper/cleanup noise. The old code removed only one dot,
        # producing broken tails like "Сейчас.." and "с.?". Remove the whole
        # dangling punctuation run so the next chunk can continue the sentence.
        if not re.search(r"(?i)\b(?:т\.д|т\.п|и т\.д|и т\.п)\.$", text):
            text = re.sub(r"(?:\s*[.!?…]+)+\s*$", "", text).rstrip()
            text = re.sub(r"\s+[,;:]\s*$", "", text).rstrip()
        return text

    def _clean_stream_chunk_for_commit(self, raw_text: str, runtime_settings: RuntimeSettings, keep_sentence_end: bool = True) -> tuple[str, str]:
        raw_text = re.sub(r"\s+", " ", raw_text or "").strip()
        raw_had_trailing_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text))
        if self._is_bad_stream_text(raw_text):
            return "", ""
        clean_mode = "Чистый текст" if runtime_settings.mode != "Точно как сказано" else "Точно как сказано"
        cleaned = self.cleaner.clean(
            raw_text,
            clean_mode,
            runtime_settings.language,
            custom_terms=runtime_settings.custom_terms,
            deep_grammar=False,
        )
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned).strip()
        if self._is_bad_stream_text(cleaned):
            # Do not resurrect punctuation-only hallucinations by falling back
            # to raw_text. If both forms are unusable, skip the chunk.
            if self._is_bad_stream_text(raw_text):
                return "", ""
            cleaned = raw_text
        if raw_had_trailing_ellipsis or not keep_sentence_end:
            cleaned = self._soften_open_stream_text(cleaned)
        return raw_text, cleaned

    def _stream_has_sentence_end(self, text: str) -> bool:
        return bool(re.search(r"[.!?…][\"'»)\]]*$", (text or "").strip()))

    def _lowercase_continuation_start(self, text: str) -> str:
        if not text:
            return text
        proper_starts = {
            "ChatGPT", "OpenAI", "Telegram", "WhatsApp", "Gmail", "Google", "Python",
            "JavaScript", "TypeScript", "PowerShell", "Windows", "Whisper", "CUDA",
        }
        first_word = re.match(r"^[\"'«(]*([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_+-]*)", text)
        if first_word and first_word.group(1) in proper_starts:
            return text
        return re.sub(
            r"^([\"'«(]*)([А-ЯЁA-Z])([а-яёa-z])",
            lambda m: m.group(1) + m.group(2).lower() + m.group(3),
            text,
            count=1,
        )

    def _prepare_stream_chunk_for_paste(
        self,
        previous_text: str,
        chunk_text: str,
        commit_meta: Optional[dict[str, object]] = None,
        raw_text: str = "",
    ) -> str:
        chunk_text = re.sub(r"[ \t\r\f\v]+", " ", chunk_text or "").strip()
        if not chunk_text:
            return ""

        meta = commit_meta or {}
        sentence_pause = bool(meta.get("sentence_pause"))
        pause_seconds = float(meta.get("pause_seconds") or 0.0)
        whisper_sentence_end = bool(meta.get("whisper_sentence_end"))
        raw_text = raw_text or str(meta.get("raw_text") or "")

        # Preserve/add final punctuation when we have a real reason for it:
        # 1) the user made a clear pause in speech; 2) Whisper confidently ended
        # the phrase with .?! . This fixes logs where raw text had periods but
        # cleaned realtime text inserted every phrase without a dot.
        raw_end_match = re.search(r"([.!?])(?:[\"'»\)\]]*)\s*$", raw_text.strip())
        if raw_end_match and not re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text.strip()):
            whisper_sentence_end = True

        if sentence_pause and not self._stream_has_sentence_end(chunk_text):
            chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + "."
        elif whisper_sentence_end and not self._stream_has_sentence_end(chunk_text):
            end_char = raw_end_match.group(1) if raw_end_match else "."
            chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + end_char
        elif not sentence_pause and not whisper_sentence_end and meta.get("forced_commit"):
            # Forced max-duration chunks can be cut mid-sentence. Do not invent
            # a dot unless there was a pause or Whisper itself ended the phrase.
            chunk_text = self._soften_open_stream_text(chunk_text)

        if previous_text and not self._stream_has_sentence_end(previous_text):
            # If the previous inserted chunk did not end as a sentence, this is
            # a continuation unless the current chunk was explicitly punctuated
            # by a strong pause/Whisper.
            if not sentence_pause:
                chunk_text = self._lowercase_continuation_start(chunk_text)
        log_category(
            "streaming",
            "pause_punctuation_decision",
            chunk_preview=chunk_text[:160],
            pause_seconds=round(pause_seconds, 3),
            sentence_pause=sentence_pause,
            whisper_sentence_end=whisper_sentence_end,
            previous_had_sentence_end=self._stream_has_sentence_end(previous_text),
            forced_commit=bool(meta.get("forced_commit")),
        )
        return chunk_text

    def _get_missing_final_tail(self, already_inserted: str, final_text: str) -> str:
        """Return only the not-yet-inserted final tail after realtime paste."""
        already_words = self._normalize_stream_words(already_inserted)
        final_words = self._normalize_stream_words(final_text)
        if not final_words:
            return ""
        if not already_words:
            return final_text.strip()
        max_overlap = min(len(already_words), len(final_words), 28)
        best = 0
        for size in range(max_overlap, 1, -1):
            suffix = already_words[-size:]
            for start_pos in range(0, min(10, max(1, len(final_words) - size + 1))):
                if final_words[start_pos:start_pos + size] == suffix:
                    best = start_pos + size
                    break
            if best:
                break
        if best <= 0:
            return ""
        original_tokens = final_text.split()
        if best >= len(original_tokens):
            return ""
        return " ".join(original_tokens[best:]).strip()

    def _realtime_stream_worker(
        self,
        origin: str,
        mode: str,
        stop_event: threading.Event,
        session_id: int,
        runtime_settings: RuntimeSettings,
    ) -> None:
        """Stable phrase-based realtime recognition.

        This does not paste every tiny audio chunk. Short chunks are the main
        reason for wrong text, hallucinations and duplicated fragments in local
        Whisper. We accumulate audio and commit only after a pause or when the
        chunk becomes long enough.
        """
        configured_interval = runtime_settings.realtime_chunk_seconds
        profile = runtime_settings.realtime_speed_profile
        streaming_model_name = self._streaming_model_name(runtime_settings)
        if profile == "Быстрее":
            poll_interval = 0.18
            min_seconds = min(max(0.75, configured_interval * 0.35), 1.35)
            max_seconds = min(max(1.60, configured_interval * 0.75), 2.80)
            trailing_silence_required = 0.24
            sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_FAST
        elif profile == "Баланс":
            poll_interval = 0.26
            min_seconds = min(max(1.35, configured_interval * 0.55), 2.40)
            max_seconds = min(max(3.80, configured_interval * 1.35), 6.00)
            trailing_silence_required = 0.42
            sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_BALANCE
        else:
            # Noisy/distant microphones need longer phrase chunks. Logs showed
            # profile "Качество" with 4-second forced chunks and almost no
            # trailing silence; Whisper hallucinated subtitles/outros and those
            # chunks were discarded, so real words could be lost. In quality mode
            # wait longer and prefer a real pause before committing.
            if self._cpu_realtime_path_expected(runtime_settings):
                poll_interval = 0.32
                min_seconds = min(max(1.80, configured_interval * 0.60), 2.80)
                max_seconds = min(max(4.80, configured_interval * 1.25), 7.00)
                trailing_silence_required = 0.55
                sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY
            else:
                poll_interval = 0.24
                min_seconds = min(max(1.60, configured_interval * 0.55), 2.60)
                max_seconds = min(max(4.20, configured_interval * 1.15), 6.50)
                trailing_silence_required = 0.48
                sentence_end_pause_seconds = STREAM_SENTENCE_PAUSE_SECONDS_QUALITY
        streaming_vad_filter = bool(runtime_settings.use_vad_filter or profile in {"Баланс", "Качество"})
        bad_retry_after_frame_index = 0
        bad_filtered_streak = 0
        max_bad_hold_seconds = max(max_seconds + 3.0, max_seconds * 1.75)
        committed_raw_context = ""
        committed_clean_context = ""
        last_frame_index = self.stream_last_frame_index
        log_info(
            "Realtime timing configured",
            session_id=session_id,
            profile=profile,
            model=streaming_model_name,
            poll_interval=poll_interval,
            min_seconds=round(min_seconds, 2),
            max_seconds=round(max_seconds, 2),
            trailing_silence_required=round(trailing_silence_required, 2),
            sentence_end_pause_seconds=round(sentence_end_pause_seconds, 2),
            streaming_vad_filter=streaming_vad_filter,
            max_bad_hold_seconds=round(max_bad_hold_seconds, 2),
        )

        def reset_context_if_requested() -> None:
            nonlocal committed_raw_context, committed_clean_context, last_frame_index
            if self.stream_context_reset_event.is_set():
                committed_raw_context = ""
                committed_clean_context = ""
                self.stream_context_reset_event.clear()
                log_info("Realtime dictation context reset", session_id=session_id)

        def commit_frames(frames: list[np.ndarray], new_index: int, sample_rate: int, is_final: bool = False) -> None:
            nonlocal committed_raw_context, committed_clean_context, last_frame_index, bad_retry_after_frame_index, bad_filtered_streak
            reset_context_if_requested()
            if not frames:
                last_frame_index = new_index
                return

            stats = self._stream_audio_stats(frames, sample_rate)
            duration = stats["duration"]
            has_pause = False
            forced_commit = False
            pause_seconds = float(stats.get("pause_seconds", stats.get("trailing_silence", 0.0)))
            sentence_pause = False
            if is_final:
                if duration < 0.20 or stats["peak"] < 0.008 or stats["rms"] < 0.0018:
                    last_frame_index = new_index
                    return
                has_pause = True
                sentence_pause = True
            else:
                if duration < min_seconds:
                    return
                if not self._is_probably_speech(frames, sample_rate):
                    last_frame_index = new_index
                    return
                pause_seconds = float(stats.get("pause_seconds", stats.get("trailing_silence", 0.0)))
                sentence_pause = pause_seconds >= sentence_end_pause_seconds
                has_pause = pause_seconds >= trailing_silence_required
                forced_commit = duration >= max_seconds
                if not has_pause and not forced_commit:
                    return
                log_info(
                    "Realtime commit triggered",
                    session_id=session_id,
                    duration=round(duration, 3),
                    trailing_silence=round(stats["trailing_silence"], 3),
                    speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                    pause_seconds=round(pause_seconds, 3),
                    sentence_pause=sentence_pause,
                    sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                    forced_commit=forced_commit,
                    has_pause=has_pause,
                    frame_index=new_index,
                    peak=round(stats.get("peak", 0.0), 5),
                    rms=round(stats.get("rms", 0.0), 5),
                    peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                    active_ratio=round(stats.get("active_ratio", 0.0), 3),
                    streaming_vad_filter=streaming_vad_filter,
                )

            # Never freeze realtime input after a bad/noisy chunk.
            # Older builds tried to retain audio after Whisper returned an empty
            # or hallucinated result, but mixed sample counts with callback-frame
            # indexes. That made retry_after_frame_index enormous, so after one
            # inserted sentence the worker kept delaying the same growing chunk
            # forever and no more text was pasted. Keep dictation continuous:
            # bad chunks are logged and skipped below, then the stream advances.

            wav_path = self.recorder.frames_to_wav(frames, prefix="stream_stable_chunk")
            if wav_path is None:
                last_frame_index = new_index
                return
            try:
                transcribed_raw = self.transcriber.transcribe(
                    wav_path,
                    runtime_settings.language,
                    model_name=streaming_model_name,
                    quality=self._stream_quality(runtime_settings),
                    custom_terms=runtime_settings.custom_terms,
                    use_vad_filter=streaming_vad_filter,
                    context_text=committed_raw_context[-500:],
                    device=runtime_settings.inference_device,
                    compute_type=runtime_settings.compute_type,
                    streaming=True,
                )
                raw = transcribed_raw
                raw_text_for_end = (raw or "").strip()
                raw_had_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text_for_end))
                whisper_sentence_end = bool(re.search(r"[.!?][\"'»\)\]]*$", raw_text_for_end)) and not raw_had_ellipsis
                keep_sentence_end = bool(is_final or sentence_pause or whisper_sentence_end)
                raw, cleaned = self._clean_stream_chunk_for_commit(
                    raw,
                    runtime_settings,
                    keep_sentence_end=keep_sentence_end,
                )
                if not raw and not cleaned:
                    bad_filtered_streak += 1
                    # Do not retain bad chunks for retry. Retaining used to
                    # make last_frame_index stay old; after one empty/noisy
                    # transcription the next chunk grew to minutes and realtime
                    # insertion effectively stopped. A filtered chunk means:
                    # skip it, advance the stream, and keep listening.
                    retain_audio_for_retry = False
                    bad_retry_after_frame_index = 0
                    log_dictation_text(
                        "stream_filtered",
                        session_id=session_id,
                        origin=origin,
                        mode=mode,
                        raw_text=transcribed_raw,
                        reason="bad_or_empty_stream_text",
                        model=streaming_model_name,
                        language=runtime_settings.language,
                        device=runtime_settings.inference_device,
                        compute_type=runtime_settings.compute_type,
                        speed_profile=runtime_settings.realtime_speed_profile,
                        is_final=is_final,
                        has_pause=has_pause,
                        forced_commit=forced_commit,
                        audio_duration=round(duration, 3),
                        trailing_silence=round(stats["trailing_silence"], 3),
                        speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                        pause_seconds=round(pause_seconds, 3),
                        sentence_pause=bool(sentence_pause),
                        sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                        whisper_sentence_end=bool(whisper_sentence_end),
                        keep_sentence_end=bool(keep_sentence_end),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                        streaming_vad_filter=streaming_vad_filter,
                        bad_filtered_streak=bad_filtered_streak,
                        retain_audio_for_retry=retain_audio_for_retry,
                        retry_after_frame_index=bad_retry_after_frame_index if retain_audio_for_retry else None,
                    )
                    log_info(
                        "Realtime chunk filtered as hallucination/noise",
                        session_id=session_id,
                        raw_text=transcribed_raw,
                        retain_audio_for_retry=retain_audio_for_retry,
                        bad_filtered_streak=bad_filtered_streak,
                        duration=round(duration, 3),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                    )
                    if retain_audio_for_retry:
                        return
                    last_frame_index = new_index
                    return
                raw_delta = self._dedupe_stream_chunk(committed_raw_context, raw)
                clean_delta = self._dedupe_stream_chunk(committed_clean_context, cleaned)
                if raw_delta or clean_delta:
                    if not raw_delta:
                        raw_delta = clean_delta
                    if not clean_delta:
                        clean_delta = raw_delta
                    raw_context_delta, raw_context_command = split_trailing_voice_control_command(raw_delta)
                    clean_context_delta, clean_context_command = split_trailing_voice_control_command(clean_delta)
                    if raw_context_command is None:
                        raw_context_delta = raw_delta
                    if clean_context_command is None:
                        clean_context_delta = clean_delta
                    if raw_context_delta:
                        committed_raw_context = (committed_raw_context + " " + raw_context_delta).strip()
                    if clean_context_delta:
                        committed_clean_context = (committed_clean_context + " " + clean_context_delta).strip()
                    commit_meta = {
                        "pause_seconds": pause_seconds,
                        "trailing_silence": float(stats.get("trailing_silence", 0.0)),
                        "speech_trailing_silence": float(stats.get("speech_trailing_silence", 0.0)),
                        "sentence_pause": bool(sentence_pause),
                        "sentence_end_pause_seconds": float(sentence_end_pause_seconds),
                        "has_pause": bool(has_pause),
                        "forced_commit": bool(forced_commit),
                        "whisper_sentence_end": bool(whisper_sentence_end),
                        "keep_sentence_end": bool(keep_sentence_end),
                        "raw_text": raw_delta,
                    }
                    log_dictation_text(
                        "stream_chunk",
                        session_id=session_id,
                        origin=origin,
                        mode=mode,
                        raw_text=raw_delta,
                        cleaned_text=clean_delta,
                        full_clean_context=committed_clean_context[-1200:],
                        model=streaming_model_name,
                        language=runtime_settings.language,
                        device=runtime_settings.inference_device,
                        compute_type=runtime_settings.compute_type,
                        speed_profile=runtime_settings.realtime_speed_profile,
                        is_final=is_final,
                        has_pause=has_pause,
                        forced_commit=forced_commit,
                        audio_duration=round(duration, 3),
                        trailing_silence=round(stats["trailing_silence"], 3),
                        speech_trailing_silence=round(stats.get("speech_trailing_silence", 0.0), 3),
                        pause_seconds=round(pause_seconds, 3),
                        sentence_pause=bool(sentence_pause),
                        sentence_end_pause_seconds=round(sentence_end_pause_seconds, 3),
                        whisper_sentence_end=bool(whisper_sentence_end),
                        keep_sentence_end=bool(keep_sentence_end),
                        peak=round(stats.get("peak", 0.0), 5),
                        rms=round(stats.get("rms", 0.0), 5),
                        peak_to_rms=round(stats.get("peak_to_rms", 0.0), 3),
                        active_ratio=round(stats.get("active_ratio", 0.0), 3),
                        streaming_vad_filter=streaming_vad_filter,
                    )
                    bad_filtered_streak = 0
                    bad_retry_after_frame_index = 0
                    self.worker_queue.put(("stream_result", (session_id, raw_delta, clean_delta, origin, mode, is_final, commit_meta)))
                last_frame_index = new_index
            finally:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass

        while not stop_event.wait(poll_interval):
            if not self.recorder.is_recording:
                break
            try:
                reset_context_if_requested()
                frames, new_index, sample_rate = self.recorder.get_frames_since(last_frame_index)
                commit_frames(frames, new_index, sample_rate, is_final=False)
            except BaseException as exc:
                log_exception("Realtime streaming chunk failed", exc, session_id=session_id, origin=origin, mode=mode)
                try:
                    self.worker_queue.put(("stream_warning", (session_id, exc)))
                except Exception:
                    pass
                time.sleep(0.8)

        if stop_event.is_set() and not STREAM_FINAL_CHUNK_ON_STOP:
            log_info(
                "Realtime final chunk skipped to keep hotkey responsive",
                session_id=session_id,
                origin=origin,
                mode=mode,
            )
            log_dictation_text(
                "stream_final_skipped",
                session_id=session_id,
                origin=origin,
                mode=mode,
                reason="stop_should_not_block_next_hotkey",
            )
            log_info("Realtime streaming worker finished", session_id=session_id, origin=origin, mode=mode)
            return

        try:
            frames, new_index, sample_rate = self.recorder.get_frames_since(last_frame_index)
            commit_frames(frames, new_index, sample_rate, is_final=True)
        except BaseException as exc:
            log_exception("Realtime final chunk failed", exc, session_id=session_id, origin=origin, mode=mode)
            try:
                self.worker_queue.put(("stream_warning", (session_id, exc)))
            except Exception:
                pass
        log_info("Realtime streaming worker finished", session_id=session_id, origin=origin, mode=mode)

    def _append_stream_text(self, widget: tk.Text, text: str) -> None:
        text = re.sub(r"[ \t\r\f\v]+", " ", (text or "").strip())
        if not text:
            return
        current = widget.get("1.0", tk.END).strip()
        if current:
            separator = "" if text.startswith("\n") else " "
            widget.insert(tk.END, separator + text)
        else:
            widget.insert(tk.END, text)
        widget.see(tk.END)

    def _update_timer(self) -> None:
        if not self.recorder.is_recording or self.record_started_at is None:
            return
        elapsed = int(time.time() - self.record_started_at)
        minutes, seconds = divmod(elapsed, 60)
        current = f"{minutes:02d}:{seconds:02d}"
        self.timer_var.set(current)
        # Update persistent recording notification once per second without creating a new window.
        if elapsed % 1 == 0:
            self._show_recording_notification(force_recreate=False)
        self.timer_job = self.root.after(1000, self._update_timer)

    def _process_audio_worker(self, wav_path: Path, origin: str, runtime_settings: RuntimeSettings) -> None:
        try:
            raw = self.transcriber.transcribe(
                wav_path,
                runtime_settings.language,
                model_name=runtime_settings.whisper_model,
                quality=runtime_settings.recognition_quality,
                custom_terms=runtime_settings.custom_terms,
                use_vad_filter=runtime_settings.use_vad_filter,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                streaming=False,
            )
            cleaned = self.cleaner.clean(
                raw,
                runtime_settings.mode,
                runtime_settings.language,
                custom_terms=runtime_settings.custom_terms,
                deep_grammar=runtime_settings.deep_grammar,
            )
            log_dictation_text(
                "final_result",
                session_id=getattr(self, "recording_session_id", None),
                origin=origin,
                mode=runtime_settings.mode,
                raw_text=raw,
                cleaned_text=cleaned,
                model=runtime_settings.whisper_model,
                language=runtime_settings.language,
                device=runtime_settings.inference_device,
                compute_type=runtime_settings.compute_type,
                deep_grammar=runtime_settings.deep_grammar,
            )
            if self.privacy_var.get():
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass
            self.worker_queue.put(("result", (raw, cleaned, origin)))
        except BaseException as exc:
            log_exception("Audio processing worker crashed", exc, wav_path=wav_path, origin=origin)
            self.worker_queue.put(("error", exc))

    def _split_stream_voice_command(self, raw: str, cleaned: str) -> tuple[str, str, Optional[dict[str, object]]]:
        raw_before, raw_command = split_trailing_voice_control_command(raw)
        cleaned_before, cleaned_command = split_trailing_voice_control_command(cleaned)
        command = cleaned_command or raw_command
        if command is None:
            return raw, cleaned, None
        if raw_command is None and normalize_voice_command_text(raw) == normalize_voice_command_text(cleaned):
            raw_before = cleaned_before
        if cleaned_command is None:
            cleaned_before = raw_before
        return raw_before.strip(), cleaned_before.strip(), command

    def _reset_stream_message_state(self, session_id: Optional[int], reason: str) -> None:
        self.stream_inserted_text = ""
        self.stream_context_reset_event.set()
        log_info("Realtime message state reset", session_id=session_id, reason=reason)

    def _handle_stream_text_piece(
        self,
        *,
        session_id: int,
        raw: str,
        cleaned: str,
        origin: str,
        stream_mode: str,
        is_final: bool,
        commit_meta: Optional[dict[str, object]] = None,
    ) -> None:
        raw = (raw or "").strip()
        cleaned = (cleaned or "").strip()
        if not raw and not cleaned:
            return
        if not raw:
            raw = cleaned
        if not cleaned:
            cleaned = raw

        self._append_stream_text(self.raw_text, raw)
        self._append_stream_text(self.clean_text, cleaned)
        self.status_var.set("Готово" if is_final and not self.recorder.is_recording else "Стриминг...")

        if stream_mode == "Вставлять фрагментами" and origin == "hotkey":
            chunk_text = cleaned if self.insert_edited_text_var.get() else raw
            chunk_text = self._dedupe_stream_chunk(self.stream_inserted_text, chunk_text).strip()
            chunk_text = self._prepare_stream_chunk_for_paste(
                self.stream_inserted_text,
                chunk_text,
                commit_meta=commit_meta,
                raw_text=raw,
            )
            if chunk_text:
                ok = self.paste_text_to_current_target(chunk_text + " ", show_messages=False)
                log_dictation_text(
                    "stream_insert",
                    session_id=session_id,
                    origin=origin,
                    mode=stream_mode,
                    raw_text=raw,
                    cleaned_text=cleaned,
                    inserted_text=chunk_text,
                    is_final=is_final,
                    paste_ok=ok,
                    commit_meta=commit_meta or {},
                )
                if ok:
                    self.stream_inserted_any = True
                    self.stream_inserted_text = (self.stream_inserted_text + " " + chunk_text).strip()
                    if self.recorder.is_recording:
                        # Do not replace the persistent "Идёт запись" toast with
                        # short success popups for every inserted chunk. Logs
                        # showed recording and typing were working, but the user
                        # thought repeat hotkey was broken because the visible
                        # toast was not the recording one.
                        self._show_recording_notification(force_recreate=False)
                    else:
                        message = "✅ Финальный фрагмент вставлен" if is_final else "⚡ Стабильный фрагмент вставлен"
                        self.notify(message, kind="success", duration_ms=1200 if is_final else 900)
                else:
                    self.notify("⚠ Фрагмент распознан, но не вставился", kind="warning", duration_ms=1600)

    def _execute_voice_control_command(
        self,
        command: dict[str, object],
        *,
        session_id: Optional[int],
        raw_text: str,
        cleaned_text: str,
        origin: str,
    ) -> bool:
        kind = str(command.get("kind", ""))
        value = command.get("value")
        label = str(command.get("label", command.get("phrase", "voice command")))
        phrase = str(command.get("phrase", ""))
        ok = False

        if kind == "text":
            ok = self.paste_text_to_current_target(str(value or ""), show_messages=False)
        else:
            current_target = get_paste_target()
            if pyautogui is not None:
                try:
                    # Voice control hotkeys/keys should also act in the current
                    # field under the cursor, not in the window where recording started.
                    time.sleep(0.025)

                    def run_action(action_kind: str, action_value: object) -> None:
                        if action_kind == "key":
                            pyautogui.press(str(action_value))
                        elif action_kind == "hotkey" and isinstance(action_value, (tuple, list)):
                            pyautogui.hotkey(*(str(part) for part in action_value))
                        else:
                            raise ValueError(f"Unsupported voice action: {action_kind}")

                    if kind in {"key", "hotkey"}:
                        run_action(kind, value)
                        ok = True
                    elif kind == "sequence" and isinstance(value, (tuple, list)):
                        for step in value:
                            if not isinstance(step, (tuple, list)) or len(step) != 2:
                                raise ValueError(f"Bad voice action step: {step!r}")
                            run_action(str(step[0]), step[1])
                            time.sleep(0.025)
                        ok = True
                    log_info(
                        "Voice control key action sent to current target",
                        command=phrase,
                        kind=kind,
                        current_foreground_hwnd=current_target.foreground_hwnd,
                        current_focus_hwnd=current_target.focus_hwnd,
                    )
                except Exception as exc:
                    log_exception("Voice control key action failed", exc, command=phrase, kind=kind, value=value)
            elif pyautogui is None:
                log_warning("Voice control key action skipped because pyautogui is not installed", command=phrase, kind=kind)

        log_info(
            "Voice control command handled",
            command=phrase,
            action=label,
            kind=kind,
            ok=ok,
            session_id=session_id,
        )
        log_dictation_text(
            "voice_command",
            session_id=session_id,
            origin=origin,
            raw_text=raw_text,
            cleaned_text=cleaned_text,
            command=phrase,
            action=label,
            ok=ok,
        )
        if ok:
            self.status_var.set(f"Команда: {label}")
            self.notify(f"🎛 Команда: {label}", kind="success", duration_ms=900)
            if command.get("reset_message_context"):
                self._reset_stream_message_state(session_id, label)
        else:
            self.notify(f"⚠ Команда распознана, но не выполнена: {label}", kind="warning", duration_ms=1800)
        return ok

    def _handle_worker_message(self, msg_type: str, payload: object) -> None:
        if msg_type == "global_hotkey_pressed":
            try:
                hotkey, target, generation = payload  # type: ignore[misc]
            except Exception:
                hotkey, target, generation = self.hotkey_var.get(), get_paste_target(), None
            self.log_hotkey_trace(
                "global_hotkey_dequeued_on_main_thread",
                hotkey=hotkey,
                generation=generation,
                target=self._hotkey_target_snapshot(target),
            )
            self._handle_global_hotkey(str(hotkey), target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_toggle_recording":
            try:
                source, target = payload  # type: ignore[misc]
            except Exception:
                source, target = "unknown", get_paste_target()
            self.log_state(
                "recording",
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self.log_hotkey_trace(
                "external_toggle_dequeued",
                source=source,
                target=self._hotkey_target_snapshot(target if isinstance(target, PasteTarget) else None),
            )
            self._repair_idle_recording_state(f"external_toggle:{source}")
            self.toggle_recording("hotkey", target if isinstance(target, PasteTarget) else None)
            return

        if msg_type == "external_show_window":
            log_info("External show-window request", source=payload)
            self.show_main_window()
            return

        if msg_type == "external_hide_window":
            log_info("External hide-window request", source=payload)
            self.hide_main_window(show_notification=True)
            return

        if msg_type == "external_exit":
            log_info("External exit request", source=payload)
            self.exit_application()
            return

        if msg_type == "stream_result":
            commit_meta: dict[str, object] = {}
            try:
                if isinstance(payload, (tuple, list)) and len(payload) >= 7:
                    session_id, raw, cleaned, origin, stream_mode, is_final, commit_meta = payload[:7]  # type: ignore[misc]
                else:
                    session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
            except Exception:
                session_id, raw, cleaned, origin, stream_mode, is_final = payload  # type: ignore[misc]
                commit_meta = {}
            if session_id != self.recording_session_id:
                return
            if not self.recorder.is_recording and not self.finalizing_recording:
                log_info(
                    "Late realtime stream result ignored after immediate stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                    commit_meta=commit_meta if isinstance(commit_meta, dict) else {},
                )
                self.log_state(
                    "streaming",
                    "late_stream_result_ignored_after_stop",
                    session_id=session_id,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                )
                if origin == "hotkey":
                    self.log_hotkey_trace(
                        "late_stream_result_ignored_after_stop",
                        session_id=session_id,
                        stream_mode=stream_mode,
                        is_final=is_final,
                    )
                return
            original_raw = raw
            original_cleaned = cleaned
            raw, cleaned, command = self._split_stream_voice_command(raw, cleaned)
            if raw or cleaned:
                self._handle_stream_text_piece(
                    session_id=session_id,
                    raw=raw,
                    cleaned=cleaned,
                    origin=origin,
                    stream_mode=stream_mode,
                    is_final=is_final,
                )
            if command and stream_mode == "Вставлять фрагментами" and origin == "hotkey":
                self._execute_voice_control_command(
                    command,
                    session_id=session_id,
                    raw_text=original_raw,
                    cleaned_text=original_cleaned,
                    origin=origin,
                )
                return

        elif msg_type == "result":
            raw, cleaned, origin = payload  # type: ignore[misc]
            self.raw_text.delete("1.0", tk.END)
            self.raw_text.insert(tk.END, raw)
            self.clean_text.delete("1.0", tk.END)
            self.clean_text.insert(tk.END, cleaned)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            self.record_btn.config(text="● Начать запись")
            self.last_result_ready = True

            streaming_insert_used = (
                self.realtime_streaming_mode_var.get() == "Вставлять фрагментами"
                and self.stream_inserted_any
            )
            if origin == "hotkey" and self.auto_paste_hotkey_var.get() and not streaming_insert_used:
                ok = self.paste_result_to_saved_target(show_messages=False)
                inserted_type = "отредактированный" if self.insert_edited_text_var.get() else "распознанный без редактирования"
                if ok:
                    self.status_var.set("Вставлено")
                    self.notify(f"✅ Текст вставлен\nТип: {inserted_type}", kind="success", duration_ms=2600)
                else:
                    self.status_var.set("Ошибка вставки")
                    self.notify("⚠ Текст распознан, но не вставился\nОн скопирован в буфер обмена — нажми Ctrl+V", kind="warning", duration_ms=4500)
            elif streaming_insert_used:
                final_text = cleaned if self.insert_edited_text_var.get() else raw
                tail = self._get_missing_final_tail(self.stream_inserted_text, final_text)
                if tail:
                    ok = self.paste_text_to_current_target(" " + tail, show_messages=False)
                    if ok:
                        self.stream_inserted_text = (self.stream_inserted_text + " " + tail).strip()
                        self.notify(
                            "✅ Стриминг завершён\nДобавлен финальный хвост текста",
                            kind="success",
                            duration_ms=3200,
                        )
                    else:
                        self.notify(
                            "✅ Стриминг завершён\nФинальная версия готова в окне; при необходимости скопируй её вручную",
                            kind="warning",
                            duration_ms=4200,
                        )
                else:
                    self.notify(
                        "✅ Стриминг завершён\nФразы уже вставлены, финальная версия готова в окне",
                        kind="success",
                        duration_ms=3200,
                    )
            else:
                self.notify("✅ Текст распознан и готов", kind="success", duration_ms=2400)

        elif msg_type == "stream_warning":
            session_id, _exc = payload  # type: ignore[misc]
            if session_id != self.recording_session_id:
                return
            # Non-fatal streaming failure. Most often this means CUDA was not ready;
            # app should stay open and either fallback to CPU or keep recording.
            self.status_var.set("Стриминг: предупреждение")
            self.notify(
                "⚠ Стриминг дал ошибку, программа не закрыта\nЕсли выбрана CUDA — попробуй auto или cpu, либо установи CUDA/cuDNN",
                kind="warning",
                duration_ms=4200,
            )

        elif msg_type in {"stream_finished", "stream_finish_timeout"}:
            session_id = int(payload)
            self.log_state("worker_queue", msg_type, session_id=session_id)
            self.log_hotkey_trace("worker_stream_finish_message", message_type=msg_type, payload_session_id=session_id)
            if session_id != self.recording_session_id:
                self.log_state(
                    "worker_queue",
                    "stale_stream_finish_ignored",
                    message_type=msg_type,
                    payload_session_id=session_id,
                    current_session_id=self.recording_session_id,
                )
                return
            timed_out = msg_type == "stream_finish_timeout"
            self.recorder.discard_frames()
            self.streaming_thread = None
            self.finalizing_recording = False
            self.last_wav_path = None
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.status_var.set("Готово")
            self.timer_var.set("00:00")
            log_info("Recording finalization finished", session_id=session_id, timed_out=timed_out)
            self.log_state("recording", "finalization_finished", session_id=session_id, timed_out=timed_out)
            self.log_hotkey_trace("finalization_finished", session_id=session_id, timed_out=timed_out)
            if timed_out:
                self.notify(
                    "⚠ Финальный фрагмент слишком долго обрабатывался\nГорячая клавиша снова доступна",
                    kind="warning",
                    duration_ms=2600,
                )
            self._start_pending_hotkey_recording()

        elif msg_type == "error":
            self.status_var.set("Ошибка")
            self.finalizing_recording = False
            self.record_btn.config(text="● Начать запись", state=tk.NORMAL)
            self.notify("⚠ Ошибка распознавания текста", kind="error", duration_ms=4000)
            self._show_error("Ошибка обработки", payload)  # type: ignore[arg-type]

    def _start_pending_hotkey_recording(self) -> None:
        if not self.pending_hotkey_start_requested:
            self.log_state("recording", "no_pending_hotkey_start")
            self.log_hotkey_trace("no_pending_hotkey_start_after_finalization")
            return
        target = self.pending_hotkey_start_target
        self.pending_hotkey_start_requested = False
        self.pending_hotkey_start_target = None
        if self.recorder.is_recording or self.finalizing_recording:
            self.log_state("recording", "pending_hotkey_start_skipped_busy")
            return
        log_info("Starting queued hotkey recording", previous_session_id=self.recording_session_id)
        self.log_state("recording", "pending_hotkey_start_launching", previous_session_id=self.recording_session_id)
        self.log_hotkey_trace(
            "pending_hotkey_start_launching",
            previous_session_id=self.recording_session_id,
            target=self._hotkey_target_snapshot(target),
        )
        self.hotkey_ignore_until = max(
            self.hotkey_ignore_until,
            time.monotonic() + HOTKEY_START_GUARD_SECONDS,
        )
        self.root.after(50, lambda target=target: self.start_recording("hotkey", target))

    def _drain_worker_queue(self) -> None:
        try:
            while True:
                msg_type, payload = self.worker_queue.get_nowait()
                if msg_type in {
                    "stream_finished",
                    "stream_finish_timeout",
                    "error",
                    "global_hotkey_pressed",
                    "external_toggle_recording",
                    "external_show_window",
                    "external_hide_window",
                    "external_exit",
                }:
                    self.log_hotkey_trace("worker_queue_drained_message", message_type=msg_type, payload=str(payload)[:500])
                self._handle_worker_message(msg_type, payload)
        except queue.Empty:
            pass

    def _poll_recording_state_watchdog(self) -> None:
        try:
            self._repair_idle_recording_state("watchdog")
        except Exception as exc:
            log_exception("Recording state watchdog failed", exc)
        try:
            self.root.after(1000, self._poll_recording_state_watchdog)
        except Exception:
            pass

    def _poll_worker_queue(self) -> None:
        self._drain_worker_queue()
        self.root.after(100, self._poll_worker_queue)
