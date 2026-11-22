"""Base entity: groups all entities of a player under one device."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN
from .coordinator import MpcHcCoordinator


class MpcHcEntity(CoordinatorEntity[MpcHcCoordinator]):
    """Base class that groups every entity under a single MPC-HC device."""

    # Entity names are prefixed with the device name automatically
    _attr_has_entity_name = True

    def __init__(self, coordinator: MpcHcCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]

        # The entry id stays the same when the host or port is reconfigured,
        # so entities and history survive an IP change
        self._entry_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_NAME) or DEFAULT_NAME,
            manufacturer="MPC-HC",
            model="Media Player Classic - Home Cinema",
            sw_version=coordinator.data.version,
            # Links to the player's own web interface from the device page
            configuration_url=f"http://{host}:{port}/",
        )