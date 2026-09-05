"""Paths, endpoints and hard limits.

Every constant in here is either taken from froide's source or was measured
against https://fragdenstaat.de . Nothing is guessed.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- endpoints (verified 2026-09-05) --------------------------------------
BASE_URL = "https://fragdenstaat.de"
API_URL = f"{BASE_URL}/api/v1"

AUTHORIZE_URL = f"{BASE_URL}/account/authorize/"
TOKEN_URL = f"{BASE_URL}/account/token/"
REVOKE_URL = f"{BASE_URL}/account/revoke_token/"
APPLICATIONS_URL = f"{BASE_URL}/account/applications/"
REGISTER_APPLICATION_URL = f"{BASE_URL}/account/applications/register/"

MAKE_REQUEST_FORM_URL = f"{BASE_URL}/anfrage-stellen/an/{{pb_id}}/"

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
