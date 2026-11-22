"""Minimal async client for the TMDb v3 API.

- Searches series and movies, reads series, episode and movie details
- Sends the token as a v3 API key or as a v4 bearer token
- Waits and retries once when TMDb asks to slow down
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp

API_URL = "https://api.themoviedb.org/3"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
FALLBACK_LANGUAGE = "en-US"
# Longest pause honoured when TMDb asks us to slow down (HTTP 429)
MAX_RETRY_WAIT = 10

# A v3 API key is 32 hex characters; anything else is treated as a v4 read
# access token and sent as a bearer token
_API_KEY_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


class TmdbError(Exception):
    """TMDb could not be reached or answered with an error."""


class TmdbAuthError(TmdbError):
    """TMDb rejected the token."""


def _retry_after(resp: aiohttp.ClientResponse) -> int:
    """Seconds TMDb asks us to wait after a 429 answer."""
    try:
        return max(1, int(resp.headers.get("Retry-After", "1")))
    except ValueError:
        return 1


class TmdbClient:
    """Thin wrapper around the few TMDb endpoints the integration needs."""

    def __init__(self, session: aiohttp.ClientSession, token: str, language: str) -> None:
        self._session = session
        self._token = token.strip()
        self.language = language or FALLBACK_LANGUAGE

    async def _get(self, path: str, **params: Any) -> dict[str, Any] | None:
        """GET an endpoint. Returns None if the item does not exist (404)."""
        query = {key: value for key, value in params.items() if value is not None}
        headers = {"accept": "application/json"}
        if _API_KEY_RE.match(self._token):
            query["api_key"] = self._token
        else:
            headers["Authorization"] = f"Bearer {self._token}"

        for attempt in (1, 2):
            retry_after = 0
            try:
                async with self._session.get(
                    f"{API_URL}{path}",
                    params=query,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status == 401:
                        raise TmdbAuthError("TMDb rejected the token")
                    if resp.status == 404:
                        return None
                    if resp.status == 429 and attempt == 1:
                        retry_after = min(_retry_after(resp), MAX_RETRY_WAIT)
                    else:
                        resp.raise_for_status()
                        return await resp.json()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise TmdbError(str(err) or type(err).__name__) from err
            # Rate limited: wait as asked, then try once more
            await asyncio.sleep(retry_after)
        raise TmdbError("TMDb did not answer")

    async def check_token(self) -> bool:
        """Return True if TMDb accepts the token."""
        try:
            data = await self._get("/authentication")
        except TmdbAuthError:
            return False
        return bool(data and data.get("success"))

    async def configuration(self) -> dict[str, Any] | None:
        """Return the image configuration (base URL and available sizes)."""
        data = await self._get("/configuration")
        return (data or {}).get("images")

    async def search_tv(self, title: str, year: int | None = None) -> list[dict[str, Any]]:
        data = await self._get(
            "/search/tv",
            query=title,
            language=self.language,
            first_air_date_year=year,
            include_adult="false",
        )
        return (data or {}).get("results", [])

    async def search_movie(self, title: str, year: int | None = None) -> list[dict[str, Any]]:
        data = await self._get(
            "/search/movie",
            query=title,
            language=self.language,
            year=year,
            include_adult="false",
        )
        return (data or {}).get("results", [])

    async def tv(self, tv_id: int, language: str | None = None) -> dict[str, Any] | None:
        """Series details, including the external IDs (IMDb)."""
        return await self._get(
            f"/tv/{tv_id}",
            language=language or self.language,
            append_to_response="external_ids",
        )

    async def episode(
        self, tv_id: int, season: int, episode: int, language: str | None = None
    ) -> dict[str, Any] | None:
        return await self._get(
            f"/tv/{tv_id}/season/{season}/episode/{episode}",
            language=language or self.language,
        )

    async def movie(self, movie_id: int, language: str | None = None) -> dict[str, Any] | None:
        return await self._get(f"/movie/{movie_id}", language=language or self.language)