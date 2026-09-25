from __future__ import annotations

import unittest

from voiceflow_app.app.realtime_text_pipeline import (
    RealtimeTextConfig,
    RealtimeTextPipeline,
)


class FakeCleaner:
    def __init__(self, replacement=None):
        self.replacement = replacement
        self.calls = []

    def clean(self, raw_text, mode, language, custom_terms="", deep_grammar=True):
        self.calls.append((raw_text, mode, language, custom_terms, deep_grammar))
        if self.replacement is not None:
            return self.replacement
        return raw_text


class RealtimeTextPipelineTests(unittest.TestCase):
    def config(self, mode="Чистый текст"):
        return RealtimeTextConfig(
            mode=mode,
            language="ru",
            custom_terms="VoiceFlow, ChatGPT",
        )

    def test_clean_for_commit_is_headless_and_softens_open_ellipsis(self):
        cleaner = FakeCleaner()
        pipeline = RealtimeTextPipeline(cleaner)
        raw, cleaned = pipeline.clean_for_commit(
            "  Продолжение мысли...  ",
            self.config(),
            keep_sentence_end=False,
        )
        self.assertEqual(raw, "Продолжение мысли...")
        self.assertEqual(cleaned, "Продолжение мысли")
        self.assertEqual(
            cleaner.calls,
            [("Продолжение мысли...", "Чистый текст", "ru", "VoiceFlow, ChatGPT", False)],
        )

    def test_bad_raw_chunk_is_rejected_before_cleaner(self):
        cleaner = FakeCleaner()
        pipeline = RealtimeTextPipeline(cleaner)
        self.assertEqual(
            pipeline.clean_for_commit("Спасибо за просмотр", self.config()),
            ("", ""),
        )
        self.assertEqual(cleaner.calls, [])

    def test_bad_cleaned_form_falls_back_to_valid_raw_text(self):
        cleaner = FakeCleaner(replacement="...")
        pipeline = RealtimeTextPipeline(cleaner)
        raw, cleaned = pipeline.clean_for_commit("Нормальный текст", self.config())
        self.assertEqual(raw, "Нормальный текст")
        self.assertEqual(cleaned, "Нормальный текст")

    def test_pause_metadata_reaches_exact_inserted_text(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        plan = pipeline.plan_stream_result(
            "Готовый фрагмент",
            "Готовый фрагмент",
            previous_inserted_text="",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=True,
            commit_meta={"sentence_pause": True, "pause_seconds": 1.8},
        )
        self.assertIsNotNone(plan.insertion)
        self.assertEqual(plan.insertion.text, "Готовый фрагмент.")
        self.assertEqual(plan.insertion.paste_text, "Готовый фрагмент. ")
        self.assertTrue(plan.insertion.punctuation.sentence_pause)
        self.assertEqual(plan.insertion.punctuation.pause_seconds, 1.8)

    def test_forced_mid_sentence_commit_preserves_continuation_semantics(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        plan = pipeline.plan_stream_result(
            "Следующая часть...",
            "Следующая часть",
            previous_inserted_text="Я думаю",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=True,
            commit_meta={"forced_commit": True, "pause_seconds": 0.0},
        )
        self.assertIsNotNone(plan.insertion)
        self.assertEqual(plan.insertion.text, "следующая часть")

    def test_raw_vs_cleaned_selection_is_explicit(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        edited = pipeline.plan_stream_result(
            "сырой вариант",
            "Отредактированный вариант",
            previous_inserted_text="",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=True,
        )
        raw = pipeline.plan_stream_result(
            "сырой вариант",
            "Отредактированный вариант",
            previous_inserted_text="",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=False,
        )
        self.assertEqual(edited.insertion.text, "Отредактированный вариант")
        self.assertEqual(edited.insertion.selected_source, "cleaned")
        self.assertEqual(raw.insertion.text, "сырой вариант")
        self.assertEqual(raw.insertion.selected_source, "raw")

    def test_preview_mode_never_requests_paste(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        plan = pipeline.plan_stream_result(
            "Текст",
            "Текст",
            previous_inserted_text="",
            origin="main",
            stream_mode="Только превью",
            insert_edited_text=True,
        )
        self.assertIsNone(plan.insertion)
        self.assertFalse(plan.execute_command)

    def test_trailing_send_command_is_removed_from_text_and_planned_separately(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        plan = pipeline.plan_stream_result(
            "Привет отправь сообщение",
            "Привет отправь сообщение",
            previous_inserted_text="",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=True,
        )
        self.assertEqual(plan.raw, "Привет")
        self.assertEqual(plan.cleaned, "Привет")
        self.assertIsNotNone(plan.command)
        self.assertEqual(plan.command["value"], "enter")
        self.assertTrue(plan.execute_command)
        self.assertIsNotNone(plan.insertion)
        self.assertEqual(plan.insertion.text, "Привет")

    def test_dedupe_uses_already_inserted_text_before_paste_decision(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        plan = pipeline.plan_stream_result(
            "часть сообщения и продолжение",
            "часть сообщения и продолжение",
            previous_inserted_text="Это первая часть сообщения",
            origin="hotkey",
            stream_mode="Вставлять фрагментами",
            insert_edited_text=True,
        )
        self.assertIsNotNone(plan.insertion)
        self.assertEqual(plan.insertion.text, "и продолжение")

    def test_final_tail_uses_selected_raw_or_cleaned_stream(self):
        pipeline = RealtimeTextPipeline(FakeCleaner())
        self.assertEqual(
            pipeline.final_tail(
                "один два три",
                raw="два три четыре raw",
                cleaned="два три четыре clean",
                insert_edited_text=True,
            ),
            "четыре clean",
        )
        self.assertEqual(
            pipeline.final_tail(
                "один два три",
                raw="два три четыре raw",
                cleaned="два три четыре clean",
                insert_edited_text=False,
            ),
            "четыре raw",
        )


if __name__ == "__main__":
    unittest.main()
