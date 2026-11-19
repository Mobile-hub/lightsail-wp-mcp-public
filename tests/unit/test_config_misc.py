import os

import pytest

from wpmcp.auth.cidrs import Allowlist, parse
from wpmcp.auth.ratelimit import SlidingWindow
from wpmcp.config import Settings, load_secret, parse_image_hosts

from ..conftest import BASE, SECRET, make_settings


# ----------------------------------------------------------------------------- settings
def test_derived_urls():
    settings = make_settings()
    assert settings.issuer == BASE
    assert settings.resource == f"{BASE}/mcp"
    assert settings.public_host == "mcp.example.test"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"secret": "short"}, "at least 32"),
        ({"db_path": ""}, "WPMCP_DB"),
        ({"public_url": ""}, "origin"),
        ({"public_url": "https://mcp.example.test/path"}, "origin"),
        ({"public_url": "https://mcp.example.test/"}, "slash"),
        ({"public_url": "https://mcp.example.test?x=1"}, "origin"),
        ({"public_url": "http://mcp.example.test"}, "https"),
        ({"public_url": "ftp://mcp.example.test", "insecure_dev": True}, "https"),
        ({"cidr_mode": "strict"}, "WPMCP_CIDR_MODE"),
        ({"cidr_mode": "enforce"}, "WPMCP_CIDR_FILE"),
        ({"host": "0.0.0.0"}, "loopback"),
    ],
)
def test_invalid_settings(overrides, message):
    with pytest.raises(ValueError, match=message):
        make_settings(**overrides)


def test_http_public_url_only_in_dev():
    assert make_settings(public_url="http://127.0.0.1:8080", insecure_dev=True).public_host == (
        "127.0.0.1:8080"
    )


def test_from_env_defaults_and_overrides(tmp_path):
    env = {
        "WPMCP_SECRET": SECRET,
        "WPMCP_PUBLIC_URL": f"{BASE}/",
        "WPMCP_DB": str(tmp_path / "db.sqlite"),
    }
    settings = Settings.from_env(env)
    assert settings.public_url == BASE
    assert settings.access_token_ttl == 900 and settings.cidr_mode == "off"
    assert not settings.trust_unix_peer and settings.uds == ""
    assert [str(n) for n in settings.trusted_proxy_cidrs] == ["127.0.0.0/8", "::1/128"]

    settings = Settings.from_env(
        {
            **env,
            "WPMCP_UDS": "/run/wpmcp/wpmcp.sock",
            "WPMCP_ACCESS_TOKEN_TTL": "300",
            "WPMCP_REFRESH_TOKEN_TTL": " ",
            "WPMCP_TRUSTED_PROXIES": "10.0.0.0/8, 192.168.1.1",
            "WPMCP_CIDR_MODE": "Monitor",
            "WPMCP_CIDR_FILE": "/etc/wpmcp/cidrs.txt",
            "WPMCP_SITE_NAME": "My site",
        }
    )
    # A Unix socket has no peer address: the proxy behind it is trusted by default.
    assert settings.trust_unix_peer and settings.uds == "/run/wpmcp/wpmcp.sock"
    assert settings.access_token_ttl == 300 and settings.refresh_token_ttl == 14 * 24 * 3600
    assert [str(n) for n in settings.trusted_proxy_cidrs] == ["10.0.0.0/8", "192.168.1.1/32"]
    assert settings.cidr_mode == "monitor" and settings.site_name == "My site"

    explicit = Settings.from_env({**env, "WPMCP_UDS": "/x.sock", "WPMCP_TRUST_UNIX_PEER": "false"})
    assert not explicit.trust_unix_peer
    assert Settings.from_env({**env, "WPMCP_TRUSTED_PROXIES": "none"}).trusted_proxy_cidrs


def test_from_env_reads_the_process_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("WPMCP_SECRET", SECRET)
    monkeypatch.setenv("WPMCP_PUBLIC_URL", BASE)
    monkeypatch.setenv("WPMCP_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.delenv("WPMCP_SECRET_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    assert Settings.from_env().secret == SECRET


def test_secret_sources_in_order(tmp_path):
    explicit = tmp_path / "explicit"
    explicit.write_text("file-secret\n")
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "wpmcp-secret").write_text("credential-secret\n")
    env = {"WPMCP_SECRET": "env-secret"}
    assert load_secret(env) == "env-secret"
    assert load_secret({**env, "CREDENTIALS_DIRECTORY": str(creds)}) == "credential-secret"
    both = {**env, "CREDENTIALS_DIRECTORY": str(creds), "WPMCP_SECRET_FILE": str(explicit)}
    assert load_secret(both) == "file-secret"
    # A credentials directory without our credential falls back to the environment.
    assert load_secret({**env, "CREDENTIALS_DIRECTORY": str(tmp_path)}) == "env-secret"
    assert load_secret({}) == ""


# ---------------------------------------------------------------------------- allowlist
def test_parse_cidr_file():
    networks = parse("# Anthropic\n160.79.104.0/21\n\n 2001:db8::/32  # v6\n203.0.113.7\n")
    assert [str(n) for n in networks] == ["160.79.104.0/21", "2001:db8::/32", "203.0.113.7/32"]
    with pytest.raises(ValueError):
        parse("not-a-network\n")


