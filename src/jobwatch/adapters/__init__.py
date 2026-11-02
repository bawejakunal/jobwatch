from __future__ import annotations

from jobwatch.adapters.amazon import AmazonAdapter
from jobwatch.adapters.apple import AppleAdapter
from jobwatch.adapters.ashby import AshbyAdapter
from jobwatch.adapters.atlassian import AtlassianAdapter
from jobwatch.adapters.base import Adapter, AdapterError, NeedsBrowserError, apply_identity
from jobwatch.adapters.eightfold import EightfoldAdapter
from jobwatch.adapters.google import GoogleAdapter
from jobwatch.adapters.greenhouse import GreenhouseAdapter
from jobwatch.adapters.icims import IcimsAdapter
from jobwatch.adapters.lever import LeverAdapter
from jobwatch.adapters.linkedin import LinkedInAdapter
from jobwatch.adapters.optiver import OptiverAdapter
from jobwatch.adapters.rippling import RipplingAdapter
from jobwatch.adapters.smartrecruiters import SmartRecruitersAdapter
from jobwatch.adapters.workday import WorkdayAdapter
from jobwatch.adapters.yc import YcAdapter

ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        GreenhouseAdapter(),
        LeverAdapter(),
        AshbyAdapter(),
        WorkdayAdapter(),
        SmartRecruitersAdapter(),
        IcimsAdapter(),
        YcAdapter(),
        EightfoldAdapter(),
        RipplingAdapter(),
        AmazonAdapter(),
        AppleAdapter(),
        GoogleAdapter(),
        LinkedInAdapter(),
        AtlassianAdapter(),
        OptiverAdapter(),
    )
}


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        raise AdapterError(f"unknown ATS {name!r}") from exc


__all__ = [
    "ADAPTERS",
    "Adapter",
    "AdapterError",
    "NeedsBrowserError",
    "apply_identity",
    "get_adapter",
]
