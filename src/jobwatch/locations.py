from __future__ import annotations

import re
from dataclasses import dataclass, field

from jobwatch.models import NormalizedJob

# ISO-3166-1 alpha-2 after normalization.
COUNTRY_ALIASES: dict[str, str] = {
    "us": "US",
    "usa": "US",
    "u.s": "US",
    "u.s.": "US",
    "u.s.a": "US",
    "u.s.a.": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "the united states": "US",
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "sg": "SG",
    "singapore": "SG",
    "ca": "CA",  # only when it means Canada as a country token, not California
    "can": "CA",
    "canada": "CA",
    "de": "DE",
    "germany": "DE",
    "fr": "FR",
    "france": "FR",
    "ie": "IE",
    "ireland": "IE",
    "in": "IN",
    "india": "IN",
    "au": "AU",
    "australia": "AU",
    "jp": "JP",
    "japan": "JP",
    "kr": "KR",
    "south korea": "KR",
    "korea": "KR",
    "nl": "NL",
    "netherlands": "NL",
    "es": "ES",
    "spain": "ES",
    "it": "IT",
    "italy": "IT",
    "br": "BR",
    "brazil": "BR",
    "mx": "MX",
    "mexico": "MX",
    "ae": "AE",
    "uae": "AE",
    "united arab emirates": "AE",
    "il": "IL",
    "israel": "IL",
    "se": "SE",
    "sweden": "SE",
    "ch": "CH",
    "switzerland": "CH",
    "at": "AT",
    "austria": "AT",
    "pl": "PL",
    "poland": "PL",
    "pt": "PT",
    "portugal": "PT",
    "cn": "CN",
    "china": "CN",
    "hk": "HK",
    "hong kong": "HK",
    "nz": "NZ",
    "new zealand": "NZ",
}

US_STATE_ABBREV = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il",
    "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt",
    "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri",
    "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
}

# Token / phrase → US. Checked as whole-word / phrase hits.
US_CITIES = {
    "san francisco", "sf", "bay area", "silicon valley", "new york", "nyc",
    "brooklyn", "manhattan", "seattle", "austin", "boston", "chicago", "atlanta",
    "denver", "boulder", "los angeles", "la", "san diego", "san jose",
    "palo alto", "mountain view", "sunnyvale", "cupertino", "redmond",
    "bellevue", "portland", "miami", "dallas", "houston", "phoenix",
    "philadelphia", "washington dc", "washington d.c", "washington d.c.",
    "arlington", "mclean", "reston", "herndon", "raleigh", "durham", "charlotte",
    "nashville", "salt lake city", "minneapolis", "detroit", "pittsburgh",
    "columbus", "indianapolis", "tampa", "orlando", "ann arbor",
}

# Foreign city / region names that should not be treated as US unless a US
# state or US country token is also present.
FOREIGN_REGIONS: dict[str, str] = {
    "emea": "EU",
    "europe": "EU",
    "european union": "EU",
    "eu only": "EU",
    "apac": "APAC",
    "asia pacific": "APAC",
    "asia-pacific": "APAC",
    "latam": "LATAM",
    "latin america": "LATAM",
}

FOREIGN_PLACES: dict[str, str] = {
    "london": "GB",
    "singapore": "SG",
    "paris": "FR",
    "toronto": "CA",
    "vancouver": "CA",
    "montreal": "CA",
    "dublin": "IE",
    "sydney": "AU",
    "melbourne": "AU",
    "munich": "DE",
    "berlin": "DE",
    "madrid": "ES",
    "barcelona": "ES",
    "tokyo": "JP",
    "seoul": "KR",
    "amsterdam": "NL",
    "zurich": "CH",
    "hong kong": "HK",
    "bengaluru": "IN",
    "bangalore": "IN",
    "hyderabad": "IN",
    "mumbai": "IN",
    "mexico city": "MX",
    "sao paulo": "BR",
    "são paulo": "BR",
    "tel aviv": "IL",
}

REMOTE_ONLY = re.compile(
    r"^(remote|remotely|anywhere|worldwide|global|work from home|wfh)(?:\s*[./-]?\s*(world)?wide)?$",
    re.I,
)
REMOTE_TOKEN = re.compile(r"\b(remote|wfh|work from home)\b", re.I)
US_PREFIX = re.compile(r"^(us|usa)([-,\s]|$)", re.I)
ICIMS_US = re.compile(r"^us-[a-z]{2}-", re.I)
STATE_ABBREV_TOKEN = re.compile(
    r"(?:^|[\s,/|(])(" + "|".join(sorted(US_STATE_ABBREV, key=len, reverse=True)) + r")(?:$|[\s,)/|])",
    re.I,
)

