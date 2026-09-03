"""SSRF protection for user-supplied scrape targets.

Jiro's ``/scrape`` endpoint fetches arbitrary URLs server-side. Without
protection an attacker could target internal services (``http://169.254.169.254``
cloud metadata, ``http://localhost:8000/api-keys``, ``http://10.0.0.1/`` LAN
hosts). We resolve the hostname to its IP address(es) and reject any that fall
into private / loopback / link-local / CGNAT / metadata ranges — including
IPv4-mapped IPv6 addresses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import urllib.parse
from typing import Iterable, List, Optional, Sequence

from jiro.errors import SSRFError

_log = logging.getLogger("jiro.security")

# CIDRs that must never be fetched by a user-controlled scrape.
# Kept intentionally tight to avoid false positives on public/CDN IPs:
# loopback, RFC1918 private, link-local (incl. cloud metadata 169.254.169.254),
# and the IPv6 equivalents.
_BLOCKED_V4 = [
    "0.0.0.0/8",          # "this" network
    "10.0.0.0/8",         # private
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local / cloud metadata (169.254.169.254)
    "172.16.0.0/12",      # private
    "192.168.0.0/16",     # private
]
_BLOCKED_V6 = [
    "::/128",             # unspecified
    "::1/128",            # loopback
    "::ffff:0:0/96",      # IPv4-mapped (checked via mapped addr)
    "64:ff9b::/96",       # IPv4-NAT64
    "fc00::/7",           # unique local (ULA)
    "fe80::/10",          # link-local
]

_BLOCKED_NETS_V4 = [ipaddress.ip_network(c) for c in _BLOCKED_V4]
_BLOCKED_NETS_V6 = [ipaddress.ip_network(c) for c in _BLOCKED_V6]


def is_blocked_ip(ip: str) -> bool:
    """Return True if the given IP literal is in a disallowed range."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable IP -> refuse
    if isinstance(addr, ipaddress.IPv6Address):
        # Unwrap IPv4-mapped / NAT64 addresses to their IPv4 form.
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif getattr(addr, "ipv4_natted", None) is not None:
            addr = addr.ipv4_natted  # type: ignore[attr-defined]
    for net in (_BLOCKED_NETS_V4 if addr.version == 4 else _BLOCKED_NETS_V6):
        if addr in net:
            return True
    return False


def _resolve(hostname: str) -> List[str]:
    """Resolve a hostname to IPv4/IPv6 address literals."""
    infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    return list({str(info[4][0]) for info in infos})


async def resolve_hostname(hostname: str) -> List[str]:
    return await asyncio.to_thread(_resolve, hostname)


def validate_target_url(
    url: str,
    *,
    allow_private: bool = False,
    own_hosts: Optional[Sequence[str]] = None,
) -> str:
    """Validate a user-supplied scrape URL.

    Raises ``ValueError`` (caller maps to SSRFError) if the URL is not http(s),
    or resolves to a blocked IP range. Returns the normalized URL on success.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("url has no host")
    host = parsed.hostname
    if allow_private:
        return url
    if host.lower() == "localhost":
        raise SSRFError("blocked host: localhost")
    # Direct IP literal fast-path (only when the host IS an IP address).
    is_literal = False
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        if is_blocked_ip(host):
            raise SSRFError(f"blocked target IP: {host}")
        return url
    try:
        addresses = _resolve(host)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        # SECURITY: Fail CLOSED - reject when DNS resolution fails
        # This prevents attackers from bypassing SSRF via DNS manipulation
        raise SSRFError(f"DNS resolution failed for {host}: {exc}")
    if not addresses:
        raise SSRFError(f"no addresses for host {host!r}")
    for addr in addresses:
        if is_blocked_ip(addr):
            raise SSRFError(f"blocked target IP: {addr} (host {host})")
    if own_hosts:
        for oh in own_hosts:
            if oh and (oh == host or oh.split(":")[0] == host):
                raise SSRFError(f"refusing to scrape the server's own host: {host}")
    return url


async def async_validate_target_url(
    url: str,
    *,
    allow_private: bool = False,
    own_hosts: Optional[Sequence[str]] = None,
) -> str:
    """Async variant of :func:`validate_target_url`."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("url has no host")
    host = parsed.hostname
    if allow_private:
        return url
    if host.lower() == "localhost":
        raise SSRFError("blocked host: localhost")
    # Direct IP literal fast-path (only when the host IS an IP address).
    is_literal = False
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal:
        if is_blocked_ip(host):
            raise SSRFError(f"blocked target IP: {host}")
        return url
    try:
        addresses = await resolve_hostname(host)
    except Exception as exc:
        # SECURITY: Fail CLOSED - reject when DNS resolution fails
        # This prevents attackers from bypassing SSRF via DNS manipulation
        raise SSRFError(f"DNS resolution failed for {host}: {exc}")
    if not addresses:
        raise SSRFError(f"no addresses for host {host!r}")
    for addr in addresses:
        if is_blocked_ip(addr):
            raise SSRFError(f"blocked target IP: {addr} (host {host})")
    if own_hosts:
        for oh in own_hosts:
            if oh and (oh == host or oh.split(":")[0] == host):
                raise SSRFError(f"refusing to scrape the server's own host: {host}")
    return url


def blocked_networks() -> Iterable[object]:
    """Expose the blocked networks (useful for tests / docs)."""
    return list(_BLOCKED_NETS_V4) + list(_BLOCKED_NETS_V6)
