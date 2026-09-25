"""Headless realtime text cleanup, command split and insertion decisions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional

from ..core.realtime import (
    PasteChunkDecision,
    dedupe_stream_chunk,
    get_missing_final_tail,
    is_bad_stream_text,
    prepare_stream_chunk_for_paste,
    soften_open_stream_text,
    split_stream_voice_command,
)
from ..services.contracts import TextCleanerContract
from ..voice_commands import (
    normalize_voice_command_text,
    split_trailing_voice_control_command,
    voice_control_command_from_text,
)


@dataclass(frozen=True)
class RealtimeTextConfig:
    mode: str
    language: str
    custom_terms: str = ""


@dataclass(frozen=True)
class RealtimeInsertionPlan:
    text: str
    paste_text: str
    selected_source: str
    punctuation: PasteChunkDecision


@dataclass(frozen=True)
class RealtimeResultPlan:
    raw: str
    cleaned: str
    command: Optional[dict[str, object]]
    execute_command: bool
    insertion: Optional[RealtimeInsertionPlan]


class RealtimeTextPipeline:
    def __init__(self, cleaner: TextCleanerContract):
        self.cleaner = cleaner

    def clean_for_commit(
        self,
        raw_text: str,
        config: RealtimeTextConfig,
        keep_sentence_end: bool = True,
    ) -> tuple[str, str]:
        raw_text = re.sub(r"\s+", " ", raw_text or "").strip()
        raw_had_trailing_ellipsis = bool(
            re.search(r"(?:\.{2,}|…)[\s.!?…]*$", raw_text)
        )
        if is_bad_stream_text(raw_text):
            return "", ""

        clean_mode = (
            "Чистый текст"
            if config.mode != "Точно как сказано"
            else "Точно как сказано"
        )
        cleaned = self.cleaner.clean(
            raw_text,
            clean_mode,
            config.language,
            custom_terms=config.custom_terms,
            deep_grammar=False,
        )
        cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned).strip()
        if is_bad_stream_text(cleaned):
            if is_bad_stream_text(raw_text):
                return "", ""
            cleaned = raw_text

        if raw_had_trailing_ellipsis or not keep_sentence_end:
            cleaned = soften_open_stream_text(cleaned)
        return raw_text, cleaned

    def dedupe_chunk(self, previous_text: str, new_text: str) -> str:
        return dedupe_stream_chunk(
            previous_text,
            new_text,
            is_voice_command=lambda text: voice_control_command_from_text(text) is not None,
        )

    def plan_stream_result(
        self,
        raw: str,
        cleaned: str,
        *,
        previous_inserted_text: str,
        origin: str,
        stream_mode: str,
        insert_edited_text: bool,
        commit_meta: Optional[dict[str, object]] = None,
    ) -> RealtimeResultPlan:
        raw, cleaned, command = split_stream_voice_command(
            raw,
            cleaned,
            splitter=split_trailing_voice_control_command,
            normalizer=normalize_voice_command_text,
        )
        raw = (raw or "").strip()
        cleaned = (cleaned or "").strip()
        if not raw and cleaned:
            raw = cleaned
        if not cleaned and raw:
            cleaned = raw

        execute_command = bool(
            command is not None
            and stream_mode == "Вставлять фрагментами"
            and origin == "hotkey"
        )

        insertion: Optional[RealtimeInsertionPlan] = None
        if stream_mode == "Вставлять фрагментами" and origin == "hotkey":
            selected_source = "cleaned" if insert_edited_text else "raw"
            selected_text = cleaned if insert_edited_text else raw
            selected_text = self.dedupe_chunk(
                previous_inserted_text,
                selected_text,
            ).strip()
            punctuation = prepare_stream_chunk_for_paste(
                previous_inserted_text,
                selected_text,
                commit_meta=commit_meta,
                raw_text=raw,
            )
            if punctuation.text:
                insertion = RealtimeInsertionPlan(
                    text=punctuation.text,
                    paste_text=punctuation.text + " ",
                    selected_source=selected_source,
                    punctuation=punctuation,
                )

        return RealtimeResultPlan(
            raw=raw,
            cleaned=cleaned,
            command=command,
            execute_command=execute_command,
            insertion=insertion,
        )

    def final_tail(
        self,
        already_inserted: str,
        *,
        raw: str,
        cleaned: str,
        insert_edited_text: bool,
    ) -> str:
        final_text = cleaned if insert_edited_text else raw
        return get_missing_final_tail(already_inserted, final_text)