# "CA" as Canada vs California: California wins when next to a US city or as
# ", CA" / "CA," in a US-style address. Bare "CA" with no other signal is
# treated as California in US job boards (Ashby/Greenhouse style).
_WORD = re.compile(r"[A-Za-z.]+")


@dataclass(slots=True)
class JobLocation:
    text: str
    country: str | None = None
    remote: bool = False
    ambiguous: bool = False


@dataclass(slots=True)
class LocationSettings:
    mode: str = "include"  # include | exclude
    countries: list[str] = field(default_factory=lambda: ["US"])
    enabled: bool = True

    def normalized_countries(self) -> set[str]:
        out: set[str] = set()
        for raw in self.countries:
            code = normalize_country(raw)
            if code:
                out.add(code)
        return out


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    folded = re.sub(r"\s+", " ", value.strip().casefold())
    folded = folded.replace(".", "")
    if folded in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[folded]
    compact = folded.replace(" ", "")
    if compact in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[compact]
    if len(folded) == 2 and folded.isalpha():
        return folded.upper()
    return None


def parse_location_settings(raw: object | None, *, default_us: bool = True) -> LocationSettings:
    if raw is False or raw == "off":
        return LocationSettings(enabled=False, countries=[])
    if raw is None:
        return LocationSettings() if default_us else LocationSettings(enabled=False, countries=[])
    if isinstance(raw, list):
        countries = [str(x) for x in raw]
        return LocationSettings(mode="include", countries=countries or ["US"])
    if not isinstance(raw, dict):
        return LocationSettings()
    mode = str(raw.get("mode") or "include").lower()
    if mode not in {"include", "exclude"}:
        mode = "include"
    countries = [str(x) for x in (raw.get("countries") or raw.get("country") or ["US"])]
    if isinstance(raw.get("countries"), str):
        countries = [str(raw["countries"])]
    enabled = bool(raw.get("enabled", True))
    if raw.get("any") is True:
        enabled = False
    return LocationSettings(mode=mode, countries=countries, enabled=enabled)


def classify_text(text: str, *, structured_country: str | None = None) -> JobLocation:
    raw = (text or "").strip()
    country = normalize_country(structured_country)
    remote = bool(REMOTE_TOKEN.search(raw)) if raw else False
    if not raw:
        return JobLocation(text=text or "", country=country, remote=remote, ambiguous=country is None)

    folded = re.sub(r"\s+", " ", raw.casefold()).strip()
    if country is None:
        country = _country_from_text(folded, raw)

    if country is None and REMOTE_ONLY.match(folded):
        return JobLocation(text=raw, country=None, remote=True, ambiguous=True)

    if country is None and remote and not _has_geographic_hint(folded):
        return JobLocation(text=raw, country=None, remote=True, ambiguous=True)

    return JobLocation(text=raw, country=country, remote=remote, ambiguous=False)


