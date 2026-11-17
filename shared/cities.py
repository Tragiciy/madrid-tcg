"""
shared/cities.py — the cities the calendar covers. The single source of truth:
locator searches, store and event city assignment, the per-city API
(public/api/v1/cities.json is generated from this list) and the SEO pages.

To add a city, add a City here. Its id becomes the URL segment
(/api/v1/<id>/, /<id>/, /en/<id>/), so it is never changed once published.

Python rather than data/cities.json: scraper subprocesses and the pipeline
import it with no file I/O (shared/ modules must not read files on import),
the fields are typed and checked when the module loads, and non-Python
clients already get a JSON view in the published cities.json.

No network requests and no I/O on import.
"""

import math
from dataclasses import dataclass

COUNTRY_NAMES = {"ES": "Spain"}
SUPPORTED_TIMEZONE = "Europe/Madrid"


@dataclass(frozen=True)
class City:
    id: str            # URL segment and registry `city`; never renamed
    name: str          # display name, also used in page copy
    timezone: str      # IANA zone of the city's calendar dates
    lat: float         # search centre
    lng: float
    radius_km: int     # locator search radius around the centre
    country: str       # ISO 3166-1 alpha-2
    # Free-text searches for locators that ignore coordinates (GEM). Results
    # are still filtered by distance from the centre.
    search_terms: tuple[str, ...]

    @property
    def location_name(self) -> str:
        """'Madrid, Spain' — what a locator's place search box would show."""
        return f"{self.name}, {COUNTRY_NAMES[self.country]}"

    def contains(self, lat, lng) -> bool:
        """True when the point lies inside the city's search radius."""
        return distance_km(self.lat, self.lng, float(lat), float(lng)) <= self.radius_km


CITIES: tuple[City, ...] = (
    City(id="madrid", name="Madrid", timezone="Europe/Madrid",
         lat=40.4168, lng=-3.7038, radius_km=50, country="ES",
         search_terms=("Madrid",)),
    City(id="barcelona", name="Barcelona", timezone="Europe/Madrid",
         lat=41.3874, lng=2.1686, radius_km=30, country="ES",
         search_terms=("Barcelona",)),
)

# The city the site root (/) shows, and the city of records that predate
# multi-city support (stores and history events without a `city`).
DEFAULT_CITY = "madrid"

BY_ID: dict[str, City] = {c.id: c for c in CITIES}


def get(city_id: str) -> City:
    """The City with this id; KeyError for an unknown id (never guess)."""
    return BY_ID[city_id]


def default() -> City:
    return BY_ID[DEFAULT_CITY]


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance (haversine)."""
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _check(cities: tuple[City, ...]) -> None:
    ids = [c.id for c in cities]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate city ids: {ids}")
    if DEFAULT_CITY not in ids:
        raise ValueError(f"DEFAULT_CITY {DEFAULT_CITY!r} is not a configured city")
    for c in cities:
        if c.country not in COUNTRY_NAMES:
            raise ValueError(f"{c.id}: add {c.country!r} to COUNTRY_NAMES")
        if not c.search_terms:
            raise ValueError(f"{c.id}: needs at least one search term")
        # The pipeline renders times and buckets weeks in one zone
        # (pipeline.config.TZ, locator_common.TZ). A city elsewhere needs
        # per-city time handling first; refuse rather than publish wrong dates.
        if c.timezone != SUPPORTED_TIMEZONE:
            raise ValueError(f"{c.id}: timezone {c.timezone} is not supported yet "
                             f"(only {SUPPORTED_TIMEZONE})")


_check(CITIES)
