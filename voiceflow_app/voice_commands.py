"""Voice-command vocabulary and pure text-to-command parsing."""

from __future__ import annotations

import re
from typing import Optional


VOICE_CONTROL_COMMANDS = {
    "опусти строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "опусти на строку ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "опусти ниже на строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "строка ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перейди на новую строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перенеси строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "перенеси на новую строку": {"kind": "text", "value": "\n", "label": "новая строка"},
    "новая строка": {"kind": "text", "value": "\n", "label": "новая строка"},
    "следующая строка": {"kind": "text", "value": "\n", "label": "новая строка"},
    "ниже": {"kind": "text", "value": "\n", "label": "новая строка"},
    "строку вниз": {"kind": "text", "value": "\n", "label": "новая строка"},
    "новое предложение": {"kind": "text", "value": ". ", "label": "новое предложение"},
    "точка": {"kind": "text", "value": ".", "label": "точка"},
    "запятая": {"kind": "text", "value": ",", "label": "запятая"},
    "вопросительный знак": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "знак вопроса": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "поставь вопросительный знак": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "поставь знак вопроса": {"kind": "text", "value": "?", "label": "вопросительный знак"},
    "восклицательный знак": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "знак восклицания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "знак внимания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь восклицательный знак": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь знак восклицания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "поставь знак внимания": {"kind": "text", "value": "!", "label": "восклицательный знак"},
    "новый абзац": {"kind": "text", "value": "\n\n", "label": "новый абзац"},
    "опусти абзац": {"kind": "text", "value": "\n\n", "label": "новый абзац"},
    "пробел": {"kind": "text", "value": " ", "label": "пробел"},
    "поставь пробел": {"kind": "text", "value": " ", "label": "пробел"},
    "таб": {"kind": "text", "value": "\t", "label": "табуляция"},
    "табуляция": {"kind": "text", "value": "\t", "label": "табуляция"},
    "удали символ": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "удали букву": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "стереть символ": {"kind": "key", "value": "backspace", "label": "Backspace"},
    "удали слово": {"kind": "hotkey", "value": ("ctrl", "backspace"), "label": "удалить слово"},
    "стереть слово": {"kind": "hotkey", "value": ("ctrl", "backspace"), "label": "удалить слово"},
    "отмени": {"kind": "hotkey", "value": ("ctrl", "z"), "label": "отмена"},
    "отмена": {"kind": "hotkey", "value": ("ctrl", "z"), "label": "отмена"},
    "выдели все": {"kind": "hotkey", "value": ("ctrl", "a"), "label": "выделить все"},
    "выделить все": {"kind": "hotkey", "value": ("ctrl", "a"), "label": "выделить все"},
    "скопируй": {"kind": "hotkey", "value": ("ctrl", "c"), "label": "копировать"},
    "копировать": {"kind": "hotkey", "value": ("ctrl", "c"), "label": "копировать"},
    "вставь": {"kind": "hotkey", "value": ("ctrl", "v"), "label": "вставить"},
    "вставить": {"kind": "hotkey", "value": ("ctrl", "v"), "label": "вставить"},
    "сохрани": {"kind": "hotkey", "value": ("ctrl", "s"), "label": "сохранить"},
    "сохранить": {"kind": "hotkey", "value": ("ctrl", "s"), "label": "сохранить"},
    "вверх": {"kind": "key", "value": "up", "label": "стрелка вверх"},
    "вниз": {"kind": "key", "value": "down", "label": "стрелка вниз"},
    "влево": {"kind": "key", "value": "left", "label": "стрелка влево"},
    "вправо": {"kind": "key", "value": "right", "label": "стрелка вправо"},
    "перейди в начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "в начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "начало строки": {"kind": "key", "value": "home", "label": "начало строки"},
    "перейди в конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "в конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "конец строки": {"kind": "key", "value": "end", "label": "конец строки"},
    "удалить строку": {
        "kind": "sequence",
        "value": (("key", "home"), ("hotkey", ("shift", "end")), ("key", "backspace"), ("key", "delete")),
        "label": "удалить строку",
    },
    "удали строку": {
        "kind": "sequence",
        "value": (("key", "home"), ("hotkey", ("shift", "end")), ("key", "backspace"), ("key", "delete")),
        "label": "удалить строку",
    },
    "очистить поле": {
        "kind": "sequence",
        "value": (("hotkey", ("ctrl", "a")), ("key", "backspace")),
        "label": "очистить поле",
        "reset_message_context": True,
    },
    "очисти поле": {
        "kind": "sequence",
        "value": (("hotkey", ("ctrl", "a")), ("key", "backspace")),
        "label": "очистить поле",
        "reset_message_context": True,
    },
    "отправить сообщение": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь сообщение": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправить сообщения": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь сообщения": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправить": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
    "отправь": {"kind": "key", "value": "enter", "label": "отправить сообщение", "reset_message_context": True},
}

def normalize_voice_command_text(text: str) -> str:
    text = (text or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^\w\sа-яА-ЯёЁ-]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def voice_control_command_from_text(*texts: str) -> Optional[dict[str, object]]:
    for text in texts:
        normalized = normalize_voice_command_text(text)
        if not normalized:
            continue
        command = VOICE_CONTROL_COMMANDS.get(normalized)
        if command:
            return {"phrase": normalized, **command}
    return None


def split_unpunctuated_trailing_send_command(text: str) -> Optional[tuple[str, dict[str, object]]]:
    """Catch "message text send message" when Whisper missed the boundary.

    Keep this intentionally narrow: only Enter/send commands with at least two
    command words are allowed as unpunctuated suffixes. Navigation and delete
    commands should remain exact phrases to avoid accidental actions in normal
    dictation.
    """
    normalized_text = normalize_voice_command_text(text)
    if not normalized_text:
        return None
    raw_tokens = re.findall(r"\S+", text)
    if len(raw_tokens) < 3:
        return None

    command_items = sorted(
        VOICE_CONTROL_COMMANDS.items(),
        key=lambda item: len(normalize_voice_command_text(item[0])),
        reverse=True,
    )
    for phrase, command_data in command_items:
        if not (
            command_data.get("reset_message_context")
            and command_data.get("kind") == "key"
            and command_data.get("value") == "enter"
        ):
            continue
        normalized_phrase = normalize_voice_command_text(phrase)
        command_word_count = len(normalized_phrase.split())
        if command_word_count < 2:
            continue
        if not normalized_text.endswith(" " + normalized_phrase):
            continue
        if len(raw_tokens) <= command_word_count:
            continue
        prefix = " ".join(raw_tokens[:-command_word_count]).strip()
        if len(normalize_voice_command_text(prefix)) < 6:
            continue
        return prefix.rstrip(" ,;:"), {"phrase": normalized_phrase, **command_data}
    return None


def split_trailing_voice_control_command(text: str) -> tuple[str, Optional[dict[str, object]]]:
    text = re.sub(r"[ \t\r\f\v]+", " ", (text or "").strip())
    if not text:
        return "", None
    command = voice_control_command_from_text(text)
    if command:
        return "", command

    # Typical dictation shape: "message text. send message."  Treat only the
    # final sentence as a command to avoid accidental actions inside normal text.
    for match in reversed(list(re.finditer(r"(?<=[.!?…])\s+", text))):
        prefix = text[:match.start()].strip()
        suffix = text[match.end():].strip()
        if not prefix or not suffix:
            continue
        command = voice_control_command_from_text(suffix)
        if command:
            return prefix, command
    unpunctuated = split_unpunctuated_trailing_send_command(text)
    if unpunctuated:
        return unpunctuated
    return text, None

