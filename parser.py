"""Recognise title, season, episode and year from file and folder names.

- Splits names into words and cuts the title at the first release marker
- Understands S05E14, 1x05 and double episodes
- Falls back to the folder name for meaningless file names
- Has no Home Assistant imports, so it can be tested on its own
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

MEDIA_TVSHOW = "tvshow"
MEDIA_MOVIE = "movie"
MEDIA_UNKNOWN = "unknown"

# Extensions that are stripped from file names. Only known video extensions are
# removed so that dots inside a title are never mistaken for an extension.
_EXT_RE = re.compile(
    r"\.(?:mkv|avi|mp4|m4v|mov|wmv|flv|webm|mpg|mpeg|ts|m2ts|vob|ogm|divx|3gp|amv|asf|gif|ogv|qt|rm|rmvb)$",
    re.IGNORECASE,
)

# S05E14, S02E03E04, S02E03-E04
_EPISODE_RE = re.compile(r"^s(\d{1,2})e(\d{1,3})(?:-?e(\d{1,3}))?$", re.IGNORECASE)
# 1x05
_EPISODE_X_RE = re.compile(r"^(\d{1,2})x(\d{1,3})$", re.IGNORECASE)
# S05 (season only, typical for season folders)
_SEASON_RE = re.compile(r"^s(\d{1,2})$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_RESOLUTION_RE = re.compile(r"^(?:\d{3,4}[pi]|4k|uhd)$", re.IGNORECASE)

# Release markers. Everything from the first marker on is technical noise.
_SOURCES = frozenset(
    {
        "dvdrip", "bdrip", "brrip", "bluray", "blu-ray", "webrip", "web-dl", "webdl", "hdtv", "dvdscr", "hdrip", "remux", "dvd",
    }
)
# Streaming services act as markers but carry no information we keep
_SERVICES = frozenset({"amzn", "nf", "dsnp", "hmax", "atvp"})
_CODECS = frozenset(
    {
        "x264", "x265", "h264", "h265", "hevc", "xvid", "divx", "avc", "aac", "ac3", "eac3", "dts", "truehd", "atmos", "10bit", "hdr", "hdr10", "proper", "repack", "internal",
    }
)
# Language tags seen in scene releases, mapped to ISO 639-1 codes
_LANGUAGES = {
    "hun": "hu", "hundub": "hu", "hunsub": "hu",
    "eng": "en", "engdub": "en", "engsub": "en",
}

# Words that stay lowercase in a title (except as the first word)
_SMALL_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "vs"}
)


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Information recognised from a file name."""

    title: str = ""  # series title for TV shows, movie title for movies
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None  # last episode of a double episode
    year: int | None = None
    media_type: str = MEDIA_UNKNOWN
    resolution: str | None = None  # e.g. "1080p"
    source: str | None = None  # e.g. "webrip"
    languages: tuple[str, ...] = ()  # ISO 639-1 codes, e.g. ("hu", "en")

    @property
    def episode_code(self) -> str | None:
        """Return a code like S05E14, S02E03-E04 or S05 (None without a season)."""
        if self.season is None:
            return None
        code = f"S{self.season:02d}"
        if self.episode is not None:
            code += f"E{self.episode:02d}"
            if self.episode_end is not None:
                code += f"-E{self.episode_end:02d}"
        return code


def _kind(token: str) -> str | None:
    """Classify a single token, or return None if it is an ordinary word."""
    t = token.lower()
    if _EPISODE_RE.match(t) or _EPISODE_X_RE.match(t):
        return "episode"
    if _SEASON_RE.match(t):
        return "season"
    if _RESOLUTION_RE.match(t):
        return "resolution"
    if t in _SOURCES:
        return "source"
    if t in _SERVICES:
        return "service"
    if t in _CODECS:
        return "codec"
    if t in _LANGUAGES:
        return "language"
    return None


def _marker_kind(token: str) -> str | None:
    """Like _kind, but also recognise ``marker-GROUP`` (e.g. ``x264-GRP``)."""
    kind = _kind(token)
    if kind or "-" not in token:
        return kind
    return _kind(token.split("-", 1)[0])


def _tokenize(name: str) -> list[str]:
    """Split a file or folder name into word tokens."""
    stem = _EXT_RE.sub("", name.strip())
    tokens = [t.strip("()[]{}") for t in re.split(r"[.\s_]+", stem)]
    tokens = [t for t in tokens if t and t != "-"]

    # Scene releases are fully lowercase and may start with "<group>-", e.g.
    # "fulcrum-the.lord.of...s03e01". Drop the group prefix, but only when an
    # episode marker is present so titles like "spider-man.2002" stay intact.
    if (
        stem == stem.lower()
        and len(tokens) > 1
        and "-" in tokens[0]
        and any(_marker_kind(t) == "episode" for t in tokens[1:])
    ):
        remainder = tokens[0].split("-", 1)[1]
        if remainder:
            tokens[0] = remainder
        else:
            tokens.pop(0)
    return tokens


