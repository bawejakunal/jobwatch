"""In-memory SQLite FTS5 matcher for software-role phrases.

Phrases are plain text. This module turns them into column-scoped MATCH
queries. Callers do not write FTS query syntax.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence

# Step 1. Checked before the business list so these survive words like "gtm".
ROLE_TITLE_KEEP = [
    "gtm engineer",
    "go to market engineer",
    "solutions engineer",
    "solution engineer",
    "support engineer",
    "technical support engineer",
    "forward deployed engineer",
]

# Step 2. Title only. A hit drops the posting even when the body mentions engineers.
ROLE_TITLE_DROP = [
    "sales",
    "account executive",
    "account manager",
    "ae",
    "business development",
    "sdr",
    "bdr",
    "gtm",
    "go to market",
    "marketing",
    "marketer",
    "recruiter",
    "sourcer",
    "talent acquisition",
    "people ops",
    "people operations",
    "human resources",
    "designer",
    "product manager",
    "product marketing",
    "chief of staff",
    "executive assistant",
    "counsel",
    "attorney",
    "accountant",
    "controller",
    "data scientist",
    "data analyst",
    "mechanical engineer",
    "electrical engineer",
    "civil engineer",
    "chemical engineer",
    "strategist",
    "clinical",
    "customer success",
]

# Step 3. Title, and the department when the department is not a business function.
ROLE_TITLE_SOFTWARE = [
    "software engineer",
    "software developer",
    "founding engineer",
    "member of technical staff",
    "mts",
    "engineer",
    "developer",
    "programmer",
    "swe",
    "sre",
    "sdet",
    "devops",
    "site reliability",
    "full stack",
    "front end",
    "frontend",
    "back end",
    "backend",
    "machine learning",
    "firmware",
    "embedded",
    "software",
    "data engineer",
    "research engineer",
    "qa engineer",
    "test engineer",
]

# Step 5. Body only, and only when the title did not decide. No bare "engineer".
ROLE_BODY_SOFTWARE = [
    "software engineer",
    "founding engineer",
    "member of technical staff",
    "full stack engineer",
    "frontend engineer",
    "front end engineer",
    "backend engineer",
    "back end engineer",
    "machine learning engineer",
    "research engineer",
    *ROLE_TITLE_KEEP,
]

# A single token in this set becomes a prefix query (engineer* matches engineering).
_PREFIX_WORDS = frozenset({"engineer", "developer", "programmer", "recruiter", "designer"})

_COLUMNS = frozenset({"title", "department", "body"})


def default_role_title_keep() -> list[str]:
    return list(ROLE_TITLE_KEEP)


def default_role_title_drop() -> list[str]:
    return list(ROLE_TITLE_DROP)


def default_role_title_software() -> list[str]:
    return list(ROLE_TITLE_SOFTWARE)


def default_role_body_software() -> list[str]:
    return list(ROLE_BODY_SOFTWARE)


def prepare_text(text: str | None) -> str:
    if not text:
        return ""
    cleaned = str(text).casefold()
    cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-").replace("\u2011", "-")
    return " ".join(cleaned.split())


def _tokens(phrase: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for char in prepare_text(phrase):
        if char.isalnum():
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def _quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def fts_term(phrase: str) -> str | None:
    """One MATCH term. Multi-word phrases prefix the last token."""
    tokens = _tokens(phrase)
    if not tokens:
        return None
    if len(tokens) == 1:
        quoted = _quote(tokens[0])
        if tokens[0] in _PREFIX_WORDS:
            return quoted + "*"
        return quoted
    return _quote(" ".join(tokens)) + "*"


def match_query(column: str, phrases: Sequence[str]) -> str | None:
    if column not in _COLUMNS:
        raise ValueError(f"unknown FTS column {column}")
    terms: list[str] = []
    for phrase in phrases:
        term = fts_term(phrase)
        if term:
            terms.append(f"{column} : {term}")
    if not terms:
        return None
    return " OR ".join(terms)


class PostingIndex:
    """One reused :memory: FTS5 table. The scan is single-threaded; a lock keeps tests safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._con = sqlite3.connect(":memory:", check_same_thread=False)
        self._con.execute(
            "CREATE VIRTUAL TABLE posting USING fts5("
            "title, department, body, tokenize = 'unicode61 remove_diacritics 2')"
        )
        self._queries: dict[tuple[str, tuple[str, ...]], str | None] = {}

    def load(self, title: str, department: str | None, body: str) -> None:
        self._con.execute("DELETE FROM posting")
        self._con.execute(
            "INSERT INTO posting(title, department, body) VALUES (?, ?, ?)",
            (prepare_text(title), prepare_text(department), prepare_text(body)),
        )

    def matches(self, column: str, phrases: Sequence[str]) -> bool:
        key = (column, tuple(phrases))
        if key not in self._queries:
            self._queries[key] = match_query(column, phrases)
        query = self._queries[key]
        if not query:
            return False
        row = self._con.execute(
            "SELECT 1 FROM posting WHERE posting MATCH ? LIMIT 1",
            (query,),
        ).fetchone()
        return row is not None

    def __enter__(self) -> PostingIndex:
        self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


INDEX = PostingIndex()
