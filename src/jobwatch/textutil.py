from __future__ import annotations

from bs4 import BeautifulSoup


def html_to_text(html: str | None) -> str | None:
    if not html:
        return None
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    text = " ".join(text.split())
    return text or None