def _country_from_text(folded: str, original: str) -> str | None:
    if US_PREFIX.match(original) or ICIMS_US.match(original):
        return "US"
    # Prefer explicit country phrases before cities so "Remote - United States" wins.
    for alias, code in sorted(COUNTRY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if len(alias) <= 2:
            if re.search(rf"\b{re.escape(alias)}\b", folded):
                # "in" / "ca" / "us" are too easy to misfire; handle below.
                if alias in {"in", "ca", "us", "de", "il", "or", "me"}:
                    continue
                return code
        elif re.search(rf"\b{re.escape(alias)}\b", folded):
            return code
    if re.search(r"\bunited states\b|\bu\.?s\.?a\.?\b", folded):
        return "US"
    if re.search(r"\bu\.s\.?\b", folded) or re.search(r"\busa\b", folded):
        return "US"
    if re.search(r"(?:^|[\s,/(|-])us(?:$|[\s,)/|-])", folded):
        return "US"

    has_us_state = _has_us_state(folded, original)
    for place, code in {**FOREIGN_REGIONS, **FOREIGN_PLACES}.items():
        if re.search(rf"\b{re.escape(place)}\b", folded) and not has_us_state:
            return code

    if has_us_state:
        return "US"
    for city in sorted(US_CITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", folded):
            return "US"
    return None


def _has_us_state(folded: str, original: str) -> bool:
    for name in US_STATE_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", folded):
            return True
    # ", CA" / "CA," / "(CA)" / "US-CA-" / "CA -"
    if STATE_ABBREV_TOKEN.search(original):
        return True
    return False


def _has_geographic_hint(folded: str) -> bool:
    if any(re.search(rf"\b{re.escape(a)}\b", folded) for a in COUNTRY_ALIASES if len(a) > 2):
        return True
    if _has_us_state(folded, folded):
        return True
    if any(re.search(rf"\b{re.escape(c)}\b", folded) for c in US_CITIES):
        return True
    if any(re.search(rf"\b{re.escape(c)}\b", folded) for c in FOREIGN_PLACES):
        return True
    return False


def ensure_places(job: NormalizedJob) -> list[JobLocation]:
    if job.places:
        return job.places
    if job.location:
        job.places = [classify_text(part) for part in _split_location_blob(job.location)]
    else:
        job.places = []
    return job.places


def _split_location_blob(text: str) -> list[str]:
    """Split 'San Francisco, CA, New York, NY' into two places when possible."""
    # City, ST, City, ST
    paired = re.findall(r"([^,]+,\s*[A-Z]{2})\s*(?:,|$)", text)
    if len(paired) >= 2:
        return [p.strip().rstrip(",") for p in paired]
    if " | " in text:
        return [p.strip() for p in text.split("|") if p.strip()]
    if " / " in text:
        return [p.strip() for p in text.split("/") if p.strip()]
    return [text.strip()]


def location_keeps(places: list[JobLocation], settings: LocationSettings) -> tuple[bool, bool]:
    """Return (keep, any_ambiguous_kept)."""
    if not settings.enabled:
        return True, False
    wanted = settings.normalized_countries()
    if not wanted:
        return True, False

    if not places:
        return True, True

    matched = False
    ambiguous = False
    foreign_only = True
    for place in places:
        if place.ambiguous or (place.country is None and not place.text):
            ambiguous = True
            foreign_only = False
            continue
        if place.country is None and place.remote:
            ambiguous = True
            foreign_only = False
            continue
        if place.country is None:
            # Unknown office string with no country — keep and flag.
            ambiguous = True
            foreign_only = False
            continue
        if place.country in wanted:
            matched = True
            foreign_only = False
        # else: clearly some other country
    if settings.mode == "exclude":
        return (not matched), False
    # include: keep US hits and ambiguous remotes / unknowns; drop foreign-only
    if matched:
        return True, False
    if ambiguous and not (foreign_only and not ambiguous):
        return True, True
    return False, False


def apply_location_filter(jobs: list[NormalizedJob], settings: LocationSettings) -> list[NormalizedJob]:
    if not settings.enabled:
        return jobs
    kept: list[NormalizedJob] = []
    for job in jobs:
        places = [classify_text(p.text, structured_country=p.country) if p.country or p.text else p for p in ensure_places(job)]
        # Re-classify text even when adapters set a country, so "Remote - US" works.
        refined: list[JobLocation] = []
        for place in places:
            refined.append(classify_text(place.text, structured_country=place.country))
        job.places = refined
        keep, ambiguous = location_keeps(refined, settings)
        if not keep:
            continue
        job.location, job.location_ambiguous = format_locations(refined, settings)
        if ambiguous:
            job.location_ambiguous = True
        kept.append(job)
    return kept


def format_locations(places: list[JobLocation], settings: LocationSettings) -> tuple[str, bool]:
    wanted = settings.normalized_countries()
    shown: list[str] = []
    hidden: list[str] = []
    ambiguous = False
    for place in places:
        text = place.text.strip()
        if not text:
            continue
        is_wanted = place.country in wanted if place.country else False
        if place.ambiguous or (place.country is None and settings.mode == "include"):
            ambiguous = True
            label = text if text.lower() not in {"remote", "remotely"} else "Remote (unspecified)"
            if place.ambiguous or place.country is None:
                if "unspecified" not in label.lower() and place.ambiguous:
                    label = f"{text} (unspecified)" if text.lower() != "remote" else "Remote (unspecified)"
            if label not in shown:
                shown.append(label)
            continue
        if settings.mode == "include" and wanted:
            if is_wanted:
                if text not in shown:
                    shown.append(text)
            elif text not in hidden:
                hidden.append(text)
        else:
            if text not in shown:
                shown.append(text)
    display = ", ".join(shown) if shown else (places[0].text if places else "")
    if hidden and settings.mode == "include":
        display = f"{display} (+ {', '.join(hidden)})" if display else ", ".join(hidden)
    return display, ambiguous
