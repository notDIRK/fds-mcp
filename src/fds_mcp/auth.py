"""OAuth 2.0 Authorization Code + PKCE against fragdenstaat.de.

fragdenstaat.de supports exactly two authentication schemes for the REST API:
OAuth2 bearer tokens and session cookies. There is no personal API key and — despite
what ``froide/docs/api.rst`` claims — no Basic Auth.

Redirect URI schemes are restricted server-side to ``https`` and ``fragdenstaat``
(``OAUTH2_PROVIDER.ALLOWED_REDIRECT_URI_SCHEMES``); ``http://localhost/...`` is rejected
when the application is registered. Hence:

  * default  ``https://localhost:8765/callback`` — a local HTTPS listener with a
    self-signed certificate that this module generates on first use;
  * fallback ``fragdenstaat://callback`` — the browser hands the code to nothing, so
    the user pastes the redirected URL back in.

Tokens live in ``~/.config/fds-mcp/tokens.json`` with mode 0600. They are never logged,
never echoed and never written anywhere else.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import config
from .config import (
    ALLOWED_REDIRECT_SCHEMES,
    AUTHORIZE_URL,
    DEFAULT_CALLBACK_HOST,
    DEFAULT_REDIRECT_URI,
    DEFAULT_SCOPES,
    FALLBACK_REDIRECT_URI,
    REVOKE_URL,
    TOKEN_REFRESH_MARGIN,
    TOKEN_URL,
    USER_AGENT,
)
from .errors import FdsMcpError


class AuthError(FdsMcpError):
    """Anything that goes wrong during login or refresh."""


# --------------------------------------------------------------------------
# secret-bearing files
# --------------------------------------------------------------------------

def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON with mode 0600, creating the file privately from the start."""
    config.ensure_home()
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# client registration data (client_id, optional secret)
# --------------------------------------------------------------------------

@dataclass
class ClientConfig:
    client_id: str
    client_secret: str | None = None
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    def __repr__(self) -> str:  # keep the secret out of tracebacks and logs
        return (f"ClientConfig(client_id={self.client_id!r}, "
                f"client_secret={'***' if self.client_secret else None}, "
                f"redirect_uri={self.redirect_uri!r}, scopes={self.scopes!r})")


def save_client_config(cfg: ClientConfig) -> Path:
    validate_redirect_uri(cfg.redirect_uri)
    path = config.client_config_path()
    _write_private_json(path, {
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "redirect_uri": cfg.redirect_uri,
        "scopes": list(cfg.scopes),
    })
    return path


def load_client_config() -> ClientConfig:
    """Load the OAuth application data. Environment wins over the config file."""
    data = _read_json(config.client_config_path()) or {}
    client_id = os.environ.get("FDS_MCP_CLIENT_ID") or data.get("client_id")
    if not client_id:
        raise AuthError(
            "No OAuth client_id configured. Register an application at "
            f"{config.REGISTER_APPLICATION_URL} (client type 'public', grant type "
            "'authorization-code', redirect URI "
            f"'{DEFAULT_REDIRECT_URI}') and then run:  fds-mcp configure --client-id <id>"
        )
    secret = os.environ.get("FDS_MCP_CLIENT_SECRET") or data.get("client_secret")
    redirect = (os.environ.get("FDS_MCP_REDIRECT_URI")
                or data.get("redirect_uri") or DEFAULT_REDIRECT_URI)
    scopes = data.get("scopes") or list(DEFAULT_SCOPES)
    if os.environ.get("FDS_MCP_SCOPES"):
        scopes = os.environ["FDS_MCP_SCOPES"].split()
    return ClientConfig(client_id=client_id, client_secret=secret or None,
                        redirect_uri=redirect, scopes=tuple(scopes))


def validate_redirect_uri(uri: str) -> str:
    scheme = urllib.parse.urlparse(uri).scheme
    if scheme not in ALLOWED_REDIRECT_SCHEMES:
        raise AuthError(
            f"Redirect URI scheme {scheme!r} is not allowed by fragdenstaat.de. "
            f"Allowed schemes: {', '.join(ALLOWED_REDIRECT_SCHEMES)}. "
            f"Use {DEFAULT_REDIRECT_URI} or {FALLBACK_REDIRECT_URI}."
        )
    return uri


