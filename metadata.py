"""Look up TMDb metadata (titles, descriptions, pictures) for the playing file.

- Matches the recognised title to a TMDb series or movie
- Builds the metadata: titles, description, genres, rating, pictures
- Applies the user's corrections and caches the results
- Has no Home Assistant imports, so it can be tested on its own
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .parser import MEDIA_MOVIE, MEDIA_TVSHOW, MediaInfo
from .tmdb import FALLBACK_LANGUAGE, TmdbAuthError, TmdbClient, TmdbError

if TYPE_CHECKING:
    from .shared import MpcHcShared

_LOGGER = logging.getLogger(__name__)

STATUS_MATCHED = "matched"
STATUS_UNMATCHED = "unmatched"
STATUS_DISABLED = "disabled"  # no token saved
STATUS_ERROR = "error"  # TMDb could not be reached or rejected the token
STATUS_PENDING = "pending"  # lookup still running; shown by the entities only

KIND_TV = "tv"
KIND_MOVIE = "movie"

# Cache lifetimes in seconds. Misses expire quickly so that a fixed file name
# or a new TMDb entry is picked up without waiting long.
FOUND_TTL = 30 * 24 * 3600
MISSING_TTL = 24 * 3600

# Minimum similarity of a search result's title to accept it as a match
MIN_RATIO = 0.9


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """What TMDb knows about the playing file."""

    status: str
    tmdb_id: int | None = None
    tmdb_type: str | None = None  # "tv" or "movie"
    imdb_id: str | None = None
    title: str | None = None  # localised series or movie title
    episode_title: str | None = None
    overview: str | None = None  # episode (or movie) description
    genres: tuple[str, ...] = ()
    rating: float | None = None  # series or movie rating, 0-10
    release_date: str | None = None  # episode air date or movie release date
    poster_path: str | None = None
    backdrop_path: str | None = None
    still_path: str | None = None  # picture of the episode

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MediaMetadata:
        values = dict(data)
        values["genres"] = tuple(values.get("genres") or ())
        return cls(**values)


# ----- Matching -----


def normalize_title(text: str) -> str:
    """Lowercase a title and drop accents, punctuation and spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", without_accents)


def pick_best(
    results: list[dict[str, Any]], title: str, year: int | None, kind: str
) -> int | None:
    """Return the TMDb ID of the best search result, or None if none is close enough."""
    wanted = normalize_title(title)
    if not wanted:
        return None

    name_keys = ("name", "original_name") if kind == KIND_TV else ("title", "original_title")
    date_key = "first_air_date" if kind == KIND_TV else "release_date"

    best_id: int | None = None
    best_rank: tuple[bool, bool, float, float] | None = None
    for result in results:
        names = [normalize_title(result.get(key) or "") for key in name_keys]
        ratio = max(
            (difflib.SequenceMatcher(None, wanted, name).ratio() for name in names if name),
            default=0.0,
        )
        if ratio < MIN_RATIO:
            continue
        year_matches = bool(year) and (result.get(date_key) or "")[:4] == str(year)
        # Exact title first, then matching year, then similarity, then popularity
        rank = (ratio == 1.0, year_matches, ratio, result.get("popularity") or 0.0)
        if best_rank is None or rank > best_rank:
            best_id, best_rank = result.get("id"), rank
    return best_id


# ----- Override keys (used by the set_tmdb_id service) -----


def series_override_key(title: str) -> str:
    """Key of an override that applies to every file with this recognised title."""
    return f"series:{normalize_title(title)}"


def file_override_key(filepath: str) -> str:
    """Key of an override that applies to one file only."""
    return f"file:{filepath.lower()}"


# ----- Lookup -----


def _kind_of(info: MediaInfo) -> str | None:
    if info.media_type == MEDIA_TVSHOW:
        return KIND_TV
    if info.media_type == MEDIA_MOVIE:
        return KIND_MOVIE
    return None  # Unknown: guessing would only produce wrong matches


async def _async_load_image_config(client: TmdbClient, shared: MpcHcShared) -> None:
    """Fetch TMDb's image base URL and sizes once; fall back to defaults on failure."""
    if shared.has_image_config:
        return
    cached = shared.cache_get("images")
    if cached is None:
        config = await client.configuration()
        if not config:
            return
        cached = {
            "base": config.get("secure_base_url"),
            "poster_sizes": config.get("poster_sizes"),
            "backdrop_sizes": config.get("backdrop_sizes"),
            "still_sizes": config.get("still_sizes"),
        }
        shared.cache_set("images", cached, FOUND_TTL)
    shared.set_image_config(cached)


async def _async_search(client: TmdbClient, kind: str, info: MediaInfo) -> int | None:
    """Search TMDb, first with the year and then without it."""
    search = client.search_tv if kind == KIND_TV else client.search_movie
    for year in (info.year, None) if info.year else (None,):
        results = await search(info.title, year)
        tmdb_id = pick_best(results, info.title, info.year, kind)
        if tmdb_id:
            return tmdb_id
    return None


