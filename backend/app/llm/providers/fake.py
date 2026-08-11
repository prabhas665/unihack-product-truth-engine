"""Deterministic, offline fake LLM provider for tests and local demos.

Never makes a network call. Unit tests configure canned responses (or a
canned error) to exercise the abstraction end to end without an API key.

A future real provider (e.g. deepseek.py) follows the same shape:
subclass LLMClient, implement ONLY _complete(), then register it in
providers/__init__.py.
"""

from __future__ import annotations

from typing import Sequence

from app.llm.base import LLMClient
from app.llm.errors import LLMError


class FakeLLMClient(LLMClient):
    provider = "fake"

    def __init__(
        self,
        responses: Sequence[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        """responses: canned outputs consumed one per call; error: raised instead."""
        self._responses = list(responses or [])
        self._error = error
        self.calls: list[str] = []

    def _complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        temperature: float | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        if self._responses:
            return self._responses.pop(0)
        raise LLMError(
            f"{self.provider}: no canned response configured for prompt "
            f"{prompt[:60]!r}..."
        )
