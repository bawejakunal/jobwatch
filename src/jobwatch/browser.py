"""Playwright recipe schema (Phase 2). Imported today so config/docs stay stable."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class BrowserRecipe:
    name: str
    start: str
    job_links: str
    wait: str = "networkidle"
    title: str | None = None
    follow: list[str] = field(default_factory=list)
    pagination: str | None = None
    max_depth: int = 2
    max_pages: int = 20
    iframe: bool = True
    harvest_xhr: bool = True
    timeout_s: float = 45.0


def run_recipe(recipe: BrowserRecipe) -> None:
    raise NotImplementedError(
        "Playwright recipes ship in Phase 2 (`jobwatch[browser]`). "
        f"Recipe {recipe.name!r} is parsed but not executed. See PLAN.md §6."
    )
