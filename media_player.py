"""Media player platform: the main MPC-HC entity.

- Playback, seek, volume and playlist control
- Series or movie title, second line (episode code and title) and poster
- Recognised and TMDb details as attributes
- Services: send_command and set_tmdb_id
"""

from __future__ import annotations

from datetime import datetime
import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.helpers import entity_platform

from .api import STATE_PAUSED, STATE_PLAYING
from .const import (
    ATTR_COMMAND,
    ATTR_SCOPE,
    ATTR_TMDB_ID,
    ATTR_TMDB_MEDIA_TYPE,
    SCOPE_FILE,
    SCOPE_SERIES,
    SERVICE_SEND_COMMAND,
    SERVICE_SET_TMDB_ID,
    Cmd,
)
from .coordinator import MpcHcConfigEntry
from .entity import MpcHcEntity
from .metadata import STATUS_MATCHED, STATUS_PENDING, MediaMetadata
from .parser import MEDIA_MOVIE, MEDIA_TVSHOW
from .shared import get_shared


# Commands are sent one at a time so MPC-HC is not flooded
PARALLEL_UPDATES = 1

async def async_setup_entry(
    hass: HomeAssistant,
    entry: MpcHcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player entity and its service."""
    async_add_entities([MpcHcMediaPlayer(entry.runtime_data)])

    # mpc_hc.send_command: send any WM_COMMAND code to the player
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SEND_COMMAND,
        {
            vol.Required(ATTR_COMMAND): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=65535)
            )
        },
        "async_send_wm_command",
    )
    
    # mpc_hc.set_tmdb_id: correct a wrong TMDb match
    platform.async_register_entity_service(
        SERVICE_SET_TMDB_ID,
        {
            vol.Optional(ATTR_TMDB_ID): vol.All(vol.Coerce(int), vol.Range(min=1)),
            vol.Optional(ATTR_TMDB_MEDIA_TYPE): vol.In(["tv", "movie"]),
            vol.Optional(ATTR_SCOPE, default=SCOPE_SERIES): vol.In(
                [SCOPE_SERIES, SCOPE_FILE]
            ),
        },
        "async_set_tmdb_id",
    )

class MpcHcMediaPlayer(MediaPlayerEntity, MpcHcEntity):
    """The main MPC-HC media player entity."""

    # name=None makes the entity take the device name (the primary entity)
    _attr_name = None
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._entry_id}_media_player"
        # Timestamp of the last poll; HA uses it to extrapolate the position
        self._position_updated_at: datetime | None = None
        self._shared = get_shared(coordinator.hass)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember when the position was last refreshed."""
        self._position_updated_at = dt_util.utcnow()
        super()._handle_coordinator_update()

    # ----- State -----

    @property
    def state(self) -> MediaPlayerState:
        """Map the MPC-HC state to a Home Assistant media player state."""
        status = self.coordinator.data
        if status.state == STATE_PLAYING:
            return MediaPlayerState.PLAYING
        if status.state == STATE_PAUSED:
            return MediaPlayerState.PAUSED
        # Stopped or no file loaded: the player is on but doing nothing
        return MediaPlayerState.IDLE

    @property
    def _has_media(self) -> bool:
        """Whether a file is currently playing or paused."""
        return self.coordinator.data.state in (STATE_PLAYING, STATE_PAUSED)

    # ----- Volume -----

    @property
    def volume_level(self) -> float:
        """Volume as a 0.0-1.0 float, as Home Assistant expects."""
        return self.coordinator.data.volume / 100

    @property
    def is_volume_muted(self) -> bool:
        return self.coordinator.data.muted

    # ----- Media info -----
    @property
    def media_content_type(self) -> MediaType | None:
        """Return movie for movies and video for everything else.

        TV episodes are deliberately reported as video: for the tvshow type the
        frontend builds the second line itself ("<series> S5E14"), which would
        repeat the title. As video, the second line comes from app_name.
        """
        if not self._has_media:
            return None
        if self.coordinator.media_info.media_type == MEDIA_MOVIE:
            return MediaType.MOVIE
        return MediaType.VIDEO
        # """Return tvshow, movie or generic video depending on the recognised file."""
        # if not self._has_media:
        #     return None
        # media_type = self.coordinator.media_info.media_type
        # if media_type == MEDIA_TVSHOW:
        #     return MediaType.TVSHOW
        # if media_type == MEDIA_MOVIE:
        #     return MediaType.MOVIE
        # return MediaType.VIDEO

    @property
    def _tmdb(self) -> MediaMetadata | None:
        """TMDb metadata of the current file, once the lookup has matched."""
        metadata = self.coordinator.metadata
        if metadata is not None and metadata.status == STATUS_MATCHED:
            return metadata
        return None

    @property
    def media_title(self) -> str | None:
        """Return the series or movie title, as TMDb spells it when it matched."""
        if not self._has_media:
            return None
        tmdb = self._tmdb
        if tmdb and tmdb.title:
            return tmdb.title
        info = self.coordinator.media_info
        if info.title:
            return info.title
        # Nothing recognised: fall back to the file name without its extension
        return self.coordinator.data.file.rsplit(".", 1)[0] or None

    @property
    def media_series_title(self) -> str | None:
        """Return the series title for TV shows."""
        if not self._has_media:
            return None
        info = self.coordinator.media_info
        if info.media_type != MEDIA_TVSHOW:
            return None
        tmdb = self._tmdb
        return (tmdb.title if tmdb and tmdb.title else None) or info.title

    @property
    def app_name(self) -> str | None:
        """Second line of the media display.

        TV shows: the episode code and, when TMDb knows it, the episode title.
        Movies: the year.
        """
        if not self._has_media:
            return None
        info = self.coordinator.media_info
        if info.media_type == MEDIA_TVSHOW:
            code = info.episode_code
            tmdb = self._tmdb
            if tmdb and tmdb.episode_title:
                return f"{code} · {tmdb.episode_title}" if code else tmdb.episode_title
            return code
        if info.media_type == MEDIA_MOVIE and info.year:
            return str(info.year)
        return None

    @property
    def media_image_url(self) -> str | None:
        """Poster of the series or movie; Home Assistant proxies and caches it."""
        tmdb = self._tmdb
        if not self._has_media or tmdb is None:
            return None
        return self._shared.image_url(tmdb.poster_path, "poster")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose recognised details that have no standard media player field."""
        if not self._has_media:
            return {}
        info = self.coordinator.media_info
        metadata = self.coordinator.metadata
        tmdb = self._tmdb
        return {
            "year": info.year,
            "resolution": info.resolution,
            "source": info.source,
            "languages": list(info.languages),
            "tmdb_status": metadata.status if metadata else STATUS_PENDING,
            "overview": tmdb.overview if tmdb else None,
            "genres": list(tmdb.genres) if tmdb else [],
            "rating": tmdb.rating if tmdb else None,
            "release_date": tmdb.release_date if tmdb else None,
            "tmdb_id": tmdb.tmdb_id if tmdb else None,
            "imdb_id": tmdb.imdb_id if tmdb else None,
        }

    @property
    def media_season(self) -> str | None:
        if not self._has_media:
            return None
        season = self.coordinator.media_info.season
        return str(season) if season is not None else None

    @property
    def media_episode(self) -> str | None:
        if not self._has_media:
            return None
        episode = self.coordinator.media_info.episode
        return str(episode) if episode is not None else None

    @property
    def media_duration(self) -> int | None:
        """Total length in seconds."""
        if not self._has_media:
            return None
        return self.coordinator.data.duration_ms // 1000

    @property
    def media_position(self) -> int | None:
        """Current position in seconds."""
        if not self._has_media:
            return None
        return self.coordinator.data.position_ms // 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        if not self._has_media:
            return None
        return self._position_updated_at

    

    # ----- Commands -----

    async def async_media_play(self) -> None:
        await self.coordinator.async_command(Cmd.PLAY)

    async def async_media_pause(self) -> None:
        await self.coordinator.async_command(Cmd.PAUSE)

    async def async_media_play_pause(self) -> None:
        """Use MPC-HC's own Play/Pause toggle instead of guessing from the state."""
        await self.coordinator.async_command(Cmd.PLAY_PAUSE)

    async def async_media_stop(self) -> None:
        await self.coordinator.async_command(Cmd.STOP)

    async def async_media_next_track(self) -> None:
        await self.coordinator.async_command(Cmd.PLAYLIST_NEXT)

    async def async_media_previous_track(self) -> None:
        await self.coordinator.async_command(Cmd.PLAYLIST_PREV)

    async def async_media_seek(self, position: float) -> None:
        await self.coordinator.async_seek(int(position))

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.async_set_volume(round(volume * 100))

    async def async_volume_up(self) -> None:
        # The step size is configured in MPC-HC itself (5% by default)
        await self.coordinator.async_command(Cmd.VOLUME_UP)

    async def async_volume_down(self) -> None:
        await self.coordinator.async_command(Cmd.VOLUME_DOWN)

    async def async_mute_volume(self, mute: bool) -> None:
        """MPC-HC only offers a mute toggle, so send it only if the state differs."""
        if mute != self.coordinator.data.muted:
            await self.coordinator.async_command(Cmd.MUTE_TOGGLE)

    async def async_send_wm_command(self, command: int) -> None:
        """Send a raw WM_COMMAND code (service mpc_hc.send_command)."""
        await self.coordinator.async_command(command)

    async def async_set_tmdb_id(
        self,
        tmdb_id: int | None = None,
        media_type: str | None = None,
        scope: str = SCOPE_SERIES,
    ) -> None:
        """Correct (or, without an ID, clear the correction of) the TMDb match."""
        await self.coordinator.async_set_override(scope, tmdb_id, media_type)