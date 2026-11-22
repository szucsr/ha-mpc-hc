"""Find MPC-HC web servers on the local network.

- Probes every address of the subnets on one TCP port
- Asks whatever answers whether it is an MPC-HC web interface
- Reports the players found and hosts that look like MPC-HC with blocked access
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import aiohttp

from .api import MpcHcAccessDeniedError, MpcHcApi, MpcHcConnectionError

_LOGGER = logging.getLogger(__name__)

# Seconds to wait for a TCP connection. Hosts behind a firewall that drops
# packets never answer, so this is also roughly the total duration of a scan.
PROBE_TIMEOUT = 1.0
# Number of simultaneous connection attempts
CONCURRENCY = 32


@dataclass(frozen=True, slots=True)
class DiscoveredPlayer:
    """An MPC-HC web server found on the network."""

    host: str
    port: int
    version: str


@dataclass(slots=True)
class ScanResult:
    """Outcome of a network scan."""

    players: list[DiscoveredPlayer] = field(default_factory=list)
    # Hosts that accept the connection but drop it right away: probably MPC-HC
    # with "Allow access from localhost only" enabled
    blocked: list[str] = field(default_factory=list)


def hosts_of(
    networks: Iterable[ipaddress.IPv4Network], exclude: Iterable[str] = ()
) -> list[str]:
    """List the host addresses of the networks, without duplicates or excluded ones."""
    skip = set(exclude)
    hosts: dict[str, None] = {}
    for network in networks:
        for host in network.hosts():
            address = str(host)
            if address not in skip:
                hosts.setdefault(address)
    return list(hosts)


async def _port_open(host: str, port: int) -> bool:
    """Check whether a TCP connection to host:port can be established."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), PROBE_TIMEOUT
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    # The peer may reset the connection while it is closing
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def async_scan(
    session: aiohttp.ClientSession,
    networks: Iterable[ipaddress.IPv4Network],
    port: int,
    exclude: Iterable[str] = (),
) -> ScanResult:
    """Scan the networks for MPC-HC web servers listening on the given port."""
    hosts = hosts_of(networks, exclude)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def probe(host: str) -> str | None:
        async with semaphore:
            return host if await _port_open(host, port) else None

    # Step 1: cheap TCP check on every address
    open_hosts = [h for h in await asyncio.gather(*(probe(h) for h in hosts)) if h]

    # Step 2: ask each address that answered whether it is MPC-HC. Only a few
    # hosts get here, so the normal request timeout is fine.
    result = ScanResult()

    async def identify(host: str) -> None:
        api = MpcHcApi(session, host, port)
        try:
            status = await api.async_get_status()
        except MpcHcAccessDeniedError:
            result.blocked.append(host)
        except MpcHcConnectionError:
            pass  # Something else listens on this port
        else:
            result.players.append(DiscoveredPlayer(host, port, status.version))

    await asyncio.gather(*(identify(h) for h in open_hosts))

    result.players.sort(key=lambda p: ipaddress.ip_address(p.host))
    result.blocked.sort(key=ipaddress.ip_address)
    _LOGGER.debug(
        "MPC-HC scan of %d hosts on port %d: %d open, %d found",
        len(hosts),
        port,
        len(open_hosts),
        len(result.players),
    )
    if result.blocked:
        _LOGGER.info(
            "Possible MPC-HC with blocked access (connection dropped): %s",
            ", ".join(result.blocked),
        )
    return result