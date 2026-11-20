import ipaddress

import pytest

from app.core import hosts
from app.core.hosts import HostNotAllowed, check_host_allowed

DEFAULT = "localhost,127.0.0.1,::1"


def allowed(host, spec=DEFAULT):
    try:
        check_host_allowed(host, spec)
        return True
    except HostNotAllowed:
        return False


def test_the_default_allows_only_this_machine():
    assert all(allowed(h) for h in ("localhost", "LOCALHOST", "127.0.0.1", "::1", "[::1]"))
    assert not any(allowed(h) for h in ("db.example.com", "10.0.0.5", "192.168.1.10", "8.8.8.8", "127.0.0.2"))


def test_the_refusal_tells_the_user_what_to_ask_for():
    with pytest.raises(HostNotAllowed) as exc:
        check_host_allowed("db.example.com", DEFAULT)
    assert "db.example.com" in exc.value.message and "ALLOWED_DB_HOSTS" in exc.value.message


def test_exact_names_and_wildcards():
    spec = "db.test.invalid, *.corp.test.invalid"
    assert allowed("db.test.invalid", spec)                       # an exact name only needs to be listed
    assert not allowed("other.test.invalid", spec)
    assert allowed("db.corp.test.invalid", spec)
    assert allowed("a.b.corp.test.invalid", spec)
    assert not allowed("corp.test.invalid", spec)                 # the bare domain is not covered by *.domain
    assert not allowed("evilcorp.test.invalid", spec)             # nor is a look-alike
    assert not allowed("db.corp.test.invalid.attacker.test", spec)


def test_cidr_ranges():
    spec = "10.0.0.0/8, 192.168.1.0/24"
    assert allowed("10.1.2.3", spec) and allowed("192.168.1.99", spec)
    assert not allowed("11.0.0.1", spec) and not allowed("192.168.2.1", spec)
    assert not allowed("db.example.com", spec)                    # a name is not matched by an address range


def test_star_allows_any_host_but_never_dangerous_addresses():
    assert allowed("203.0.113.10", "*") and allowed("anything.test.invalid", "*")
    for bad in ("169.254.169.254", "0.0.0.0", "224.0.0.1", "fe80::1", "::"):
        assert not allowed(bad, "*"), bad


def test_a_dangerous_address_is_allowed_only_when_listed_explicitly():
    assert allowed("169.254.169.254", "169.254.169.254")
    assert not allowed("169.254.169.254", "169.254.0.0/16")        # a range is not an explicit decision


def test_an_allowed_name_that_resolves_to_a_dangerous_address_is_refused(monkeypatch):
    monkeypatch.setattr(hosts, "_resolve", lambda host: [ipaddress.ip_address("169.254.169.254")])
    assert not allowed("metadata.test.invalid", "metadata.test.invalid")
    monkeypatch.setattr(hosts, "_resolve", lambda host: [ipaddress.ip_address("10.1.1.1")])
    assert allowed("internal.test.invalid", "internal.test.invalid")


def test_unresolvable_names_are_left_to_the_connection_attempt():
    assert allowed("nonexistent-host.test.invalid", "*.test.invalid")


@pytest.mark.parametrize("bad", ["", "   ", "two words", "a/b", "back\\slash", "user@host", "host?x=1", "host#frag"])
def test_malformed_hosts_are_rejected_before_anything_else(bad):
    with pytest.raises(HostNotAllowed, match="not a valid host"):
        check_host_allowed(bad, "*")


# ------------------------------------------------------ pinning the address to connect to

def addrs(*values):
    return [ipaddress.ip_address(v) for v in values]


def test_a_literal_address_is_its_own_pin():
    assert hosts.pin_addresses("127.0.0.1", DEFAULT) == ["127.0.0.1"]
    assert hosts.pin_addresses("[::1]", DEFAULT) == ["::1"]


def test_a_name_is_resolved_once_and_the_checked_addresses_are_the_ones_returned(monkeypatch):
    """The connection must use exactly what was checked; resolving again could return something else."""
    calls = []

    def resolve(host):
        calls.append(host)
        return addrs("10.1.1.1", "10.1.1.2")

    monkeypatch.setattr(hosts, "_resolve", resolve)
    assert hosts.pin_addresses("db.test.invalid", "db.test.invalid") == ["10.1.1.1", "10.1.1.2"]
    assert calls == ["db.test.invalid"]


def test_ipv4_addresses_come_first_and_the_order_is_stable(monkeypatch):
    monkeypatch.setattr(hosts, "_resolve", lambda host: addrs("::1", "127.0.0.1", "127.0.0.1"))
    assert hosts.pin_addresses("localhost", DEFAULT) == ["127.0.0.1", "::1"]


def test_one_dangerous_address_refuses_the_whole_name(monkeypatch):
    monkeypatch.setattr(hosts, "_resolve", lambda host: addrs("10.1.1.1", "169.254.169.254"))
    with pytest.raises(HostNotAllowed, match="cannot be used"):
        hosts.pin_addresses("db.test.invalid", "db.test.invalid")


def test_a_name_that_does_not_resolve_cannot_be_pinned(monkeypatch):
    """check_host_allowed leaves this to the connection attempt; pinning cannot, or a name that fails the check
    and then resolves for the driver would slip past it."""
    monkeypatch.setattr(hosts, "_resolve", lambda host: [])
    with pytest.raises(hosts.HostNotFound) as exc:
        hosts.pin_addresses("gone.test.invalid", "*.test.invalid")
    assert exc.value.status_code == 400 and "could not be found" in exc.value.message


def test_pinning_still_applies_the_allowlist():
    with pytest.raises(HostNotAllowed) as exc:
        hosts.pin_addresses("db.example.com", DEFAULT)
    assert exc.value.status_code == 403


def test_an_explicitly_listed_address_is_pinned_even_if_it_looks_risky():
    assert hosts.pin_addresses("169.254.169.254", "169.254.169.254") == ["169.254.169.254"]


def test_ensure_listed_checks_the_list_without_touching_dns(monkeypatch):
    monkeypatch.setattr(hosts, "_resolve", lambda host: (_ for _ in ()).throw(AssertionError("DNS was used")))
    hosts.ensure_listed("db.test.invalid", "db.test.invalid")
    with pytest.raises(HostNotAllowed):
        hosts.ensure_listed("db.test.invalid", DEFAULT)
