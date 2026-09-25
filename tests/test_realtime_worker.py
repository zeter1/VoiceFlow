from __future__ import annotations

from pathlib import Path
import queue
import unittest

from voiceflow_app.app.realtime_worker import RealtimeWorkerConfig, RealtimeWorkerEngine
from voiceflow_app.app.session_controller import SessionTranscript
from voiceflow_app.core.realtime_policy import build_realtime_timing
from voiceflow_app.worker_messages import (
    StreamResultPayload,
    StreamWarningPayload,
    WorkerMessageKind,
    coerce_worker_message,
)


def good_stats() -> dict[str, float]:
    return {
        "duration": 1.0,
        "rms": 0.05,
        "peak": 0.20,
        "peak_to_rms": 4.0,
        "active_ratio": 0.5,
        "trailing_silence": 0.30,
        "speech_trailing_silence": 0.30,
        "pause_seconds": 0.30,
        "speech_threshold": 0.02,
        "noise_floor_rms": 0.005,
        "speech_high_rms": 0.08,
    }


def noise_stats() -> dict[str, float]:
    result = good_stats()
    result.update({"peak": 0.005, "rms": 0.001})
    return result


class ScriptedStopEvent:
    def __init__(self, waits):
        self.waits = list(waits)
        self._set = False

    def wait(self, _timeout):
        value = bool(self.waits.pop(0)) if self.waits else True
        if value:
            self._set = True
        return value

    def is_set(self):
        return self._set


class FakeResetEvent:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value

    def clear(self):
        self.value = False

    def set(self):
        self.value = True


class FakeRecorder:
    device = None
    sample_rate = 16000
    channels = 1

    def __init__(self, batches):
        self.is_recording = True
        self.batches = list(batches)
        self.requested_indices = []

    def get_frames_since(self, frame_index):
        self.requested_indices.append(frame_index)
        if not self.batches:
            return [], frame_index, self.sample_rate
        return self.batches.pop(0)


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def transcribe_frames(self, frames, request, *, prefix="stream_chunk", session_id=None):
        self.calls.append((list(frames), request, prefix, session_id))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SessionTranscript(
            session_id=int(session_id or 0),
            wav_path=Path("missing-test-worker.wav"),
            raw_text=str(outcome),
            cleaned_text=str(outcome),
        )


class ScriptedStats:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, _frames, _sample_rate):
        return self.values.pop(0)


