"""Headless delivery coordination for planned realtime text and commands."""

from __future__ import annotations

from dataclasses import dataclass

from .ports import TextInsertionPort, VoiceActionPort
from .realtime_text_pipeline import RealtimeInsertionPlan


@dataclass(frozen=True)
class InsertionDeliveryOutcome:
    ok: bool
    committed_text: str
    paste_text: str
    selected_source: str
    code: str
    error: str
    foreground_hwnd: int | None
    focus_hwnd: int | None


@dataclass(frozen=True)
class VoiceCommandDeliveryOutcome:
    ok: bool
    kind: str
    phrase: str
    label: str
    reset_message_context: bool
    code: str
    error: str
    foreground_hwnd: int | None
    focus_hwnd: int | None


class RealtimeDeliveryController:
    def __init__(
        self,
        text_inserter: TextInsertionPort,
        voice_action_executor: VoiceActionPort,
    ):
        self.text_inserter = text_inserter
        self.voice_action_executor = voice_action_executor

    def deliver_insertion(
        self,
        plan: RealtimeInsertionPlan,
    ) -> InsertionDeliveryOutcome:
        result = self.text_inserter.paste_current(plan.paste_text)
        return InsertionDeliveryOutcome(
            ok=result.ok,
            committed_text=plan.text if result.ok else "",
            paste_text=plan.paste_text,
            selected_source=plan.selected_source,
            code=result.code,
            error=result.error,
            foreground_hwnd=result.foreground_hwnd,
            focus_hwnd=result.focus_hwnd,
        )

    def execute_voice_command(
        self,
        command: dict[str, object],
    ) -> VoiceCommandDeliveryOutcome:
        result = self.voice_action_executor.execute(command)
        return VoiceCommandDeliveryOutcome(
            ok=result.ok,
            kind=str(command.get("kind", "")),
            phrase=str(command.get("phrase", "")),
            label=str(command.get("label", command.get("phrase", "voice command"))),
            reset_message_context=bool(command.get("reset_message_context")),
            code=result.code,
            error=result.error,
            foreground_hwnd=result.foreground_hwnd,
            focus_hwnd=result.focus_hwnd,
        )
