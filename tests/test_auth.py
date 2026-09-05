"""OAuth handling: PKCE, redirect scheme policy, token storage permissions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat

import pytest

from fds_mcp import auth, config
from fds_mcp.auth import AuthError, ClientConfig, Tokens

# --- PKCE -----------------------------------------------------------------

def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = auth.make_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge


def test_pkce_verifier_length_is_within_the_rfc_range():
    verifier, _ = auth.make_pkce_pair()
    assert 43 <= len(verifier) <= 128


def test_pkce_pairs_are_not_reused():
    assert auth.make_pkce_pair()[0] != auth.make_pkce_pair()[0]


def test_authorization_url_carries_pkce_and_state():
    cfg = ClientConfig("abc", None, config.DEFAULT_REDIRECT_URI, ("read:user",))
    url = auth.authorization_url(cfg, "CHALLENGE", "STATE")
    assert url.startswith("https://fragdenstaat.de/account/authorize/?")
    for part in ("code_challenge=CHALLENGE", "code_challenge_method=S256",
                 "state=STATE", "response_type=code", "client_id=abc"):
        assert part in url


# --- redirect URI policy --------------------------------------------------

@pytest.mark.parametrize("uri", [
    "https://localhost:8765/callback",
    "fragdenstaat://callback",
])
def test_allowed_redirect_schemes_pass(uri):
    assert auth.validate_redirect_uri(uri) == uri


@pytest.mark.parametrize("uri", [
    "http://localhost:8765/callback",
    "urn:ietf:wg:oauth:2.0:oob",
    "myapp://callback",
])
def test_forbidden_redirect_schemes_are_rejected(uri):
    """fragdenstaat.de allows only https and fragdenstaat."""
    with pytest.raises(AuthError):
        auth.validate_redirect_uri(uri)


def test_the_default_redirect_uri_is_https_loopback():
    assert config.DEFAULT_REDIRECT_URI == "https://localhost:8765/callback"
    assert config.FALLBACK_REDIRECT_URI == "fragdenstaat://callback"


# --- token storage --------------------------------------------------------

def test_tokens_are_stored_with_mode_600(isolated_home):
    auth.save_tokens(Tokens("access", "refresh", 0.0, "read:user"))
    path = config.tokens_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_the_config_directory_is_not_world_readable(isolated_home):
    auth.save_tokens(Tokens("access", "refresh", 0.0))
    mode = stat.S_IMODE(os.stat(config.home()).st_mode)
    assert mode & 0o077 == 0


def test_tokens_round_trip(isolated_home):
    auth.save_tokens(Tokens("access", "refresh", 99.0, "read:user read:request"))
    loaded = auth.load_tokens()
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert loaded.scope == "read:user read:request"


def test_client_secret_is_stored_with_mode_600(isolated_home):
    auth.save_client_config(ClientConfig("id", "s3cret"))
    assert stat.S_IMODE(os.stat(config.client_config_path()).st_mode) == 0o600


def test_saving_a_forbidden_redirect_uri_fails(isolated_home):
    with pytest.raises(AuthError):
        auth.save_client_config(ClientConfig("id", None, "http://localhost/cb"))


def test_forget_tokens_removes_the_file(isolated_home):
    auth.save_tokens(Tokens("access", "refresh", 0.0))
    assert auth.forget_tokens() is True
    assert auth.load_tokens() is None
    assert auth.forget_tokens() is False


# --- secrets must not leak ------------------------------------------------

def test_repr_of_tokens_hides_the_secrets():
    text = repr(Tokens("SUPERSECRETACCESS", "SUPERSECRETREFRESH", 0.0))
    assert "SUPERSECRET" not in text
    assert "***" in text


def test_repr_of_client_config_hides_the_secret():
    text = repr(ClientConfig("public-id", "SUPERSECRET"))
    assert "SUPERSECRET" not in text
    assert "public-id" in text


def test_dataclass_field_of_the_client_hides_the_token():
    from fds_mcp.client import FdsClient

    assert "SUPERSECRET" not in repr(FdsClient(token="SUPERSECRET"))


def test_expiry_uses_the_refresh_margin():
    import time

    assert Tokens("a", "r", time.time() + 3600).expired is False
    assert Tokens("a", "r", time.time() + 5).expired is True


def test_from_response_derives_the_expiry():
    tokens = Tokens.from_response({"access_token": "a", "refresh_token": "r",
                                   "expires_in": 3600, "scope": "read:user"})
    assert tokens.expired is False
    assert tokens.scope == "read:user"


def test_access_token_without_login_fails_clearly(isolated_home):
    with pytest.raises(AuthError, match="Not logged in"):
        auth.access_token()


def test_load_client_config_without_configuration_points_at_registration(isolated_home):
    with pytest.raises(AuthError, match="applications/register"):
        auth.load_client_config()


def test_env_token_wins_over_the_store(isolated_home, monkeypatch):
    monkeypatch.setenv("FDS_TOKEN", "from-env")
    assert auth.access_token() == "from-env"


def test_stored_token_file_contains_no_client_secret(isolated_home):
    auth.save_tokens(Tokens("access", "refresh", 0.0))
    data = json.loads(config.tokens_path().read_text(encoding="utf-8"))
    assert "client_secret" not in data
