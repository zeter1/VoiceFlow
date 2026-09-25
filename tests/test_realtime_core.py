from __future__ import annotations

import unittest

from voiceflow_app.core.realtime import (
    dedupe_stream_chunk,
    get_missing_final_tail,
    is_bad_stream_text,
    lowercase_continuation_start,
    prepare_stream_chunk_for_paste,
    soften_open_stream_text,
    split_stream_voice_command,
)


class RealtimeCoreTests(unittest.TestCase):
    def test_dedupe_removes_overlap_and_exact_duplicate(self) -> None:
        previous = "Это первая часть сообщения"
        self.assertEqual(
            dedupe_stream_chunk(previous, "часть сообщения и продолжение"),
            "и продолжение",
        )
        self.assertEqual(dedupe_stream_chunk(previous, "первая часть"), "")

    def test_repeated_voice_command_is_not_swallowed(self) -> None:
        result = dedupe_stream_chunk(
            "новая строка",
            "новая строка",
            is_voice_command=lambda text: text == "новая строка",
        )
        self.assertEqual(result, "новая строка")

    def test_hallucination_and_punctuation_only_chunks_are_rejected(self) -> None:
        self.assertTrue(is_bad_stream_text("..."))
        self.assertTrue(is_bad_stream_text("Спасибо за просмотр"))
        self.assertTrue(is_bad_stream_text("да да да да"))
        self.assertFalse(is_bad_stream_text("нормальный фрагмент диктовки"))

    def test_forced_mid_sentence_commit_does_not_invent_sentence_end(self) -> None:
        decision = prepare_stream_chunk_for_paste(
            "Я думаю",
            "Следующая часть...",
            commit_meta={"forced_commit": True},
            raw_text="Следующая часть...",
        )
        self.assertEqual(decision.text, "следующая часть")
        self.assertFalse(decision.whisper_sentence_end)

    def test_pause_and_whisper_end_preserve_real_punctuation(self) -> None:
        pause = prepare_stream_chunk_for_paste(
            "",
            "Готовый фрагмент",
            commit_meta={"sentence_pause": True, "pause_seconds": 1.8},
        )
        self.assertEqual(pause.text, "Готовый фрагмент.")

        question = prepare_stream_chunk_for_paste(
            "",
            "Это вопрос",
            raw_text="Это вопрос?",
        )
        self.assertEqual(question.text, "Это вопрос?")

    def test_continuation_lowercases_normal_word_but_preserves_proper_term(self) -> None:
        self.assertEqual(lowercase_continuation_start("Продолжение текста"), "продолжение текста")
        self.assertEqual(lowercase_continuation_start("ChatGPT отвечает"), "ChatGPT отвечает")

    def test_open_chunk_softening_removes_dangling_punctuation(self) -> None:
        self.assertEqual(soften_open_stream_text("Сейчас..!?"), "Сейчас")

    def test_missing_final_tail_returns_only_new_suffix(self) -> None:
        self.assertEqual(
            get_missing_final_tail(
                "один два три четыре",
                "два три четыре пять шесть",
            ),
            "пять шесть",
        )
        self.assertEqual(get_missing_final_tail("один два", "совсем другой текст"), "")

    def test_voice_command_split_prefers_detected_trailing_command(self) -> None:
        command = {"kind": "key", "value": "enter", "phrase": "отправь сообщение"}

        def splitter(text: str):
            suffix = " отправь сообщение"
            if text.endswith(suffix):
                return text[: -len(suffix)], command
            return text, None

        normalizer = lambda text: " ".join(text.lower().split())
        raw, cleaned, result = split_stream_voice_command(
            "Привет отправь сообщение",
            "Привет отправь сообщение",
            splitter=splitter,
            normalizer=normalizer,
        )
        self.assertEqual(raw, "Привет")
        self.assertEqual(cleaned, "Привет")
        self.assertEqual(result, command)


if __name__ == "__main__":
    unittest.main()