def test_allowlist_off_permits_everything():
    assert Allowlist().permits("198.51.100.1")
    assert Allowlist().permits("")


def test_allowlist_enforce(tmp_path):
    path = tmp_path / "cidrs.txt"
    path.write_text("160.79.104.0/21\n2001:db8::/32\n")
    allowlist = Allowlist(str(path), "enforce")
    assert allowlist.permits("160.79.105.9")
    assert allowlist.permits("2001:db8::1")
    assert not allowlist.permits("198.51.100.1")
    assert not allowlist.permits("")
    assert not allowlist.permits("testclient")


def test_allowlist_enforce_fails_closed_at_startup(tmp_path):
    with pytest.raises(ValueError, match="cannot load"):
        Allowlist(str(tmp_path / "missing.txt"), "enforce")
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n")
    with pytest.raises(ValueError, match="no networks"):
        Allowlist(str(empty), "enforce")


def test_allowlist_monitor_starts_without_a_file(tmp_path):
    allowlist = Allowlist(str(tmp_path / "missing.txt"), "monitor")
    assert allowlist.permits("198.51.100.1")


def test_allowlist_reloads_and_keeps_last_good(tmp_path):
    path = tmp_path / "cidrs.txt"
    path.write_text("203.0.113.0/24\n")
    allowlist = Allowlist(str(path), "enforce")
    assert allowlist.permits("203.0.113.5", now=0)

    path.write_text("198.51.100.0/24\n")
    os.utime(path, (10_000, 10_000))
    assert allowlist.permits("203.0.113.5", now=10)  # not re-checked yet
    assert not allowlist.permits("203.0.113.5", now=100)
    assert allowlist.permits("198.51.100.5", now=100)

    path.write_text("garbage\n")  # a broken update keeps the previous list
    os.utime(path, (20_000, 20_000))
    assert allowlist.permits("198.51.100.5", now=200)
    path.unlink()
    assert allowlist.permits("198.51.100.5", now=300)


# --------------------------------------------------------------------------- rate limit
def test_sliding_window():
    limiter = SlidingWindow(limit=2, window=10)
    assert limiter.allow("ip", now=100)
    assert limiter.allow("ip", now=101)
    assert not limiter.allow("ip", now=105)
    assert limiter.allow("other", now=105)
    assert limiter.allow("ip", now=111)  # the first hit left the window


def test_sliding_window_counts_several_at_once_or_none():
    limiter = SlidingWindow(limit=5, window=10)
    assert limiter.allow("grant", now=100, count=3)
    assert limiter.remaining("grant", now=100) == 2
    assert not limiter.allow("grant", now=101, count=3)  # all or nothing
    assert limiter.remaining("grant", now=101) == 2
    assert limiter.allow("grant", now=101, count=2)
    assert limiter.remaining("grant", now=101) == 0
    assert limiter.remaining("grant", now=112) == 5  # the window moved on
    assert limiter.remaining("other") == 5


def test_sliding_window_uses_the_monotonic_clock_by_default():
    limiter = SlidingWindow(limit=1, window=60)
    assert limiter.allow("ip") and not limiter.allow("ip")


def test_sliding_window_memory_is_bounded(monkeypatch):
    monkeypatch.setattr("wpmcp.auth.ratelimit._MAX_KEYS", 10)
    limiter = SlidingWindow(limit=5, window=10)
    for i in range(11):
        limiter.allow(f"old-{i}", now=0)
    limiter.allow("new", now=100)  # stale keys are evicted
    assert set(limiter._hits) == {"new"}
    for i in range(40):
        limiter.allow(f"live-{i}", now=100)  # nothing stale to evict: it starts over
        assert len(limiter._hits) <= 11


def test_image_hosts_are_parsed_and_normalised():
    value = " Covers.Example.org., .cdn.example.net,img*.example.com,covers.example.org"
    hosts = parse_image_hosts(value)
    assert hosts == ("covers.example.org", ".cdn.example.net", "img*.example.com")
    assert parse_image_hosts(None) == () and parse_image_hosts("none") == ()
    env = {"WPMCP_SECRET": SECRET, "WPMCP_PUBLIC_URL": BASE, "WPMCP_DB": ":memory:"}
    env["WPMCP_IMAGE_HOSTS"] = "covers.example.org"
    assert Settings.from_env(env).image_hosts == ("covers.example.org",)


@pytest.mark.parametrize(
    "value",
    [
        ".org",  # a whole domain ending
        "*.org",
        "example*.org",
        "localhost",
        "203.0.113.5",
        "https://covers.example.org",
        "covers.example.org/path",
        "covers.example.org:443",
        "*",
        "a b.example.org",
        ",".join(f"h{n}.example.org" for n in range(21)),
    ],
)
def test_image_hosts_refused(value):
    with pytest.raises(ValueError, match="WPMCP_IMAGE_HOSTS"):
        parse_image_hosts(value)


def test_image_hosts_must_be_normalised_in_settings():
    with pytest.raises(ValueError, match="image_hosts"):
        make_settings(image_hosts=("Covers.Example.org",))
    assert make_settings(image_hosts=(".example.org",)).image_hosts == (".example.org",)