async def _async_resolve(
    client: TmdbClient, shared: MpcHcShared, info: MediaInfo, filepath: str, kind: str
) -> tuple[str, int] | None:
    """Find the TMDb item: a user override first, then the (cached) search."""
    override = shared.get_override(file_override_key(filepath)) or shared.get_override(
        series_override_key(info.title)
    )
    if override:
        return override["type"], int(override["id"])

    search_key = f"search:{kind}:{normalize_title(info.title)}:{info.year or ''}:{client.language}"
    cached = shared.cache_get(search_key)
    if cached is not None:
        return (kind, cached["id"]) if cached["id"] else None

    tmdb_id = await _async_search(client, kind, info)
    shared.cache_set(search_key, {"id": tmdb_id}, FOUND_TTL if tmdb_id else MISSING_TTL)
    return (kind, tmdb_id) if tmdb_id else None


def _genres(data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(g["name"] for g in data.get("genres") or [] if g.get("name"))


def _rating(data: dict[str, Any]) -> float | None:
    """Rating with one decimal, or None if nobody has voted yet."""
    if not data.get("vote_count"):
        return None
    return round(float(data.get("vote_average") or 0), 1)


async def _async_build_tv(client: TmdbClient, tmdb_id: int, info: MediaInfo) -> MediaMetadata:
    series = await client.tv(tmdb_id)
    if series is None:
        return MediaMetadata(status=STATUS_UNMATCHED)

    episode = None
    if info.season is not None and info.episode is not None:
        episode = await client.episode(tmdb_id, info.season, info.episode)

    overview = (episode or {}).get("overview") or series.get("overview")
    # Many entries have no description in the chosen language: use English
    if not overview and not client.language.lower().startswith("en"):
        if episode is not None:
            english = await client.episode(
                tmdb_id, info.season, info.episode, language=FALLBACK_LANGUAGE
            )
            overview = (english or {}).get("overview")
        if not overview:
            english_series = await client.tv(tmdb_id, language=FALLBACK_LANGUAGE)
            overview = (english_series or {}).get("overview")

    return MediaMetadata(
        status=STATUS_MATCHED,
        tmdb_id=tmdb_id,
        tmdb_type=KIND_TV,
        imdb_id=(series.get("external_ids") or {}).get("imdb_id") or None,
        title=series.get("name") or None,
        episode_title=(episode or {}).get("name") or None,
        overview=overview or None,
        genres=_genres(series),
        rating=_rating(series),
        release_date=(episode or {}).get("air_date") or series.get("first_air_date") or None,
        poster_path=series.get("poster_path"),
        backdrop_path=series.get("backdrop_path"),
        still_path=(episode or {}).get("still_path"),
    )


async def _async_build_movie(client: TmdbClient, tmdb_id: int) -> MediaMetadata:
    movie = await client.movie(tmdb_id)
    if movie is None:
        return MediaMetadata(status=STATUS_UNMATCHED)

    overview = movie.get("overview")
    if not overview and not client.language.lower().startswith("en"):
        english = await client.movie(tmdb_id, language=FALLBACK_LANGUAGE)
        overview = (english or {}).get("overview")

    return MediaMetadata(
        status=STATUS_MATCHED,
        tmdb_id=tmdb_id,
        tmdb_type=KIND_MOVIE,
        imdb_id=movie.get("imdb_id") or None,
        title=movie.get("title") or None,
        overview=overview or None,
        genres=_genres(movie),
        rating=_rating(movie),
        release_date=movie.get("release_date") or None,
        poster_path=movie.get("poster_path"),
        backdrop_path=movie.get("backdrop_path"),
    )


async def async_fetch_metadata(
    client: TmdbClient, shared: MpcHcShared, info: MediaInfo, filepath: str
) -> MediaMetadata:
    """Look up the metadata of the playing file. Never raises TMDb errors."""
    kind = _kind_of(info)
    if kind is None or not info.title:
        return MediaMetadata(status=STATUS_UNMATCHED)

    try:
        await _async_load_image_config(client, shared)

        target = await _async_resolve(client, shared, info, filepath, kind)
        if target is None:
            return MediaMetadata(status=STATUS_UNMATCHED)
        kind, tmdb_id = target

        cache_key = f"meta:{kind}:{tmdb_id}:{info.season}:{info.episode}:{client.language}"
        cached = shared.cache_get(cache_key)
        if cached is not None:
            return MediaMetadata.from_dict(cached)

        if kind == KIND_TV:
            metadata = await _async_build_tv(client, tmdb_id, info)
        else:
            metadata = await _async_build_movie(client, tmdb_id)
        if metadata.status == STATUS_MATCHED:
            shared.cache_set(cache_key, metadata.to_dict(), FOUND_TTL)
        return metadata
    except TmdbAuthError:
        # Log once per rejected token, not on every file
        if not shared.token_rejected:
            shared.token_rejected = True
            _LOGGER.warning(
                "TMDb rejected the saved token; check it in the integration's options"
            )
        return MediaMetadata(status=STATUS_ERROR)
    except TmdbError as err:
        _LOGGER.debug("TMDb lookup failed: %s", err)
        return MediaMetadata(status=STATUS_ERROR)