def _season_episode(tokens: list[str]) -> tuple[int | None, int | None, int | None]:
    """Find season, episode and the end of a double episode in the tokens."""
    season_only: int | None = None
    for token in tokens:
        low = token.lower()
        # Also try the part before a hyphen to handle "s05e14-group"
        for candidate in (low, low.split("-", 1)[0]):
            match = _EPISODE_RE.match(candidate)
            if match:
                end = int(match.group(3)) if match.group(3) else None
                return int(match.group(1)), int(match.group(2)), end
            match = _EPISODE_X_RE.match(candidate)
            if match:
                return int(match.group(1)), int(match.group(2)), None
        if season_only is None:
            match = _SEASON_RE.match(low)
            if match:
                season_only = int(match.group(1))
    return season_only, None, None


def _media_type(season: int | None, year: int | None) -> str:
    if season is not None:
        return MEDIA_TVSHOW
    if year is not None:
        return MEDIA_MOVIE
    return MEDIA_UNKNOWN


def _parse_name(name: str) -> MediaInfo:
    """Parse one file or folder name, keeping the original title casing."""
    tokens = _tokenize(name)
    if not tokens:
        return MediaInfo()

    # The title needs at least one word, so a marker at index 0 is ignored.
    limit = next(
        (i for i, t in enumerate(tokens) if i > 0 and _marker_kind(t)), len(tokens)
    )

    # A year inside the title area ends the title. The last one wins so that
    # titles containing a year ("Blade Runner 2049 2017") work.
    year_idx: int | None = None
    for i in range(1, limit):
        if _YEAR_RE.match(tokens[i]):
            year_idx = i
    cut = year_idx if year_idx is not None else limit

    season, episode, episode_end = _season_episode(tokens)
    year = int(tokens[year_idx]) if year_idx is not None else None

    resolution: str | None = None
    source: str | None = None
    languages: list[str] = []
    for token in tokens[cut:]:
        parts = [token, *token.split("-")] if "-" in token else [token]
        for part in parts:
            kind = _kind(part)
            if kind == "resolution" and resolution is None:
                resolution = part.lower()
            elif kind == "source" and source is None:
                source = part.lower()
            elif kind == "language":
                code = _LANGUAGES[part.lower()]
                if code not in languages:
                    languages.append(code)

    return MediaInfo(
        title=" ".join(tokens[:cut]).strip(" -"),
        season=season,
        episode=episode,
        episode_end=episode_end,
        year=year,
        media_type=_media_type(season, year),
        resolution=resolution,
        source=source,
        languages=tuple(languages),
    )


def _smart_title(title: str) -> str:
    """Capitalise a lowercase title, keeping small words lowercase."""
    words = []
    for i, word in enumerate(title.lower().split()):
        if i > 0 and word in _SMALL_WORDS:
            words.append(word)
        else:
            # Capitalise each hyphen part: "spider-man" -> "Spider-Man"
            words.append("-".join(p[:1].upper() + p[1:] for p in word.split("-")))
    return " ".join(words)


def _fix_case(title: str, folder_title: str) -> str:
    """Improve the casing of an all-lowercase title.

    Titles that already contain capitals are kept as they are. A lowercase
    title is replaced by the folder's casing when the folder holds the same
    title, otherwise it is title-cased.
    """
    if not title or title != title.lower():
        return title
    if folder_title and folder_title != title and folder_title.lower() == title:
        return folder_title
    return _smart_title(title)


def parse_media(file: str, filedir: str = "") -> MediaInfo:
    """Recognise media information from a file name and its directory.

    ``file`` is the file name only, ``filedir`` the full directory path as
    reported by MPC-HC (Windows style, but both separators are accepted).
    """
    info = _parse_name(file)

    folder_name = ""
    if filedir:
        folder_name = re.split(r"[\\/]+", filedir.strip().rstrip("\\/"))[-1]
    # A bare drive root such as "W:" is not a useful folder name
    folder = (
        _parse_name(folder_name)
        if folder_name and not folder_name.endswith(":")
        else MediaInfo()
    )

    file_is_bare = info.season is None and info.episode is None and info.year is None
    folder_has_meta = (
        folder.season is not None
        or folder.episode is not None
        or folder.year is not None
    )

    # A generic file name ("video.mkv") inside a descriptive folder: trust the folder
    title = info.title
    if not title or (file_is_bare and folder_has_meta and folder.title):
        title = folder.title

    # Missing details are only borrowed from the folder when both describe
    # the same title, so unrelated folders never leak into the result
    same_title = bool(title) and title.lower() == folder.title.lower()

    season, episode, episode_end = info.season, info.episode, info.episode_end
    year = info.year
    resolution, source, languages = info.resolution, info.source, info.languages
    if same_title:
        if season is None and episode is None:
            season, episode, episode_end = (
                folder.season,
                folder.episode,
                folder.episode_end,
            )
        year = year if year is not None else folder.year
        resolution = resolution or folder.resolution
        source = source or folder.source
        languages = languages or folder.languages

    return replace(
        info,
        title=_fix_case(title, folder.title),
        season=season,
        episode=episode,
        episode_end=episode_end,
        year=year,
        media_type=_media_type(season, year),
        resolution=resolution,
        source=source,
        languages=languages,
    )
