from __future__ import annotations

import pytest

from tests.conftest import SECRET, make_settings
from wpmcp.config import Settings


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"wp_url": "ftp://example.org"}, "http"),
        ({"wp_url": "https://example.org/"}, "trailing slash"),
        ({"wp_url": "https://example.org?x=1"}, "query"),
        ({"wp_url": "http://example.org"}, "only on loopback"),
        ({"wp_url": "https://example.org", "wp_password": ""}, "Application Password"),
        ({"wp_url": "https://example.org", "wp_user": ""}, "WPMCP_WP_USER"),
        ({"wp_url": "https://example.org", "wp_connect_ip": "10.0.0.5"}, "loopback"),
    ],
)
def test_wordpress_settings_are_validated(overrides, message):
    values = {"wp_password": "pw", **overrides}
    with pytest.raises(ValueError, match=message):
        make_settings(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"wp_url": "http://127.0.0.1:8081"},
        {"wp_url": "http://localhost:8081"},
        {"wp_url": "http://books.example.org", "wp_connect_ip": "127.0.0.1"},
        {"wp_url": "https://books.example.org", "wp_connect_ip": "::1"},
    ],
)
def test_local_wordpress_urls_are_accepted(overrides):
    assert make_settings(wp_password="pw", **overrides).wp_url


def test_secrets_are_not_in_repr():
    settings = make_settings(wp_url="https://example.org", wp_password="app-password-value")
    text = repr(settings)
    assert SECRET not in text
    assert "app-password-value" not in text


def test_wordpress_settings_from_env(tmp_path):
    password = tmp_path / "pw"
    password.write_text("file password\n")
    settings = Settings.from_env(
        {
            "WPMCP_SECRET": SECRET,
            "WPMCP_PUBLIC_URL": "https://mcp.example.org",
            "WPMCP_DB": ":memory:",
            "WPMCP_WP_URL": "https://example.org",
            "WPMCP_WP_USER": "integration",
            "WPMCP_WP_PASSWORD_FILE": str(password),
            "WPMCP_WP_CONNECT_IP": "127.0.0.1",
            "WPMCP_WP_TIMEOUT": "5",
        }
    )
    assert settings.wp_password == "file password"
    assert (settings.wp_user, settings.wp_connect_ip, settings.wp_timeout) == (
        "integration",
        "127.0.0.1",
        5.0,
    )


def test_wordpress_password_from_systemd_credential(tmp_path):
    (tmp_path / "wp-app-password").write_text("credential password")
    settings = Settings.from_env(
        {
            "CREDENTIALS_DIRECTORY": str(tmp_path),
            "WPMCP_SECRET": SECRET,
            "WPMCP_PUBLIC_URL": "https://mcp.example.org",
            "WPMCP_DB": ":memory:",
            "WPMCP_WP_URL": "https://example.org",
        }
    )
    assert settings.wp_password == "credential password"


def test_no_wordpress_by_default():
    settings = Settings.from_env(
        {"WPMCP_SECRET": SECRET, "WPMCP_PUBLIC_URL": "https://mcp.example.org", "WPMCP_DB": "x"}
    )
    assert settings.wp_url == ""


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"media_socket": "run/media.sock"}, "absolute"),
        ({"write_budget": 0}, "too low"),
        ({"upload_ttl": 10}, "too low"),
        ({"media_max_bytes": 100}, "too low"),
    ],
)
def test_media_and_write_settings_are_validated(overrides, message):
    with pytest.raises(ValueError, match=message):
        make_settings(**overrides)


def test_media_and_write_settings_from_env():
    settings = Settings.from_env(
        {
            "WPMCP_SECRET": SECRET,
            "WPMCP_PUBLIC_URL": "https://mcp.example.org",
            "WPMCP_DB": ":memory:",
            "WPMCP_MEDIA_SOCKET": "/run/wpmcp-media.sock",
            "WPMCP_MEDIA_MAX_BYTES": "2097152",
            "WPMCP_UPLOAD_TTL": "600",
            "WPMCP_WRITE_BUDGET": "30",
        }
    )
    assert settings.media_socket == "/run/wpmcp-media.sock"
    assert (settings.media_max_bytes, settings.upload_ttl, settings.write_budget) == (
        2097152,
        600,
        30,
    )
