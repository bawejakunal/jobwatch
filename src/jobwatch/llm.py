"""Provider-agnostic LLM hooks (Phase 3). Off by default; BYOK when wired."""

from __future__ import annotations

from typing import Protocol

from jobwatch.models import NormalizedJob


class LLMProvider(Protocol):
    def extract_jobs(self, page_text: str, base_url: str) -> list[NormalizedJob]: ...

    def is_new_grad(self, title: str, description: str | None) -> bool | None: ...


class NullLLM:
    def extract_jobs(self, page_text: str, base_url: str) -> list[NormalizedJob]:
        del page_text, base_url
        return []

    def is_new_grad(self, title: str, description: str | None) -> bool | None:
        del title, description
        return None