class RealtimeWorkerEngineTests(unittest.TestCase):
    def make_engine(self, *, batches, outcomes, stats, final_chunk_on_stop=False):
        recorder = FakeRecorder(batches)
        session = FakeSession(outcomes)
        messages = queue.Queue()
        reset = FakeResetEvent()
        timing = build_realtime_timing(
            "Быстрее",
            2,
            use_vad_filter=False,
            cpu_path_expected=True,
        )

        def clean_chunk(raw, _keep_sentence_end):
            if raw == "FILTER":
                return "", ""
            return raw, raw

        def dedupe(previous, new):
            if previous and new in previous:
                return ""
            return new

        engine = RealtimeWorkerEngine(
            recorder=recorder,
            session_controller=session,
            message_queue=messages,
            config=RealtimeWorkerConfig(
                session_id=7,
                origin="hotkey",
                mode="Вставлять фрагментами",
                language="ru",
                model_name="small",
                quality="Быстро",
                custom_terms="",
                inference_device="cpu",
                compute_type="int8",
                speed_profile="Быстрее",
                timing=timing,
                final_chunk_on_stop=final_chunk_on_stop,
            ),
            start_frame_index=0,
            context_reset_event=reset,
            clean_chunk=clean_chunk,
            dedupe_chunk=dedupe,
            audio_stats=ScriptedStats(stats),
            sleep=lambda _seconds: None,
        )
        return engine, recorder, session, messages, reset

    def test_speech_pause_emits_typed_result_and_advances_cursor(self):
        engine, recorder, session, messages, _reset = self.make_engine(
            batches=[([object()], 4, 16000)],
            outcomes=["Привет"],
            stats=[good_stats()],
        )
        snapshot = engine.run(ScriptedStopEvent([False, True]))
        message = coerce_worker_message(messages.get_nowait())
        self.assertEqual(message.kind, WorkerMessageKind.STREAM_RESULT)
        self.assertIsInstance(message.payload, StreamResultPayload)
        self.assertEqual(message.payload.raw, "Привет")
        self.assertEqual(message.payload.session_id, 7)
        self.assertEqual(snapshot.last_frame_index, 4)
        self.assertEqual(recorder.requested_indices, [0])
        self.assertEqual(len(session.calls), 1)

    def test_noise_advances_without_transcription_then_speech_continues(self):
        engine, recorder, session, messages, _reset = self.make_engine(
            batches=[([object()], 2, 16000), ([object()], 5, 16000)],
            outcomes=["После шума"],
            stats=[noise_stats(), good_stats()],
        )
        snapshot = engine.run(ScriptedStopEvent([False, False, True]))
        self.assertEqual(recorder.requested_indices, [0, 2])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(snapshot.last_frame_index, 5)
        self.assertEqual(coerce_worker_message(messages.get_nowait()).kind, WorkerMessageKind.STREAM_RESULT)

    def test_filtered_chunk_does_not_freeze_frame_cursor(self):
        engine, recorder, session, messages, _reset = self.make_engine(
            batches=[([object()], 3, 16000), ([object()], 6, 16000)],
            outcomes=["FILTER", "Работа продолжается"],
            stats=[good_stats(), good_stats()],
        )
        snapshot = engine.run(ScriptedStopEvent([False, False, True]))
        self.assertEqual(recorder.requested_indices, [0, 3])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(snapshot.last_frame_index, 6)
        result = coerce_worker_message(messages.get_nowait())
        self.assertEqual(result.kind, WorkerMessageKind.STREAM_RESULT)
        self.assertEqual(result.payload.raw, "Работа продолжается")

    def test_transcription_error_keeps_original_exception_and_worker_recovers(self):
        original = RuntimeError("transcription failed")
        engine, recorder, session, messages, _reset = self.make_engine(
            batches=[([object()], 3, 16000), ([object()], 6, 16000)],
            outcomes=[original, "После ошибки"],
            stats=[good_stats(), good_stats()],
        )
        snapshot = engine.run(ScriptedStopEvent([False, False, True]))
        warning = coerce_worker_message(messages.get_nowait())
        result = coerce_worker_message(messages.get_nowait())
        self.assertEqual(warning.kind, WorkerMessageKind.STREAM_WARNING)
        self.assertIsInstance(warning.payload, StreamWarningPayload)
        self.assertIs(warning.payload.error, original)
        self.assertEqual(result.kind, WorkerMessageKind.STREAM_RESULT)
        self.assertEqual(recorder.requested_indices, [0, 0])
        self.assertEqual(snapshot.last_frame_index, 6)

    def test_stop_cancellation_skips_final_transcription(self):
        engine, recorder, session, messages, _reset = self.make_engine(
            batches=[([object()], 4, 16000)],
            outcomes=["Не должно быть вызвано"],
            stats=[good_stats()],
            final_chunk_on_stop=False,
        )
        snapshot = engine.run(ScriptedStopEvent([True]))
        self.assertEqual(recorder.requested_indices, [])
        self.assertEqual(session.calls, [])
        self.assertTrue(messages.empty())
        self.assertEqual(snapshot.last_frame_index, 0)

    def test_context_reset_removes_previous_prompt_before_next_transcription(self):
        engine, _recorder, session, _messages, reset = self.make_engine(
            batches=[],
            outcomes=["Первый", "Второй"],
            stats=[good_stats(), good_stats()],
        )
        engine.process_frames([object()], 2, 16000)
        self.assertEqual(session.calls[0][1].context_text, "")
        self.assertEqual(engine.snapshot().committed_raw_context, "Первый")
        reset.set()
        engine.process_frames([object()], 4, 16000)
        self.assertEqual(session.calls[1][1].context_text, "")
        self.assertEqual(engine.snapshot().committed_raw_context, "Второй")


if __name__ == "__main__":
    unittest.main()
