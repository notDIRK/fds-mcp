"""Paths, endpoints and hard limits.

Every constant in here is either taken from froide's source or was measured
against https://fragdenstaat.de . Nothing is guessed.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path

from .errors import FdsMcpError


class ConfigError(FdsMcpError):
    """The environment asks for something this server must not do."""


# --- which instance ------------------------------------------------------
# froide powers more than one public portal; its own README names the German and the
# Austrian site. Everything here is measured against https://fragdenstaat.de , and that
# stays the default, but the instance is a setting rather than a literal because the
# read-only tools work unchanged against a sibling installation.
#
# It is deliberately a function and not a module constant. A constant is read once at
# import time, which would make the value a matter of import order and would leave the
# host guard below pointing at whatever was true first.
DEFAULT_BASE_URL = "https://fragdenstaat.de"

# Attachments on fragdenstaat.de are served from a separate host. That is a property of
# that one deployment, not of froide, so it must not travel to another instance —
# widening the host guard is exactly how a bearer token leaves for a stranger.
DEFAULT_MEDIA_HOSTS = ("media.frag-den-staat.de",)


def base_url() -> str:
    """The froide instance to talk to. ``FDS_MCP_BASE_URL`` overrides the default.

    Refuses anything the bearer token has no business reaching: a scheme other than
    https, embedded credentials, a path, or a missing host.
    """
    raw = (os.environ.get("FDS_MCP_BASE_URL") or DEFAULT_BASE_URL).strip()
    url = raw.rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ConfigError(
            f"FDS_MCP_BASE_URL must use https, got {raw!r}. The bearer token is sent to "
            "this host."
        )
    if not parts.hostname:
        raise ConfigError(f"FDS_MCP_BASE_URL has no host: {raw!r}")
    if parts.username or parts.password:
        raise ConfigError(
            "FDS_MCP_BASE_URL must not carry credentials. Put the token in the "
            "keyring or FDS_TOKEN, never in a URL."
        )
    if parts.path or parts.query or parts.fragment:
        raise ConfigError(
            f"FDS_MCP_BASE_URL must be an origin without a path, got {raw!r}. Every "
            "route in this client is appended to it."
        )
    return url


def is_default_instance() -> bool:
    """Are we talking to fragdenstaat.de itself?"""
    return base_url() == DEFAULT_BASE_URL


def api_url() -> str:
    return f"{base_url()}/api/v1"


def authorize_url() -> str:
    return f"{base_url()}/account/authorize/"


def token_url() -> str:
    return f"{base_url()}/account/token/"


def revoke_url() -> str:
    return f"{base_url()}/account/revoke_token/"


def applications_url() -> str:
    return f"{base_url()}/account/applications/"


def register_application_url() -> str:
    return f"{base_url()}/account/applications/register/"


def make_request_form_url(pb_id: object) -> str:
    """The prefilled web form. The path is froide's, not a fragdenstaat.de theme detail —
    verified 2026-09-05: fragdenstaat.at answers 200 on /anfrage-stellen/ and 404 on
    /make-request/."""
    return f"{base_url()}/anfrage-stellen/an/{pb_id}/"


def media_hosts() -> tuple[str, ...]:
    """Extra hosts that serve attachments, comma-separated in ``FDS_MCP_MEDIA_HOSTS``."""
    raw = os.environ.get("FDS_MCP_MEDIA_HOSTS")
    if raw:
        return tuple(h.strip() for h in raw.split(",") if h.strip())
    return DEFAULT_MEDIA_HOSTS if is_default_instance() else ()


def allowed_hosts() -> tuple[str, ...]:
    """Every host this client may send the bearer token to.

    Derived from the configured instance, never a literal: an absolute URL out of an API
    response is untrusted input, and the check that stops it has to move with the
    setting or it stops meaning anything.
    """
    host = urllib.parse.urlsplit(base_url()).hostname or ""
    return (host, *media_hosts())


USER_AGENT = "fds-mcp/0.1 (+https://github.com/notDIRK/fds-mcp)"

# --- OAuth ----------------------------------------------------------------
# OAUTH2_PROVIDER.ALLOWED_REDIRECT_URI_SCHEMES on fragdenstaat.de is exactly
# {"https", "fragdenstaat"} — http://localhost/... is rejected at registration.
ALLOWED_REDIRECT_SCHEMES = ("https", "fragdenstaat")
DEFAULT_REDIRECT_URI = "https://localhost:8765/callback"
FALLBACK_REDIRECT_URI = "fragdenstaat://callback"
DEFAULT_CALLBACK_HOST = "127.0.0.1"
DEFAULT_CALLBACK_PORT = 8765

# froide/settings.py:511
ALL_SCOPES = (
    "read:user",
    "read:profile",
    "read:email",
    "read:request",
    "make:request",
    "write:request",
    "write:message",
    "write:attachment",
    "follow:request",
    "read:document",
)
# The minimum this server needs. Nothing here grants deletion.
DEFAULT_SCOPES = ("read:user", "read:request", "make:request")

# REFRESH_TOKEN_EXPIRE_SECONDS on fragdenstaat.de: 180 days.
REFRESH_TOKEN_LIFETIME_DAYS = 180
# Refresh this many seconds before the access token actually expires.
TOKEN_REFRESH_MARGIN = 60


def home() -> Path:
    """Configuration/state directory. Override with ``FDS_MCP_HOME`` (tests do)."""
    override = os.environ.get("FDS_MCP_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / "fds-mcp"


def ensure_home() -> Path:
    path = home()
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:  # pragma: no cover - exotic filesystems
        pass
    return path


def tokens_path() -> Path:
    return home() / "tokens.json"


def client_config_path() -> Path:
    return home() / "config.json"


def throttle_path() -> Path:
    return home() / "throttle.json"


def tls_cert_path() -> Path:
    return home() / "callback-cert.pem"


def tls_key_path() -> Path:
    return home() / "callback-key.pem"
