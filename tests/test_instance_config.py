"""The instance the client talks to is configurable — and the host guard follows it.

froide runs more than one public portal; fragdenstaat.at is the second production
instance named in froide's own README. Pointing this server at another instance must
not be possible by accident, and must never widen the set of hosts the bearer token
may reach. These tests exist because that guard used to be a literal tuple.
"""

from __future__ import annotations

import pytest

from fds_mcp import config
from fds_mcp.client import FdsClient, ForeignHost

AT = "https://fragdenstaat.at"


def test_defaults_to_fragdenstaat_de():
    assert config.base_url() == "https://fragdenstaat.de"
    assert config.api_url() == "https://fragdenstaat.de/api/v1"


def test_base_url_can_be_pointed_at_another_froide_instance(monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    assert config.base_url() == AT
    assert config.api_url() == f"{AT}/api/v1"
    assert config.authorize_url() == f"{AT}/account/authorize/"


def test_trailing_slash_is_not_doubled(monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT + "/")
    assert config.api_url() == f"{AT}/api/v1"


@pytest.mark.parametrize("value", [
    "http://fragdenstaat.at",                 # the bearer token travels here
    "ftp://fragdenstaat.at",
    "https://user:pw@fragdenstaat.at",        # credentials in a URL
    "https://fragdenstaat.at/some/path",      # a path would silently break every route
    "https://",                               # no host
    "not-a-url",
])
def test_a_bad_base_url_is_refused_loudly(monkeypatch, value):
    monkeypatch.setenv("FDS_MCP_BASE_URL", value)
    with pytest.raises(config.ConfigError):
        config.base_url()


def test_allowed_hosts_follow_the_configured_instance(monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    hosts = config.allowed_hosts()
    assert "fragdenstaat.at" in hosts
    # The point of the whole exercise: the old default must not linger.
    assert "fragdenstaat.de" not in hosts
    assert "media.frag-den-staat.de" not in hosts


def test_the_media_host_is_a_default_instance_detail():
    assert "media.frag-den-staat.de" in config.allowed_hosts()


def test_extra_media_hosts_can_be_declared(monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    monkeypatch.setenv("FDS_MCP_MEDIA_HOSTS", "media.fragdenstaat.at, cdn.example.org")
    hosts = config.allowed_hosts()
    assert "media.fragdenstaat.at" in hosts and "cdn.example.org" in hosts


def test_the_token_does_not_follow_a_link_to_the_old_default(monkeypatch):
    """The security test. With .at configured, fragdenstaat.de is a foreign host."""
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    with pytest.raises(ForeignHost):
        FdsClient(token="SECRET").request(
            "GET", "https://fragdenstaat.de/api/v1/request/1/")


def test_a_download_url_from_the_old_default_is_refused_too(tmp_path, monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    with pytest.raises(ForeignHost):
        FdsClient(token="SECRET").download(
            "https://media.frag-den-staat.de/x.pdf", tmp_path / "x.pdf")


def test_the_client_targets_the_configured_instance(monkeypatch):
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    assert FdsClient().base == f"{AT}/api/v1"


@pytest.mark.live
def test_live_the_austrian_instance_answers_the_account_free_tools(monkeypatch):
    """Proof that the configuration is real and not decorative.

    This is the negative control for the whole change: it fails the moment someone
    hard-codes fragdenstaat.de again. Needs no account — fragdenstaat.at serves
    /api/v1/publicbody/ and /api/v1/law/ unauthenticated.
    """
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    with FdsClient() as client:
        found = client.search_authorities("Wien")
        assert found, "expected at least one Viennese authority"
        laws = client.get_authority(found[0]["id"]).get("laws") or []
        assert any("Informationsfreiheitsgesetz" in (law.get("name") or "")
                   for law in laws), laws


@pytest.mark.live
def test_live_the_austrian_instance_has_no_georegions(monkeypatch):
    """Documented gap, not a bug on our side.

    check_jurisdiction() resolves a place through /api/v1/georegion/. That table is
    empty on fragdenstaat.at (total_count 0 against 24216 on .de, measured
    2026-09-05), so the tool cannot work there however the client is configured.
    """
    monkeypatch.setenv("FDS_MCP_BASE_URL", AT)
    with FdsClient() as client:
        assert client.find_georegions("Graz") == []
