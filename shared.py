"""Data shared by all players: TMDb settings, corrections and cache.

- Saves the TMDb token, the language and the user's corrections in .storage
- Caches TMDb answers with an expiry time
- Builds picture URLs from TMDb's image configuration
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
# Cache writes are batched so a lookup does not hit the disk every time
CACHE_SAVE_DELAY = 30
MAX_CACHE_ENTRIES = 2000

DEFAULT_IMAGE_BASE = "https://image.tmdb.org/t/p/"
# Picture size used for each kind of image
IMAGE_SIZES = {"poster": "w500", "backdrop": "w780", "still": "w300"}
IMAGE_SIZE_KEYS = {
    "poster": "poster_sizes",
    "backdrop": "backdrop_sizes",
    "still": "still_sizes",
}


class MpcHcShared:
    """Settings and caches that belong to the whole integration, not one player."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._settings_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.settings"
        )
        self._cache_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.tmdb_cache"
        )
        self._lock = asyncio.Lock()
        self._loaded = False
        self._overrides: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._images: dict[str, Any] | None = None
        self.tmdb_token = ""
        self.tmdb_language = ""
        # Set when TMDb rejects the token, so the warning is logged only once
        self.token_rejected = False

    async def async_load(self) -> None:
        """Load the stored data. Safe to call from every entry; loads once."""
        async with self._lock:
            if self._loaded:
                return
            settings = await self._settings_store.async_load() or {}
            self.tmdb_token = settings.get("tmdb_token", "")
            self.tmdb_language = settings.get("tmdb_language", "")
            self._overrides = settings.get("overrides", {})

            cache = await self._cache_store.async_load() or {}
            now = time.time()
            # Drop entries that expired while Home Assistant was off
            self._cache = {k: v for k, v in cache.items() if v.get("expires", 0) > now}
            self._loaded = True

    # ----- Settings and overrides -----

    def _settings_data(self) -> dict[str, Any]:
        return {
            "tmdb_token": self.tmdb_token,
            "tmdb_language": self.tmdb_language,
            "overrides": self._overrides,
        }

    async def async_update_settings(self, token: str, language: str) -> None:
        self.tmdb_token = token
        self.tmdb_language = language
        self.token_rejected = False
        await self._settings_store.async_save(self._settings_data())

    def get_override(self, key: str) -> dict[str, Any] | None:
        return self._overrides.get(key)

    async def async_set_override(self, key: str, value: dict[str, Any] | None) -> None:
        """Save a user's correction, or remove it when value is None."""
        if value is None:
            self._overrides.pop(key, None)
        else:
            self._overrides[key] = value
        await self._settings_store.async_save(self._settings_data())

    # ----- Cache -----

    def cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry["expires"] <= time.time():
            del self._cache[key]
            return None
        return entry["value"]

    def cache_set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            # Make room by dropping the entry that expires first
            oldest = min(self._cache, key=lambda k: self._cache[k]["expires"])
            del self._cache[oldest]
        self._cache[key] = {"expires": time.time() + ttl, "value": value}
        self._cache_store.async_delay_save(lambda: self._cache, CACHE_SAVE_DELAY)

    # ----- Pictures -----

    @property
    def has_image_config(self) -> bool:
        return self._images is not None

    def set_image_config(self, config: dict[str, Any]) -> None:
        self._images = config

    def image_url(self, path: str | None, kind: str) -> str | None:
        """Build the picture URL; kind is "poster", "backdrop" or "still"."""
        if not path:
            return None
        config = self._images or {}
        base = config.get("base") or DEFAULT_IMAGE_BASE
        wanted = IMAGE_SIZES[kind]
        sizes = config.get(IMAGE_SIZE_KEYS[kind])
        # Use the wanted size if TMDb offers it, otherwise the original
        size = wanted if not sizes or wanted in sizes else "original"
        return f"{base}{size}{path}"


def get_shared(hass: HomeAssistant) -> MpcHcShared:
    """Return the shared object, creating it on first use.

    Call async_load() on it before reading the settings.
    """
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = MpcHcShared(hass)
    return hass.data[DOMAIN]