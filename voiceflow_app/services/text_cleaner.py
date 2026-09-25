"""Offline dictated-text cleanup service.

Extracted from the historical monolithic voiceflow.py without intentional
runtime behavior changes. This module depends only on text-processing stdlib APIs.
"""

from __future__ import annotations

import re
from typing import Optional


class LocalTextCleaner:
    """Accuracy-oriented local editor for dictated text.

    Fully offline rules cannot equal a cloud LLM, but this pass is deliberately
    conservative and layered: spoken punctuation, filler removal, typo repair,
    sentence segmentation, comma heuristics, term preservation and optional
    LanguageTool grammar correction when the user installs language-tool-python.
    """

    RU_FILLERS = [
        "эээ", "ээ", "эм", "мм", "м-м", "типа", "короче", "как бы", "значит",
        "в общем", "вобщем", "это самое", "наверное наверное", "блин", "слушай",
        "так сказать", "вот", "ну вот", "ну типа", "как говорится", "скажем так",
        # "по сути" is intentionally not removed: in logs it often appears as
        # part of "по сути дела", and deleting only the first words leaves broken text.
        "в принципе", "реально", "прям", "прямо", "ладно",
    ]
    EN_FILLERS = ["um", "uh", "like", "you know", "i mean", "sort of", "kind of", "basically"]

    RU_REPLACEMENTS = {
        "вообщем": "в общем", "вобщем": "в общем", "во-первых": "во-первых", "во первых": "во-первых",
        "во-вторых": "во-вторых", "во вторых": "во-вторых", "щас": "сейчас", "счас": "сейчас",
        "сейчась": "сейчас", "чё": "что", "че": "что", "чтоб": "чтобы", "шо": "что",
        "пожалста": "пожалуйста", "пожалуста": "пожалуйста", "пжл": "пожалуйста",
        "извените": "извините", "извени": "извини", "спс": "спасибо", "оч": "очень",
        "сдесь": "здесь", "здраствуйте": "здравствуйте", "здравствуйтее": "здравствуйте",
        "до свиданья": "до свидания", "врят ли": "вряд ли", "врядли": "вряд ли",
        "придти": "прийти", "будующее": "будущее", "следущий": "следующий",
        "следущее": "следующее", "агенство": "агентство", "симпотичный": "симпатичный",
        "учавствовать": "участвовать", "учавствую": "участвую", "вообщето": "вообще-то",
        "что-ли": "что ли", "как-будто": "как будто", "всётаки": "всё-таки", "все таки": "всё-таки",
        "все-таки": "всё-таки", "по этому": "поэтому", "поэтому что": "потому что",
        "так же": "также", "то же": "тоже", "ни кто": "никто", "ни чего": "ничего",
        "ни когда": "никогда", "не где": "негде", "не зачем": "незачем",
        "на счёт": "насчёт", "не смотря на": "несмотря на", "имейл": "email", "емейл": "email",
    }

    PRESERVE_TERMS = {
        "chat gpt": "ChatGPT", "чат gpt": "ChatGPT", "чат джипити": "ChatGPT", "чат гпт": "ChatGPT",
        "chatgpt": "ChatGPT", "openai": "OpenAI", "опен ai": "OpenAI", "опенэйай": "OpenAI",
        "telegram": "Telegram", "телеграм": "Telegram", "whatsapp": "WhatsApp", "ватсап": "WhatsApp",
        "gmail": "Gmail", "google docs": "Google Docs", "гугл докс": "Google Docs",
        "notion": "Notion", "slack": "Slack", "crm": "CRM", "cursor": "Cursor",
        "vs code": "VS Code", "vscode": "VS Code", "python": "Python", "питон": "Python",
        "javascript": "JavaScript", "java script": "JavaScript", "typescript": "TypeScript",
        "api": "API", "ai": "AI", "make": "Make", "мейк": "Make", "n8n": "n8n", "эн эйт эн": "n8n",
        "tilda": "Tilda", "тильда": "Tilda", "reels": "Reels", "рилс": "Reels", "рилсы": "Reels",
        "instagram": "Instagram", "инстаграм": "Instagram", "windows": "Windows", "powershell": "PowerShell",
        "whisper": "Whisper", "faster-whisper": "faster-whisper", "fast whisper": "faster-whisper",
    }

    HARD_SENTENCE_CUES = [
        "дальше", "далее", "потом", "после этого", "теперь", "следующий момент",
        "следующее", "еще момент", "ещё момент", "важно", "главное", "итог", "в итоге",
        "по итогу", "например", "кстати", "отдельно", "при этом", "второй момент", "третий момент",
        "первое", "второе", "третье", "четвертое", "четвёртое", "пятое",
    ]

    DICTATION_SENTENCE_CUES = [
        "но зато", "но почему", "и меня это", "но", "зато", "да уж", "сейчас посмотрю",
        "у меня", "меня это", "сразу", "сюда", "почему", "зачем", "как вообще", "не будет ли",
        "может быть", "давайте", "я не могу", "иногда", "надо", "он не всегда", "он видел",
        "поэтому", "из-за этого", "в этом случае", "в любом случае", "получается", "получается что",
        "главное", "самое главное", "смотри", "давай", "давайте теперь", "сейчас", "сначала",
        "посмотрим", "вот почему", "по факту", "на самом деле", "это значит", "это означает",
        "это",
        "и еще", "и ещё", "еще", "ещё", "так вот", "в целом", "с одной стороны",
        "с другой стороны", "отдельный момент", "следующая проблема", "следующая штука",
    ]

    QUESTION_START_CUES = [
        "как", "почему", "зачем", "где", "куда", "когда", "откуда",
        "что если", "разве", "неужели", "не будет ли", "может быть", "но почему",
        "а почему", "а как", "а что", "сколько", "какой", "какая", "какое", "какие",
        "кто", "кому", "чей", "можно ли", "нужно ли", "надо ли", "стоит ли", "будет ли",
        "есть ли", "получится ли", "сможет ли", "могу ли", "можем ли", "как мне",
        "что делать если", "куда именно", "как лучше", "как правильно", "что можно",
        "что надо", "что нужно", "почему это", "как сделать",
    ]

    COMMA_BEFORE = [
        "но", "а", "что", "если", "когда", "потому что", "поскольку", "чтобы", "хотя", "который",
        "которая", "которое", "которые", "где", "куда", "откуда", "пока", "так как",
        "перед тем как", "после того как", "несмотря на то что", "для того чтобы",
        "из-за того что", "благодаря тому что", "чем", "словно", "будто", "как будто",
        "если бы", "даже если", "при условии что",
    ]

    def __init__(self):
        self._language_tool_cache: dict[str, object] = {}
        self._language_tool_failed: set[str] = set()

    def clean(self, raw_text: str, mode: str, language: str, custom_terms: str = "", deep_grammar: bool = True) -> str:
        text = raw_text.strip()
        if not text:
            return ""

        user_terms = self._parse_custom_terms(custom_terms)

        # Stage 1. Normalize speech/transcription artifacts.
        text = self._normalize_quotes_and_symbols(text)
        text = self._apply_spoken_punctuation(text)
        text = self._remove_repeated_spaces(text)
        text = self._fix_common_transcription_words(text)
        text = self._restore_preserved_terms(text, user_terms)
        text = self._remove_repeated_words(text)

        if mode != "Точно как сказано":
            text = self._remove_fillers(text)
            text = self._remove_command_phrases(text)
            text = self._remove_repeated_words(text)

        # Stage 2. Punctuation and sentence structure.
        text = self._normalize_spacing(text)
        text = self._insert_sentence_boundaries(text)
        text = self._improve_dictation_sentence_flow(text)
        text = self._split_overlong_sentences(text)
        text = self._insert_commas(text)
        text = self._fix_ru_microgrammar(text)
        text = self._fix_punctuation_collisions(text)
        text = self._question_punctuation(text)
        text = self._fix_punctuation_collisions(text)
        text = self._basic_punctuation(text)
        text = self._capitalize_sentences(text)
        text = self._restore_preserved_terms(text, user_terms)

        # Stage 3. Optional grammar engine.
        if deep_grammar:
            text = self._apply_language_tool_if_available(text, language)
            text = self._normalize_spacing(text)
            text = self._fix_punctuation_collisions(text)
            text = self._capitalize_sentences(text)
            text = self._restore_preserved_terms(text, user_terms)

        # Stage 4. Mode-specific formatting.
        if mode == "Коротко":
            text = self._make_short(text)
        elif mode == "Деловой стиль":
            text = self._business_style(text)
        elif mode == "Развернуто":
            text = self._expanded_style(text)
        elif mode == "Продающий стиль":
            text = self._sales_style(text)
        elif mode == "Для ChatGPT / AI-промпт":
            text = self._prompt_style(text)
        elif mode == "Для кода":
            text = self._coding_style(text)

        return text.strip()

    def _parse_custom_terms(self, custom_terms: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for term in re.split(r"[,;\n]", custom_terms or ""):
            term = term.strip()
            if len(term) >= 2:
                result[term.lower()] = term
        return result

    def _remove_repeated_spaces(self, text: str) -> str:
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r" *\n+ *", lambda m: "\n" * m.group(0).count("\n"), text)
        return text.strip()

    def _normalize_quotes_and_symbols(self, text: str) -> str:
        text = text.replace("…", "...")
        text = text.replace("—", " — ").replace("–", " — ")
        text = text.replace("« ", "«").replace(" »", "»")
        return text

    def _apply_spoken_punctuation(self, text: str) -> str:
        # Spoken punctuation is common in dictation and should not remain as words.
        replacements = [
            (r"(?i)\s+опусти\s+строку\s+", "\n"),
            (r"(?i)\s+строка\s+ниже\s+", "\n"),
            (r"(?i)\s+следующая\s+строка\s+", "\n"),
            (r"(?i)\s+перейди\s+на\s+новую\s+строку\s+", "\n"),
            (r"(?i)\s+новая\s+строка\s+", "\n"),
            (r"(?i)\s+опусти\s+абзац\s+", "\n\n"),
            (r"(?i)\s+новый\s+абзац\s+", "\n\n"),
            (r"(?i)\s+точка\s+", ". "),
            (r"(?i)\s+запятая\s+", ", "),
            (r"(?i)\s+(?:знак\s+вопроса|вопросительный\s+знак)\s+", "? "),
            (r"(?i)\s+(?:знак\s+восклицания|восклицательный\s+знак|знак\s+внимания)\s+", "! "),
            (r"(?i)\s+двоеточие\s+", ": "),
            (r"(?i)\s+точка\s+с\s+запятой\s+", "; "),
            (r"(?i)\s+вопросительный\s+знак\s+", "? "),
            (r"(?i)\s+восклицательный\s+знак\s+", "! "),
            (r"(?i)\s+тире\s+", " — "),
            (r"(?i)\s+открой\s+кавычки\s+", " «"),
            (r"(?i)\s+закрой\s+кавычки\s+", "» "),
        ]
        text = " " + text + " "
        for pattern, repl in replacements:
            text = re.sub(pattern, repl, text)
        return text.strip()

    def _fix_common_transcription_words(self, text: str) -> str:
        for wrong, right in sorted(self.RU_REPLACEMENTS.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = rf"(?i)(?<![\w-]){re.escape(wrong)}(?![\w-])"
            text = re.sub(pattern, right, text)
        return text

    def _restore_preserved_terms(self, text: str, user_terms: Optional[dict[str, str]] = None) -> str:
        terms = dict(self.PRESERVE_TERMS)
        if user_terms:
            terms.update(user_terms)
        for raw, proper in sorted(terms.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = rf"(?i)(?<![\w-]){re.escape(raw)}(?![\w-])"
            text = re.sub(pattern, proper, text)
        return text

    def _remove_repeated_words(self, text: str) -> str:
        # Repeat pass handles "я я", "это это" and short accidental stutters.
        previous = None
        while previous != text:
            previous = text
            text = re.sub(r"(?i)\b([а-яёa-z0-9_-]{2,})\s+\1\b", r"\1", text)
        return text.strip()

    def _remove_fillers(self, text: str) -> str:
        fillers = sorted(self.RU_FILLERS + self.EN_FILLERS, key=len, reverse=True)
        for filler in fillers:
            text = re.sub(rf"(?i)(^|[\s,.;:!?]){re.escape(filler)}(?=\s|,|\.|;|:|!|\?|$)", r"\1", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        return text.strip(" ,")

    def _remove_command_phrases(self, text: str) -> str:
        replacements = [
            r"(?i)^напиши\s+(?:пожалуйста\s+)?(?:клиенту|ей|ему|им|мне)?\s*(?:что|такой текст|сообщение)?\s*",
            r"(?i)^скажи\s+(?:пожалуйста\s+)?(?:клиенту|ей|ему|им|мне)?\s*(?:что)?\s*",
            r"(?i)^сделай\s+(?:мне\s+)?(?:текст|сообщение|письмо|пост|заметку)\s*(?:что|про|о том что)?\s*",
            r"(?i)^напечатай\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^вставь\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^запиши\s+(?:пожалуйста\s+)?(?:что)?\s*",
            r"(?i)^write\s+(?:a\s+)?(?:message|email|post|note)\s*(?:that)?\s*",
        ]
        for pattern in replacements:
            text = re.sub(pattern, "", text).strip()
        return text

    def _normalize_spacing(self, text: str) -> str:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"([,.!?;:])([^\s»\)])", r"\1 \2", text)
        text = re.sub(r"\s+—\s+", " — ", text)
        text = re.sub(r"\n\s+", "\n", text)
        return text.strip()

    def _insert_sentence_boundaries(self, text: str) -> str:
        text = re.sub(r"(?i)\s+(?:и|а)\s+потом\s+", ". Потом ", text)
        text = re.sub(r"(?i)\s+(?:и|а)\s+дальше\s+", ". Дальше ", text)
        text = re.sub(r"(?i)\s+(?:и|а)\s+после\s+этого\s+", ". После этого ", text)
        text = re.sub(r"(?i)\s+и\s+в\s+итоге\s+", ". В итоге ", text)
        for cue in sorted(self.HARD_SENTENCE_CUES, key=len, reverse=True):
            pattern = rf"(?i)(?<=[а-яёa-z0-9\)])\s+({re.escape(cue)})\s+"

            def hard_sentence_repl(match: re.Match[str], cue: str = cue) -> str:
                clause = re.split(r"[.!?\n]", match.string[:match.start()])[-1].strip()
                clause_lower = clause.lower()
                # Do not split short phrases like "А теперь", "давай теперь"
                # or "давайте дальше". Logs showed these becoming
                # "А. Теперь" / "Давай. Теперь" in realtime text.
                if cue in {"теперь", "дальше", "потом"}:
                    if len(clause) < 18 or re.match(r"^(?:а|и\s+)?давай(?:те)?(?:\s+\S+){0,6}$", clause_lower):
                        return f" {match.group(1).lower()} "
                return f". {match.group(1).capitalize()} "

            text = re.sub(pattern, hard_sentence_repl, text)
        text = re.sub(r"(?i)\b(?:и|а)\.\s+(Потом|Дальше|После этого|В итоге)\b", r". \1", text)
        return text

    def _improve_dictation_sentence_flow(self, text: str) -> str:
        # Realtime chunks are cleaned separately, so we add a small layer of
        # dictation-specific sentence cues that Whisper often leaves as commas.
        def sentence_cue_repl(match: re.Match[str]) -> str:
            cue = match.group(1)
            prefix = match.string[:match.start()]
            clause = re.split(r"[.!?\n]", prefix)[-1].strip()
            cue_lower = cue.lower()
            question_like = cue_lower in {
                "почему", "зачем", "как вообще", "не будет ли", "может быть",
                "а почему", "а как", "а что", "сколько", "куда именно",
            }
            last_word_match = re.search(r"(?i)([а-яёa-z]+)\s*$", clause)
            last_word = last_word_match.group(1).lower() if last_word_match else ""
            if last_word in {"и", "а", "но"} and not cue_lower.startswith(last_word + " "):
                return match.group(0)

            short_cue = cue_lower in {
                "давайте", "давай", "давайте теперь", "надо", "иногда", "он не всегда",
                "он видел", "у меня", "и меня это", "я не могу", "поэтому", "из-за этого",
                "в этом случае", "в любом случае", "получается", "получается что",
                "главное", "самое главное", "смотри", "сейчас", "сначала", "посмотрим",
                "вот почему", "по факту", "на самом деле", "это значит", "это означает",
                "и еще", "и ещё", "еще", "ещё", "так вот", "в целом", "с одной стороны",
                "с другой стороны", "отдельный момент", "следующая проблема", "следующая штука",
            }
            # Do not split indirect questions. Logs showed bad output like
            # "Но я не понимаю. Почему..." instead of "не понимаю, почему...".
            clause_lower = clause.lower().strip(" ,;:")
            if cue_lower in {"почему", "зачем", "как вообще", "а почему", "а как", "а что"}:
                if re.search(r"\b(?:не\s+понимаю|не\s+знаю|непонятно|не\s+понял|не\s+поняла|не\s+ясно)$", clause_lower):
                    return match.group(0)
            if cue_lower == "это" and clause.lower().startswith("давайте"):
                short_cue = True
            if cue_lower == "это" and not clause.lower().startswith("давайте") and len(clause) < 35:
                return match.group(0)
            if len(clause) < (12 if short_cue else 28) and not question_like:
                return match.group(0)
            return f". {cue[0].upper() + cue[1:]} "

        for cue in sorted(self.DICTATION_SENTENCE_CUES, key=len, reverse=True):
            pattern = rf"(?i)(?<![.!?\n])\s+({re.escape(cue)})\s+"
            text = re.sub(pattern, sentence_cue_repl, text)

        text = re.sub(r"(?i)\b(да уж|может быть|скорее всего|наверное|вероятно|кстати|если честно|честно говоря|по факту|на самом деле)\s+", lambda m: f"{m.group(1).capitalize()}, ", text)
        # "по сути дела" is a single colloquial phrase. Do not rewrite it
        # into the unnatural "По сути, дела" during realtime cleanup.
        text = re.sub(r"(?i)\bпо сути\s+(?!дела\b)", lambda m: f"{m.group(0).strip().capitalize()}, ", text)
        text = re.sub(r"(?i)\bсразу\s+то\s+что\b", "сразу то, что", text)
        text = re.sub(r"(?i)\bдело\s+в\s+том\s+что\b", "дело в том, что", text)
        text = re.sub(r"(?i)\bпотому\s+что\s+это\b", "потому что это", text)
        text = re.sub(r"(?i)\bэто\s+значит\s+что\b", "это значит, что", text)
        text = re.sub(r"(?i)\bэто\s+означает\s+что\b", "это означает, что", text)
        text = re.sub(r"(?i)\bполучается\s+что\b", "получается, что", text)
        text = re.sub(r"(?i)\bя\s+думаю\s+что\b", "я думаю, что", text)
        text = re.sub(r"(?i)\bмне\s+кажется\s+что\b", "мне кажется, что", text)
        text = re.sub(r"(?i)\bя\s+понимаю\s+что\b", "я понимаю, что", text)
        text = re.sub(r"(?i)\bя\s+вижу\s+что\b", "я вижу, что", text)
        text = re.sub(r"(?i)\bпроблема\s+в\s+том\s+что\b", "проблема в том, что", text)
        text = re.sub(r"(?i)\bвопрос\s+в\s+том\s+что\b", "вопрос в том, что", text)
        text = re.sub(r"(?i)\bситуация\s+в\s+том\s+что\b", "ситуация в том, что", text)
        text = re.sub(r"(?i)\bс\s+одной\s+стороны\s+", "с одной стороны, ", text)
        text = re.sub(r"(?i)\bс\s+другой\s+стороны\s+", "с другой стороны, ", text)
        text = re.sub(r"(?i)\bв\s+целом\s+", "в целом, ", text)
        text = re.sub(r"(?i)\bтак\s+вот\s+", "так вот, ", text)
        return text

    def _question_punctuation(self, text: str) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []
        for part in parts:
            sentence = part.strip()
            if not sentence:
                continue
            lower = sentence.lower()
            trailing_ellipsis = bool(re.search(r"(?:\.{2,}|…)[\s.!?…]*$", sentence))
            is_question = any(
                lower.startswith(cue + " ") or lower.startswith(cue + ",") or lower == cue
                for cue in self.QUESTION_START_CUES
            )
            # A bare "ли" is too broad for realtime dictation: phrases like
            # "то ли правила срезались" were incorrectly converted into
            # "срезались,?". Keep only clear question patterns.
            is_question = is_question or bool(re.search(
                r"(?i)\b(?:разве|неужели)\b|\b(?:можно|нужно|надо|стоит|будет|есть|получится|сможет|могу|можем)\s+ли\b",
                sentence[:120],
            ))
            if is_question and not trailing_ellipsis and len(sentence) <= 180 and not lower.startswith("как только"):
                # Do not leave broken combinations like ",?" or ";?" when a
                # streaming chunk ended with a comma.
                sentence = re.sub(r"[,:;]\s*$", "", sentence).rstrip()
                if sentence.endswith("."):
                    sentence = sentence[:-1].rstrip() + "?"
                elif not re.search(r"[!?…]$", sentence):
                    sentence = sentence.rstrip() + "?"
            result.append(sentence)
        return " ".join(result)

    def _split_overlong_sentences(self, text: str) -> str:
        parts = re.split(r"(?<=[.!?])\s+", text)
        result: list[str] = []
        for sentence in parts:
            if len(sentence) <= 170:
                result.append(sentence)
                continue
            sentence = re.sub(r"(?i)\s+(но|зато|при этом|поэтому|из-за этого|после этого|дальше|потом|в итоге|в этом случае|в любом случае|у меня|давайте|давай|иногда|надо|получается|главное|самое главное|смотри|сначала|сейчас|на самом деле)\s+", r". \1 ", sentence)
            sentence = re.sub(r"(?i)\s+(и еще|и ещё)\s+", r". Ещё ", sentence)
            result.extend(re.split(r"(?<=[.!?])\s+", sentence))
        return " ".join(part.strip() for part in result if part.strip())

    def _insert_commas(self, text: str) -> str:
        intro_words = [
            "пожалуйста", "кажется", "возможно", "наверное", "вероятно", "конечно",
            "к сожалению", "к счастью", "во-первых", "во-вторых", "с одной стороны", "с другой стороны",
            "честно говоря", "если честно", "на мой взгляд", "по-моему", "как минимум", "как правило",
            "кстати", "например", "скорее всего", "может быть", "по сути", "по факту", "на самом деле",
        ]
        for word in sorted(intro_words, key=len, reverse=True):
            if word == "по сути":
                pattern = rf"(?i)(^|[.!?]\s+)({re.escape(word)})(\s+)(?!дела\b)"
            else:
                pattern = rf"(?i)(^|[.!?]\s+)({re.escape(word)})(\s+)"
            text = re.sub(pattern, lambda m: f"{m.group(1)}{m.group(2).capitalize()}, ", text)

        middle_intro_words = [
            "кажется", "возможно", "наверное", "вероятно", "конечно", "к сожалению", "к счастью",
            "кстати", "например", "скорее всего", "может быть", "честно говоря", "если честно",
            "на мой взгляд", "по-моему", "как правило", "по сути", "по факту",
        ]
        for word in sorted(middle_intro_words, key=len, reverse=True):
            if word == "по сути":
                pattern = rf"(?i)(?<=[а-яёa-z0-9])\s+({re.escape(word)})\s+(?!дела\b)(?=[а-яёa-z0-9])"
            else:
                pattern = rf"(?i)(?<=[а-яёa-z0-9])\s+({re.escape(word)})\s+(?=[а-яёa-z0-9])"
            text = re.sub(pattern, r", \1, ", text)

        after_intro_words = ["например", "кстати", "во-первых", "во-вторых", "главное", "самое главное", "смотри", "слушай"]
        for word in sorted(after_intro_words, key=len, reverse=True):
            text = re.sub(rf"(?i)\b({re.escape(word)})\s+(?=[а-яёa-z0-9])", r"\1, ", text)

        text = re.sub(r"(?i)(?<![,.!?;:])\s+(то есть|то бишь|а именно)\s+", r", \1 ", text)
        text = re.sub(r"(?i)\b(дело|проблема|суть)\s+в\s+том\s+что\b", r"\1 в том, что", text)
        text = re.sub(r"(?i)\b(важно|главное)\s+то\s+что\b", r"\1 то, что", text)
        text = re.sub(r"(?i)\b(если|когда|пока|хотя)\s+([^.!?]{8,90}?)\s+(то|тогда)\s+", r"\1 \2, \3 ", text)
        for conj in sorted(self.COMMA_BEFORE, key=len, reverse=True):
            pattern = rf"(?i)(?<![,.!?;:])\s+({re.escape(conj)})\s+"
            text = re.sub(pattern, r", \1 ", text)
        text = re.sub(r"(?i)не только\s+(.+?)\s+но и\s+", r"не только \1, но и ", text)
        text = re.sub(r"(?i)как\s+(.{3,70}?)\s+так и\s+", r"как \1, так и ", text)
        text = re.sub(r"(?i)как\s+только\s+", "как только ", text)
        text = re.sub(r"(?i)\bпотому,\s+что\b", "потому что", text)
        text = re.sub(r"(?i)\bтак,\s+как\b", "так как", text)
        text = re.sub(r"(?i)\bкак,\s+будто\b", "как будто", text)
        text = re.sub(r"(?i)\bчто,\s+если\b", "что если", text)
        text = re.sub(r"(?i)\bи,\s+что\s+(делать|нужно|надо|можно|будет|получится|лучше)\b", r"и что \1", text)
        text = re.sub(r"(?i)\bчто\s+делать\s+если\b", "что делать, если", text)
        # Remove common false commas caused by the broad conjunction heuristic.
        text = re.sub(r"(?i)^А,\s+", "А ", text)
        text = re.sub(r"(?i)\bа,\s+(если|когда|как|почему|зачем|что|куда|где)\b", r"а \1", text)
        text = re.sub(r"(?i)\bи,\s+что\b", "и что", text)
        text = re.sub(r"(?i)\bну,\s+что\b", "ну что", text)
        text = re.sub(r"(?i)\bпо сути,\s+дела\b", lambda m: "По сути дела" if m.group(0)[0].isupper() else "по сути дела", text)
        text = re.sub(r"(?<=[а-яёa-z0-9])\s+По сути дела\b", " по сути дела", text)
        return text

    def _fix_ru_microgrammar(self, text: str) -> str:
        # Conservative local grammar normalizations for dictated Russian.
        text = re.sub(r"(?i)\bболее\s+точнее\b", "точнее", text)
        text = re.sub(r"(?i)\bболее\s+лучше\b", "лучше", text)
        text = re.sub(r"(?i)\bболее\s+хуже\b", "хуже", text)
        text = re.sub(r"(?i)\bболее\s+плохо\b", "хуже", text)
        text = re.sub(r"(?i)\bдовольно\s+таки\b", "довольно-таки", text)
        text = re.sub(r"(?i)\bвсе\s+таки\b", "всё-таки", text)
        text = re.sub(r"(?i)\bвсё\s+таки\b", "всё-таки", text)
        text = re.sub(r"(?i)\bшел\b", "шёл", text)
        text = re.sub(r"(?i)\bшла\b", "шла", text)
        text = re.sub(r"(?i)\bя\s+буду\s+смогу\b", "я смогу", text)
        text = re.sub(r"(?i)\bмы\s+будем\s+сможем\b", "мы сможем", text)
        text = re.sub(r"(?i)\bне\s+успеваю\s+сделать\b", "не успеваю сделать", text)
        text = re.sub(r"(?i)\bв\s+течении\b", "в течение", text)
        text = re.sub(r"(?i)\bпо\s+приезду\b", "по приезде", text)
        text = re.sub(r"(?i)\bсогласно\s+([а-яё]+ого)\b", r"согласно \1", text)
        return text

    def _fix_punctuation_collisions(self, text: str) -> str:
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r",\s*,+", ",", text)
        # Whisper often emits ellipsis for unfinished realtime chunks. For
        # dictation this usually becomes noisy ".." / "..." in the inserted
        # text, so collapse repeated dots to a single normal sentence dot.
        text = re.sub(r"(?:\.\s*){2,}", ".", text)
        text = re.sub(r"([.!?])\s*,", r"\1", text)
        text = re.sub(r",\s*([.!?])", r"\1", text)
        text = re.sub(r"\.\s*\?", "?", text)
        text = re.sub(r"\?\s*\.", "?", text)
        text = re.sub(r"!\s*\.", "!", text)
        text = re.sub(r"(?i)\bа,\s+(если|когда|как|почему|зачем|что|куда|где)\b", r"а \1", text)
        text = re.sub(r"(?i)\bи,\s+что\b", "и что", text)
        text = re.sub(r"(?i)\bну,\s+что\b", "ну что", text)
        text = re.sub(r"(?i)\bпо сути,\s+дела\b", lambda m: "По сути дела" if m.group(0)[0].isupper() else "по сути дела", text)
        text = re.sub(r"(?<=[а-яёa-z0-9])\s+По сути дела\b", " по сути дела", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"\s+\n", "\n", text)
        return text.strip()

    def _basic_punctuation(self, text: str) -> str:
        if not re.search(r"[.!?…]$", text):
            text += "."
        return text

    def _capitalize_sentences(self, text: str) -> str:
        def cap_match(match: re.Match[str]) -> str:
            return match.group(1) + match.group(2).upper()
        text = text.strip()
        if text:
            text = text[0].upper() + text[1:]
        text = re.sub(r"(^|[.!?]\s+)([а-яёa-z])", cap_match, text)
        text = re.sub(r"(\n\s*)([а-яёa-z])", cap_match, text)
        return text

    def _apply_language_tool_if_available(self, text: str, language: str) -> str:
        lang = self._map_language_tool_code(language, text)
        if lang in self._language_tool_failed:
            return text
        try:
            import language_tool_python  # type: ignore
        except Exception:
            return text
        try:
            tool = self._language_tool_cache.get(lang)
            if tool is None:
                tool = language_tool_python.LanguageTool(lang)
                self._language_tool_cache[lang] = tool
            corrected = tool.correct(text)
            if isinstance(corrected, str) and corrected.strip():
                return corrected.strip()
        except Exception:
            self._language_tool_failed.add(lang)
        return text

    def _map_language_tool_code(self, language: str, text: str) -> str:
        if language == "en":
            return "en-US"
        if language == "es":
            return "es"
        if language == "fr":
            return "fr"
        if language == "de":
            return "de-DE"
        if language == "ru":
            return "ru-RU"
        return "ru-RU" if re.search(r"[а-яёА-ЯЁ]", text) else "en-US"

    def _make_short(self, text: str) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        short = " ".join(sentences[:2]).strip()
        if len(short) > 280:
            short = short[:277].rstrip() + "..."
        return short

    def _business_style(self, text: str) -> str:
        lower = text.lower()
        if len(text) > 55 and any(word in lower for word in ["клиент", "материал", "задач", "стоим", "отправ", "договор", "счет", "счёт"]):
            return f"Здравствуйте!\n\n{text}\n\nС уважением."
        return text

    def _expanded_style(self, text: str) -> str:
        return f"{text}\n\nДополнительно можно уточнить детали, сроки и ожидаемый результат."

    def _sales_style(self, text: str) -> str:
        return f"{text}\n\nЕсли вам актуально — напишите, и я подскажу лучший вариант под вашу задачу."

    def _prompt_style(self, text: str) -> str:
        return (
            "Создай результат по следующему запросу. "
            "Сохрани смысл, сделай ответ структурированным, понятным и полезным.\n\n"
            f"Запрос: {text}"
        )

    def _coding_style(self, text: str) -> str:
        return (
            "Сформируй техническое решение по задаче ниже. "
            "Опиши архитектуру, шаги реализации, возможные ошибки и пример кода.\n\n"
            f"Задача: {text}"
        )
