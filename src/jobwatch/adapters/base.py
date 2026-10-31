from __future__ import annotations

from typing import Protocol

import httpx

from jobwatch.models import Company, NormalizedJob


class AdapterError(Exception):
    """A single-company fetch failed. The rest of the scan continues."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class NeedsBrowserError(AdapterError):
    """This tenant cannot be read over plain HTTP. Use a Phase 2 recipe."""


class Adapter(Protocol):
    name: str

    def matches_url(self, url: str) -> bool: ...

    def parse_identity(self, url: str) -> dict[str, str]: ...

    def sniff_html(self, url: str, html: str) -> dict[str, str] | None: ...

    def fetch(
        self,
        company: Company,
        client: httpx.Client,
        *,
        include_content: bool = False,
    ) -> list[NormalizedJob]: ...


def apply_identity(company: Company, identity: dict[str, str]) -> Company:
    """Fill blank identity fields from detection without clobbering overrides."""
    mapping = {
        "board": "board",
        "host": "host",
        "tenant": "tenant",
        "site": "site",
        "locale": "locale",
        "portal": "portal",
        "region": "region",
    }
    for key, attr in mapping.items():
        value = identity.get(key)
        if value and not getattr(company, attr):
            setattr(company, attr, value)
    if identity.get("ats") and not company.ats:
        company.ats = identity["ats"]
    return company