# --------------------------------------------------------------------------
# token store
# --------------------------------------------------------------------------

@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str = ""
    token_type: str = "Bearer"
    obtained_at: float = 0.0

    def __repr__(self) -> str:  # never leak tokens into logs or tracebacks
        return (f"Tokens(expires_at={self.expires_at}, scope={self.scope!r}, "
                "access_token=***, refresh_token=***)")

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - TOKEN_REFRESH_MARGIN

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
            "obtained_at": self.obtained_at,
        }

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> Tokens:
        now = time.time()
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=now + float(payload.get("expires_in", 3600)),
            scope=payload.get("scope", ""),
            token_type=payload.get("token_type", "Bearer"),
            obtained_at=now,
        )


def save_tokens(tokens: Tokens) -> Path:
    path = config.tokens_path()
    _write_private_json(path, tokens.to_dict())
    return path


def load_tokens() -> Tokens | None:
    data = _read_json(config.tokens_path())
    if not data:
        return None
    return Tokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=float(data.get("expires_at", 0)),
        scope=data.get("scope", ""),
        token_type=data.get("token_type", "Bearer"),
        obtained_at=float(data.get("obtained_at", 0)),
    )


def forget_tokens() -> bool:
    path = config.tokens_path()
    if path.exists():
        path.unlink()
        return True
    return False


# --------------------------------------------------------------------------
# PKCE
# --------------------------------------------------------------------------

