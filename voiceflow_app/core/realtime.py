"""Pure realtime dictation decisions.

This module intentionally imports no Tkinter, microphone, Whisper or VoiceFlow
runtime module. It is the deterministic core behind realtime chunk decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Optional


VoiceCommandDetector = Callable[[str], bool]
TrailingCommandSplitter = Callable[[str], tuple[str, Optional[dict[str, object]]]]
TextNormalizer = Callable[[str], str]


_BAD_STREAM_PHRASES = (
    "спасибо за просмотр",
    "спасибо за внимание",
    "продолжение следует",
    "субтитры сделал",
    "субтитры создал",
    "субтитры создала",
    "субтитры создавал",
    "субтитры подготовил",
    "редактор субтитров",
    "подписывайтесь",
    "смотрите далее",
    "thank you for watching",
    "subtitles by",
    "captioned by",
    "dima torzok",
    "dimatorzok",
    "дима торжок",
    "диматорзок",
    "субтитры dima",
    "субтитры dimatorzok",
)

_PROPER_CONTINUATION_STARTS = {
    "ChatGPT",
    "OpenAI",
    "Telegram",
    "WhatsApp",
    "Gmail",
    "Google",
    "Python",
    "JavaScript",
    "TypeScript",
    "PowerShell",
    "Windows",
    "Whisper",
    "CUDA",
}


def normalize_stream_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", " ", (text or "").lower())
    return [word for word in cleaned.split() if word]


def dedupe_stream_chunk(
    previous_text: str,
    new_text: str,
    *,
    is_voice_command: Optional[VoiceCommandDetector] = None,
) -> str:
    """Remove a repeated prefix while preserving intentional command repeats."""
    new_text = re.sub(r"\s+", " ", new_text or "").strip()
    previous_text = re.sub(r"\s+", " ", previous_text or "").strip()
    if not new_text:
        return ""
    if is_voice_command is not None and is_voice_command(new_text):
        return new_text
    if not previous_text:
        return new_text

    previous_words = normalize_stream_words(previous_text)
    new_words = normalize_stream_words(new_text)
    if not previous_words or not new_words:
        return new_text

    max_overlap = min(14, len(previous_words), len(new_words))
    best = 0
    for size in range(max_overlap, 0, -1):
        if previous_words[-size:] == new_words[:size]:
            best = size
            break

    if best <= 0:
        normalized_previous = " ".join(previous_words)
        normalized_new = " ".join(new_words)
        if normalized_new and normalized_new in normalized_previous:
            return ""
        return new_text

    original_tokens = new_text.split()
    if best >= len(original_tokens):
        return ""
    return " ".join(original_tokens[best:]).strip()


def is_bad_stream_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not normalized or len(normalized) < 2:
        return True

    punctuation_only = re.sub(r"\s+", "", normalized)
    if re.fullmatch(r"[\.。…]+", punctuation_only) or re.fullmatch(r"[\.,;:!?…\-—]+", punctuation_only):
        return True
    if any(phrase in normalized for phrase in _BAD_STREAM_PHRASES):
        return True

    words = normalized.split()
    return len(words) >= 4 and len(set(words)) <= 2


def soften_open_stream_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()
    if not text:
        return ""
    if not re.search(r"(?i)\b(?:т\.д|т\.п|и т\.д|и т\.п)\.$", text):
        text = re.sub(r"(?:\s*[.!?…]+)+\s*$", "", text).rstrip()
        text = re.sub(r"\s+[,;:]\s*$", "", text).rstrip()
    return text


def stream_has_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?…][\"'»)\]]*$", (text or "").strip()))


def lowercase_continuation_start(text: str) -> str:
    if not text:
        return text
    first_word = re.match(r"^[\"'«(]*([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9_+-]*)", text)
    if first_word and first_word.group(1) in _PROPER_CONTINUATION_STARTS:
        return text
    return re.sub(
        r"^([\"'«(]*)([А-ЯЁA-Z])([а-яёa-z])",
        lambda match: match.group(1) + match.group(2).lower() + match.group(3),
        text,
        count=1,
    )


@dataclass(frozen=True)
class PasteChunkDecision:
    text: str
    pause_seconds: float
    sentence_pause: bool
    whisper_sentence_end: bool
    previous_had_sentence_end: bool
    forced_commit: bool


def prepare_stream_chunk_for_paste(
    previous_text: str,
    chunk_text: str,
    *,
    commit_meta: Optional[dict[str, object]] = None,
    raw_text: str = "",
) -> PasteChunkDecision:
    chunk_text = re.sub(r"[ \t\r\f\v]+", " ", chunk_text or "").strip()
    meta = commit_meta or {}
    sentence_pause = bool(meta.get("sentence_pause"))
    pause_seconds = float(meta.get("pause_seconds") or 0.0)
    whisper_sentence_end = bool(meta.get("whisper_sentence_end"))
    forced_commit = bool(meta.get("forced_commit"))
    raw_text = raw_text or str(meta.get("raw_text") or "")
    previous_had_sentence_end = stream_has_sentence_end(previous_text)

    if not chunk_text:
        return PasteChunkDecision(
            text="",
            pause_seconds=pause_seconds,
            sentence_pause=sentence_pause,
            whisper_sentence_end=whisper_sentence_end,
            previous_had_sentence_end=previous_had_sentence_end,
            forced_commit=forced_commit,
        )

    raw_end_match = re.search(r"([.!?])(?:[\"'»\)\]]*)\s*$", raw_text.strip())
    if raw_end_match and not re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text.strip()):
        whisper_sentence_end = True

    if sentence_pause and not stream_has_sentence_end(chunk_text):
        chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + "."
    elif whisper_sentence_end and not stream_has_sentence_end(chunk_text):
        end_char = raw_end_match.group(1) if raw_end_match else "."
        chunk_text = re.sub(r"[,;:]\s*$", "", chunk_text).rstrip() + end_char
    elif not sentence_pause and not whisper_sentence_end and forced_commit:
        chunk_text = soften_open_stream_text(chunk_text)

    if previous_text and not previous_had_sentence_end and not sentence_pause:
        chunk_text = lowercase_continuation_start(chunk_text)

    return PasteChunkDecision(
        text=chunk_text,
        pause_seconds=pause_seconds,
        sentence_pause=sentence_pause,
        whisper_sentence_end=whisper_sentence_end,
        previous_had_sentence_end=previous_had_sentence_end,
        forced_commit=forced_commit,
    )


def get_missing_final_tail(already_inserted: str, final_text: str) -> str:
    already_words = normalize_stream_words(already_inserted)
    final_words = normalize_stream_words(final_text)
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


def split_stream_voice_command(
    raw: str,
    cleaned: str,
    *,
    splitter: TrailingCommandSplitter,
    normalizer: TextNormalizer,
) -> tuple[str, str, Optional[dict[str, object]]]:
    raw_before, raw_command = splitter(raw)
    cleaned_before, cleaned_command = splitter(cleaned)
    command = cleaned_command or raw_command
    if command is None:
        return raw, cleaned, None
    if raw_command is None and normalizer(raw) == normalizer(cleaned):
        raw_before = cleaned_before
    if cleaned_command is None:
        cleaned_before = raw_before
    return raw_before.strip(), cleaned_before.strip(), command
