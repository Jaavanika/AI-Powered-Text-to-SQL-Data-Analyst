"""Which database hosts a user may ask the backend to connect to.

Letting users name any host and port would turn the server into a tool for probing whatever
network it sits on. Hosts must be on an allowlist, and addresses used for cloud metadata
services or "this host" tricks are refused even then (unless listed explicitly as an address).
"""

import ipaddress
import socket


class HostNotAllowed(Exception):
    status_code = 403

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class HostNotFound(HostNotAllowed):
    """An allowed name that does not resolve, so there is no address to connect to."""

    status_code = 400


def _entries(spec: str) -> list[str]:
    return [e.strip().lower() for e in spec.split(",") if e.strip()]


def _ip(value: str):
    try:
        return ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return None


def _listed(host: str, address, entries: list[str]) -> bool:
    for entry in entries:
        if entry == "*" or entry == host:
            return True
        if entry.startswith("*.") and host.endswith(entry[1:]) and host != entry[2:]:
            return True
        if address is not None:
            try:
                if "/" in entry and address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def _risky(address) -> bool:
    # Not is_reserved: it also covers the IPv6 loopback ::1, which "localhost" resolves to.
    return address.is_link_local or address.is_unspecified or address.is_multicast


def _clean(host: str) -> str:
    host = host.strip().lower().strip("[]")
    if not host or any(c in host for c in " /\\@?#"):
        raise HostNotAllowed("That is not a valid host name.")
    return host


def ensure_listed(host: str, spec: str) -> None:
    """Raise HostNotAllowed unless `host` is on the allowlist. Looks at the list only, never at DNS, so it is
    cheap enough to repeat whenever a stored connection is used (the list may have been tightened since)."""
    host = _clean(host)
    if not _listed(host, _ip(host), _entries(spec)):
        raise HostNotAllowed(
            f"The host '{host}' is not on this server's list of allowed database hosts. "
            "Ask your administrator to add it to ALLOWED_DB_HOSTS.")


def _check(host: str, spec: str) -> list:
    """The allowlist check. Returns the addresses it examined: the host itself if it is an address,
    otherwise what the name resolved to (resolved exactly once)."""
    host = _clean(host)
    ensure_listed(host, spec)
    literal = _ip(host)
    if literal is not None and host in _entries(spec):
        return [literal]                      # an address named explicitly is the administrator's decision
    # A name or range that is allowed must still not lead somewhere dangerous, such as the
    # link-local address cloud providers use for instance metadata.
    addresses = [literal] if literal else _resolve(host)
    if any(_risky(a) for a in addresses):
        raise HostNotAllowed(
            f"The host '{host}' resolves to an address that cannot be used for database connections.")
    return addresses


def check_host_allowed(host: str, spec: str) -> None:
    """Raise HostNotAllowed unless `host` may be connected to under the allowlist `spec`."""
    _check(host, spec)


def pin_addresses(host: str, spec: str) -> list[str]:
    """The addresses to connect to for `host`, after the allowlist check, IPv4 first.

    Connecting by these addresses instead of by name means the connection goes where the check looked.
    If the driver resolved the name again, a DNS answer that changes between the two lookups
    ("DNS rebinding") could send it to an address that was never checked.
    """
    addresses = _check(host, spec)
    if not addresses:
        raise HostNotFound(f"The host '{_clean(host)}' could not be found. Check the name.")
    return [str(a) for a in sorted(set(addresses), key=lambda a: (a.version, int(a)))]


def _resolve(host: str) -> list:
    try:
        found = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, ValueError, UnicodeError):
        return []          # the connection attempt will fail with a clear message of its own
    return list(found)
