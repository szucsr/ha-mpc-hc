from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components import network
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    MpcHcAccessDeniedError,
    MpcHcApi,
    MpcHcConnectionError,
    MpcHcInvalidResponseError,
)
from .const import DEFAULT_HOST, DEFAULT_NAME, DEFAULT_PORT, DOMAIN
from .discovery import DiscoveredPlayer, async_scan

PORT_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))


async def _async_local_networks(
    hass: HomeAssistant,
) -> tuple[list[ipaddress.IPv4Network], set[str]]:
    """Return the networks of the adapters Home Assistant uses and its own addresses."""
    networks: list[ipaddress.IPv4Network] = []
    own_addresses: set[str] = set()
    for adapter in await network.async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for ipv4 in adapter["ipv4"]:
            address = ipaddress.ip_address(ipv4["address"])
            if address.is_loopback or address.is_link_local:
                continue
            own_addresses.add(str(address))
            # Never scan more than one /24 per address: a /16 would be 65 000 hosts
            prefix = max(ipv4["network_prefix"], 24)
            net = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            if net not in networks:
                networks.append(net)
    return networks, own_addresses


class MpcHcConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup and reconfiguration of an MPC-HC instance."""

    VERSION = 1

    def __init__(self) -> None:
        """Remember the players found by the last scan."""
        self._discovered: dict[str, DiscoveredPlayer] = {}

    @staticmethod
    def _schema(defaults: Mapping[str, Any]) -> vol.Schema:
        """Form schema, pre-filled with the given values."""
        return vol.Schema(
            {
                vol.Optional(
                    CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
                ): str,
                vol.Required(
                    CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)
                ): str,
                vol.Required(
                    CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
                ): PORT_VALIDATOR,
            }
        )

    def _is_duplicate(
        self, host: str, port: int, ignore_entry_id: str | None = None
    ) -> bool:
        """Whether another entry already points at this host and port."""
        return any(
            entry.entry_id != ignore_entry_id
            and entry.data[CONF_HOST].lower() == host.lower()
            and entry.data[CONF_PORT] == port
            for entry in self._async_current_entries()
        )

    async def _async_check(
        self, host: str, port: int, ignore_entry_id: str | None = None
    ) -> str | None:
        """Return an error key, or None if the player is reachable and new."""
        if self._is_duplicate(host, port, ignore_entry_id):
            return "already_configured"

        api = MpcHcApi(async_get_clientsession(self.hass), host, port)
        try:
            await api.async_get_status()
        # Subclasses first: they are more specific than the base error
        except MpcHcAccessDeniedError:
            return "localhost_only"
        except MpcHcInvalidResponseError:
            return "not_mpc_hc"
        except MpcHcConnectionError:
            return "cannot_connect"
        return None

    # ----- Setup: choose how to add the player -----

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose between scanning the network and manual entry."""
        return self.async_show_menu(step_id="user", menu_options=["scan", "manual"])

    # ----- Setup: scan the network -----

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan the local network for MPC-HC web servers."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_PORT]
            networks, own_addresses = await _async_local_networks(self.hass)
            result = await async_scan(
                async_get_clientsession(self.hass),
                networks,
                port,
                exclude=own_addresses,
            )

            # Players that are already set up are not offered again
            known = {
                (entry.data[CONF_HOST].lower(), entry.data[CONF_PORT])
                for entry in self._async_current_entries()
            }
            self._discovered = {
                player.host: player
                for player in result.players
                if (player.host.lower(), player.port) not in known
            }
            if self._discovered:
                return await self.async_step_select_device()

            if result.players:
                errors["base"] = "all_configured"
            elif result.blocked:
                errors["base"] = "localhost_only_found"
            else:
                errors["base"] = "no_devices_found"

        port_default = (user_input or {}).get(CONF_PORT, DEFAULT_PORT)
        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema(
                {vol.Required(CONF_PORT, default=port_default): PORT_VALIDATOR}
            ),
            errors=errors,
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the found players and give it a name."""
        if user_input is not None:
            player = self._discovered[user_input[CONF_HOST]]
            name = user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME
            return self.async_create_entry(
                title=name,
                data={CONF_NAME: name, CONF_HOST: player.host, CONF_PORT: player.port},
            )

        options = [
            SelectOptionDict(
                value=player.host,
                label=f"{player.host}:{player.port} (MPC-HC {player.version})",
            )
            for player in self._discovered.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=options[0]["value"]): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="select_device", data_schema=schema)

    # ----- Setup: manual entry -----

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for name, host and port, then verify the connection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            name = user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME

            if error := await self._async_check(host, port):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name, CONF_HOST: host, CONF_PORT: port},
                )

        # After an error the form keeps what the user typed
        return self.async_show_form(
            step_id="manual", data_schema=self._schema(user_input or {}), errors=errors
        )

    # ----- Reconfigure -----

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change name, host or port of an existing entry, keeping its entities."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            name = user_input.get(CONF_NAME, "").strip() or DEFAULT_NAME

            if error := await self._async_check(host, port, entry.entry_id):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    title=name,
                    data_updates={CONF_NAME: name, CONF_HOST: host, CONF_PORT: port},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._schema(user_input or entry.data),
            errors=errors,
        )