def make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for method S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorization_url(cfg: ClientConfig, challenge: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# --------------------------------------------------------------------------
# token endpoint
# --------------------------------------------------------------------------

def _post_token(data: dict[str, str]) -> dict[str, Any]:
    try:
        resp = httpx.post(TOKEN_URL, data=data, timeout=30.0,
                          headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        raise AuthError(f"Token endpoint unreachable: {exc}") from exc
    if resp.status_code >= 400:
        # The body may echo request parameters; show only the error fields.
        try:
            payload = resp.json()
            detail = payload.get("error_description") or payload.get("error") or ""
        except ValueError:
            detail = ""
        raise AuthError(f"Token endpoint returned HTTP {resp.status_code}. {detail}".strip())
    return resp.json()


def exchange_code(cfg: ClientConfig, code: str, verifier: str) -> Tokens:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "code_verifier": verifier,
    }
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    return Tokens.from_response(_post_token(data))


def refresh(cfg: ClientConfig, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise AuthError("No refresh token stored. Run: fds-mcp login")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens.refresh_token,
        "client_id": cfg.client_id,
    }
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    fresh = Tokens.from_response(_post_token(data))
    if not fresh.refresh_token:
        fresh.refresh_token = tokens.refresh_token
    return fresh


def revoke(cfg: ClientConfig, tokens: Tokens) -> None:
    data = {"token": tokens.access_token, "client_id": cfg.client_id}
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret
    try:
        httpx.post(REVOKE_URL, data=data, timeout=30.0,
                   headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:  # pragma: no cover - network path
        raise AuthError(f"Revocation endpoint unreachable: {exc}") from exc


def access_token() -> str:
    """Return a usable access token, refreshing transparently.

    Refresh tokens on fragdenstaat.de are valid for 180 days.
    """
    env = os.environ.get("FDS_TOKEN")
    if env:
        return env
    tokens = load_tokens()
    if tokens is None:
        raise AuthError("Not logged in. Run: fds-mcp login")
    if not tokens.expired:
        return tokens.access_token
    cfg = load_client_config()
    tokens = refresh(cfg, tokens)
    save_tokens(tokens)
    return tokens.access_token


# --------------------------------------------------------------------------
# local HTTPS callback listener
# --------------------------------------------------------------------------

def ensure_self_signed_cert() -> tuple[Path, Path]:
    """Create (or reuse) a self-signed certificate for the local callback listener.

    fragdenstaat.de refuses plain-http redirect URIs, so the loopback listener has to
    speak HTTPS. openssl is used so that the package needs no crypto dependency;
    if it is missing, callers should fall back to the manual paste flow.
    """
    cert, key = config.tls_cert_path(), config.tls_key_path()
    if cert.exists() and key.exists():
        return cert, key
    if shutil.which("openssl") is None:
        raise AuthError(
            "openssl not found — cannot create the local HTTPS certificate. "
            f"Use the manual flow instead: fds-mcp login --manual "
            f"(redirect URI {FALLBACK_REDIRECT_URI})"
        )
    config.ensure_home()
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "825",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AuthError(f"openssl failed to create the callback certificate: "
                        f"{proc.stderr.strip()[:400]}")
    os.chmod(key, 0o600)
    os.chmod(cert, 0o600)
    return cert, key


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        query = urllib.parse.urlparse(self.path).query
        parsed = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        type(self).result.update(parsed)
        body = (b"<html><body><h1>fds-mcp</h1><p>You can close this tab and return "
                b"to the terminal.</p></body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # keep the code out of stderr
        return


def wait_for_callback(port: int, timeout: float = 300.0) -> dict[str, str]:
    """Serve exactly one HTTPS request on loopback and return its query parameters."""
    cert, key = ensure_self_signed_cert()
    handler = type("_Handler", (_CallbackHandler,), {"result": {}})
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server = http.server.HTTPServer((DEFAULT_CALLBACK_HOST, port), handler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if not handler.result:
        raise AuthError(f"No OAuth callback received within {timeout:.0f}s.")
    return dict(handler.result)


# --------------------------------------------------------------------------
# the login flow
# --------------------------------------------------------------------------

def login(*, manual: bool = False, open_browser: bool = True,
          port: int = config.DEFAULT_CALLBACK_PORT, out=sys.stderr) -> Tokens:
    """Run the full Authorization-Code + PKCE flow and store the tokens.

    ``manual=True`` uses ``fragdenstaat://callback`` and asks the user to paste the
    redirected URL — the flow for machines without a browser or without openssl.
    """
    cfg = load_client_config()
    if manual:
        cfg = ClientConfig(cfg.client_id, cfg.client_secret,
                           FALLBACK_REDIRECT_URI, cfg.scopes)
    validate_redirect_uri(cfg.redirect_uri)

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorization_url(cfg, challenge, state)

    print("Open this URL in your browser and approve the requested scopes:", file=out)
    print(f"\n  {url}\n", file=out)
    if open_browser and not manual:
        import webbrowser

        webbrowser.open(url)

    if manual:
        print("After approving you will be redirected to a "
              f"{FALLBACK_REDIRECT_URI} URL that your browser cannot open.", file=out)
        print("Copy that whole URL from the address bar and paste it here.", file=out)
        pasted = input("Redirect URL: ").strip()
        params = {k: v[0] for k, v in
                  urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()}
    else:
        print(f"Waiting for the callback on https://localhost:{port}/callback ...",
              file=out)
        print("Your browser will warn about the self-signed certificate — that is the "
              "local listener; accept it.", file=out)
        params = wait_for_callback(port)

    if params.get("error"):
        raise AuthError(f"Authorization denied: {params['error']} "
                        f"{params.get('error_description', '')}".strip())
    # bytes, not str: compare_digest() raises TypeError on non-ASCII str, and the state
    # in the callback is whatever the redirect carried.
    if not secrets.compare_digest(params.get("state", "").encode("utf-8"),
                                  state.encode("utf-8")):
        raise AuthError("OAuth state mismatch — aborting (possible CSRF).")
    code = params.get("code")
    if not code:
        raise AuthError("No authorization code in the callback.")

    tokens = exchange_code(cfg, code, verifier)
    path = save_tokens(tokens)
    print(f"Tokens stored in {path} (mode 0600). Scopes: {tokens.scope}", file=out)
    return tokens
