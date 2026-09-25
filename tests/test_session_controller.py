from __future__ import annotations

from pathlib import Path
import unittest

from voiceflow_app.app.session_controller import (
    HeadlessSessionController,
    SessionPhase,
    SessionTranscriptionRequest,
    SessionTransitionError,
)


class FakeRecorder:
    def __init__(self):
        self.device = None
        self.sample_rate = 16000
        self.channels = 1
        self.is_recording = False
        self.start_calls = 0
        self.stop_calls = 0
        self.discard_calls = 0
        self.fail_start = False
        self.fail_stop = False
        self.wav_path = Path("fake.wav")

    def start(self):
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("start failed")
        self.is_recording = True

    def stop_discard(self):
        if self.is_recording:
            self.stop_stream_keep_frames()
        self.discard_frames()

    def stop_stream_keep_frames(self):
        self.stop_calls += 1
        self.is_recording = False
        if self.fail_stop:
            raise RuntimeError("stop failed")

    def discard_frames(self):
        self.discard_calls += 1

    def stop_to_wav(self):
        return self.wav_path

    def frames_count(self):
        return 1

    def get_frames_since(self, frame_index):
        return [["frame"]], 1, self.sample_rate

    def frames_to_wav(self, frames, prefix="stream_chunk"):
        return self.wav_path if frames else None


class FakeTranscriber:
    active_backend_label = "fake/cpu"

    def __init__(self):
        self.calls = []

    def backend_candidates(self, device_option, compute_type_option):
        return [("cpu", "int8")]

    def transcribe(self, wav_path, language="auto", **kwargs):
        self.calls.append((wav_path, language, kwargs))
        return "Привет мир"


class FakeCleaner:
    def __init__(self):
        self.calls = []

    def clean(self, raw_text, mode, language, custom_terms="", deep_grammar=True):
        self.calls.append((raw_text, mode, language, custom_terms, deep_grammar))
        return "Привет, мир."


class HeadlessSessionControllerTests(unittest.TestCase):
    def make_controller(self):
        recorder = FakeRecorder()
        transcriber = FakeTranscriber()
        cleaner = FakeCleaner()
        return HeadlessSessionController(recorder, transcriber, cleaner), recorder, transcriber, cleaner

    def test_full_headless_start_frames_transcription_commit_stop(self):
        controller, recorder, transcriber, cleaner = self.make_controller()
        session_id = controller.start_capture()
        self.assertEqual(session_id, 1)
        self.assertEqual(controller.phase, SessionPhase.RECORDING)

        result = controller.transcribe_frames(
            [["frame"]],
            SessionTranscriptionRequest(
                language="ru",
                model_name="small",
                clean_mode="Чистый текст",
                deep_grammar=False,
            ),
            session_id=session_id,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.raw_text, "Привет мир")
        self.assertEqual(result.cleaned_text, "Привет, мир.")

        controller.record_commit(result.cleaned_text)
        self.assertEqual(controller.committed_text, "Привет, мир.")
        self.assertTrue(controller.inserted_any)

        controller.stop_capture()
        self.assertEqual(controller.phase, SessionPhase.IDLE)
        self.assertFalse(recorder.is_recording)
        self.assertEqual(recorder.stop_calls, 1)
        self.assertGreaterEqual(recorder.discard_calls, 1)
        self.assertEqual(len(transcriber.calls), 1)
        self.assertEqual(len(cleaner.calls), 1)

    def test_double_start_is_rejected_without_second_recorder_start(self):
        controller, recorder, _transcriber, _cleaner = self.make_controller()
        controller.start_capture()
        with self.assertRaises(SessionTransitionError):
            controller.start_capture()
        self.assertEqual(recorder.start_calls, 1)

    def test_stop_then_restart_increments_session_and_resets_commits(self):
        controller, _recorder, _transcriber, _cleaner = self.make_controller()
        self.assertEqual(controller.start_capture(), 1)
        controller.record_commit("первый")
        controller.stop_capture()
        self.assertEqual(controller.start_capture(), 2)
        self.assertEqual(controller.committed_text, "")
        self.assertFalse(controller.inserted_any)

    def test_failed_start_recovers_to_idle_and_allows_retry(self):
        controller, recorder, _transcriber, _cleaner = self.make_controller()
        recorder.fail_start = True
        with self.assertRaises(RuntimeError):
            controller.start_capture()
        self.assertEqual(controller.phase, SessionPhase.IDLE)
        self.assertFalse(recorder.is_recording)

        recorder.fail_start = False
        self.assertEqual(controller.start_capture(), 2)
        self.assertEqual(controller.phase, SessionPhase.RECORDING)

    def test_stop_failure_recovers_idle_state(self):
        controller, recorder, _transcriber, _cleaner = self.make_controller()
        controller.start_capture()
        recorder.fail_stop = True
        with self.assertRaises(RuntimeError):
            controller.stop_capture()
        self.assertEqual(controller.phase, SessionPhase.IDLE)
        self.assertFalse(recorder.is_recording)

    def test_finalizing_blocks_racing_restart(self):
        controller, _recorder, _transcriber, _cleaner = self.make_controller()
        controller.start_capture()
        controller.begin_finalizing()
        self.assertEqual(controller.phase, SessionPhase.FINALIZING)
        with self.assertRaises(SessionTransitionError):
            controller.begin_capture()


if __name__ == "__main__":
    unittest.main()
