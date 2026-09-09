from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StageStreamEvent:
    event: str
    attempt: int
    text: str | None = None


class StageFieldStreamParser:
    """Extract one DSPy ChatAdapter output field from raw provider stream chunks.

    Refine may invoke several model calls. Only the requested specification
    field is exposed; reasoning, quality-assessment fields, and Refine feedback
    remain internal. Every extracted candidate is provisional until Refine
    returns its final Prediction.
    """

    def __init__(self, field_name: str) -> None:
        if not field_name.strip():
            raise ValueError("field_name is required")
        self.start_marker = f"[[ ## {field_name.strip()} ## ]]"
        self.end_marker = "[[ ## quality_assessment ## ]]"
        self.buffer = ""
        self.active = False
        self.attempt = 0
        self._first_content = False

    @staticmethod
    def _suffix_prefix_length(value: str, marker: str) -> int:
        limit = min(len(value), len(marker) - 1)
        for size in range(limit, 0, -1):
            if marker.startswith(value[-size:]):
                return size
        return 0

    def feed(self, text: str) -> list[StageStreamEvent]:
        if not text:
            return []

        self.buffer += text
        events: list[StageStreamEvent] = []

        while True:
            marker = self.end_marker if self.active else self.start_marker
            index = self.buffer.find(marker)

            if index >= 0:
                before = self.buffer[:index]
                self.buffer = self.buffer[index + len(marker) :]

                if self.active:
                    if before:
                        chunk = before
                        if not self._first_content:
                            chunk = chunk.lstrip()
                            self._first_content = True
                        if chunk:
                            events.append(StageStreamEvent("token", self.attempt, chunk))
                    events.append(StageStreamEvent("candidate_end", self.attempt))
                    self.active = False
                    self._first_content = False
                else:
                    self.attempt += 1
                    events.append(StageStreamEvent("candidate_start", self.attempt))
                    self.active = True
                    self._first_content = False
                continue

            keep = self._suffix_prefix_length(self.buffer, marker)
            safe_length = len(self.buffer) - keep

            if self.active and safe_length > 0:
                chunk = self.buffer[:safe_length]
                self.buffer = self.buffer[safe_length:]
                if not self._first_content:
                    chunk = chunk.lstrip()
                    self._first_content = True
                if chunk:
                    events.append(StageStreamEvent("token", self.attempt, chunk))
            elif not self.active:
                self.buffer = self.buffer[-keep:] if keep else ""
            break

        return events

    def finalize(self) -> list[StageStreamEvent]:
        events: list[StageStreamEvent] = []
        if self.active:
            chunk = self.buffer
            if not self._first_content:
                chunk = chunk.lstrip()
            if chunk:
                events.append(StageStreamEvent("token", self.attempt, chunk))
            events.append(StageStreamEvent("candidate_end", self.attempt))
        self.buffer = ""
        self.active = False
        self._first_content = False
        return events


def provider_chunk_text(value: Any) -> str | None:
    """Return text from a LiteLLM/OpenAI-compatible streamed chunk when present."""

    try:
        choices = getattr(value, "choices")
        if not choices:
            return None
        delta = getattr(choices[0], "delta")
        content = getattr(delta, "content", None)
        return str(content) if content is not None else None
    except (AttributeError, IndexError, TypeError):
        return None
