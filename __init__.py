"""Set up and unload the MPC-HC integration.

- Loads the shared TMDb settings and cache once per Home Assistant start
- Creates the API client and the coordinator for every configured player
- Forwards the setup to the entity platforms and unloads them again
"""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MpcHcApi
from .coordinator import MpcHcConfigEntry, MpcHcCoordinator

# More platforms (sensor, image, button, select, number) are added in later steps
PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

async def async_setup_entry(hass: HomeAssistant, entry: MpcHcConfigEntry) -> bool:
    """Set up MPC-HC from a config entry."""
    api = MpcHcApi(
        async_get_clientsession(hass), entry.data[CONF_HOST], entry.data[CONF_PORT]
    )
    coordinator = MpcHcCoordinator(hass, entry, api)

    # Raises ConfigEntryNotReady if the PC is off, so HA retries automatically
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MpcHcConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)