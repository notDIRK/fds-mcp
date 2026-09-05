"""Thin HTTP client for the FragDenStaat / froide REST API.

Deliberately minimal and READ-ONLY by default. Write operations exist but require
an explicit ``allow_write=True`` *and* a token. Security rule 7: any method other
than GET is refused while ``allow_write`` is not set.

Verified against fragdenstaat.de on 2026-09-05
(froide @ bc6c2fa, fragdenstaat_de @ 88bfbba).
"""

from __future__ import annotations

import itertools
import os
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .config import API_URL, BASE_URL, USER_AGENT
from .errors import FdsMcpError

READ_ONLY_METHODS = frozenset({"GET"})

# Hosts this client may ever send an OAuth bearer token to. ``file_url`` on an
# attachment and any absolute URL handed to ``request()`` come from the API response,
# not from us, so they are checked against this list before the Authorization header is
# attached. Without the check a single manipulated ``file_url`` would hand the user's
# access token to a third party.
ALLOWED_HOSTS = ("fragdenstaat.de", "media.frag-den-staat.de")


def _host_allowed(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


class FdsError(FdsMcpError):
    """Any failure while talking to fragdenstaat.de.

    ``status_code`` makes it possible to tell a 429 (rate limit) apart from a 401
    (token gone) or a 500 — the caller needs that distinction, a bare message does not
    carry it.
    """

    def __init__(self, message: str, status_code: int | None = None,
                 retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class TruncatedResult(FdsError):
    """The result list was longer than ``max_items`` — never truncate silently."""


class WriteBlocked(FdsError):
    """A write was attempted while the client was in read-only mode."""


class AuthRequired(FdsError):
    """The call needs an OAuth token and none was available."""


class ForeignHost(FdsError):
    """A URL pointed somewhere other than fragdenstaat.de. Refused, never followed."""


@dataclass
class FdsClient:
    """HTTP access to https://fragdenstaat.de/api/v1/.

    Args:
        token: a static bearer token. Prefer ``token_provider``.
        token_provider: callable returning a fresh access token (handles refresh).
        allow_write: must be True before any non-GET request is permitted.
    """

    token: str | None = field(default=None, repr=False)
    token_provider: Callable[[], str] | None = field(default=None, repr=False)
    allow_write: bool = False
    base: str = API_URL
    timeout: float = 30.0
    _client: httpx.Client | None = field(default=None, repr=False, compare=False)

    # ---------------- construction ----------------

    @classmethod
    def from_env(cls, *, allow_write: bool = False) -> FdsClient:
        """Client using ``FDS_TOKEN`` from the environment, if present."""
        return cls(token=os.environ.get("FDS_TOKEN"), allow_write=allow_write)

    @classmethod
    def authenticated(cls, *, allow_write: bool = False) -> FdsClient:
        """Client backed by the stored OAuth tokens (refreshing them as needed)."""
        from .auth import access_token

        return cls(token_provider=access_token, allow_write=allow_write)

    # ---------------- internals ----------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> FdsClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _current_token(self) -> str | None:
        if self.token_provider is not None:
            return self.token_provider()
        return self.token

    def request(self, method: str, path: str, *, params: dict | None = None,
                payload: dict | None = None) -> Any:
        """Perform one API call. Non-GET is refused unless ``allow_write`` is set."""
        method = method.upper()

        # --- security rule 7 -------------------------------------------------
        if method not in READ_ONLY_METHODS and not self.allow_write:
            raise WriteBlocked(
                f"{method} {path} blocked: this client is read-only. "
                "Construct FdsClient(allow_write=True) to permit writes."
            )
        token = self._current_token()
        if method not in READ_ONLY_METHODS and not token:
            raise AuthRequired(f"{method} {path} needs an OAuth token. Run: fds-mcp login")

        url = path if path.startswith("http") else f"{self.base}{path}"
        if not _host_allowed(url):
            raise ForeignHost(
                f"{method} {url} refused: {ALLOWED_HOSTS} are the only hosts this client "
                "talks to. Absolute URLs come out of API responses and are not trusted."
            )
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            resp = self._http().request(
                method, url, params=params, json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise FdsError(f"{method} {url} failed: {exc}") from exc

        if resp.status_code >= 400:
            raise FdsError(
                f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retry_after=resp.headers.get("Retry-After"),
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise FdsError(f"{method} {url} returned non-JSON content") from exc

    def paginate(self, path: str, params: dict | None = None, *, limit: int = 50,
                 max_items: int = 5000) -> Iterator[dict]:
        """Walk a limit/offset collection. Server PAGE_SIZE is 50.

        De-duplicates by ``id``: ``/message/`` and ``/attachment/`` apply a server-side
        ``.order_by()`` (froide/foirequest/api_views/message.py:54), and LIMIT/OFFSET over
        an unsorted queryset is not stable in PostgreSQL — the same row can appear on two
        pages while another never shows up.

        Reaching ``max_items`` raises :class:`TruncatedResult` rather than quietly
        returning a short list. Use ``itertools.islice`` when you deliberately want the
        first N.
        """
        params = dict(params or {})
        params["limit"] = limit
        offset = 0
        seen: set = set()
        while True:
            params["offset"] = offset
            page = self.request("GET", path, params=params)
            objects = page.get("objects", page.get("results", []))
            for obj in objects:
                key = obj.get("id", id(obj))
                if key in seen:
                    continue
                seen.add(key)
                yield obj
                if len(seen) >= max_items:
                    raise TruncatedResult(
                        f"{path}: more than {max_items} objects — narrow the filter or "
                        "raise max_items.")
            if not objects or len(objects) < limit:
                return
            offset += limit

    # ---------------- reading ----------------

    def search_authorities(self, query: str, **filters: Any) -> list[dict]:
        page = self.request("GET", "/publicbody/search/", params={"q": query, **filters})
        return page.get("objects", page.get("results", []))

    def get_authority(self, pb_id: int) -> dict:
        return self.request("GET", f"/publicbody/{pb_id}/")

    def get_law(self, law_id: int) -> dict:
        return self.request("GET", f"/law/{law_id}/")

    def list_jurisdictions(self) -> list[dict]:
        page = self.request("GET", "/jurisdiction/", params={"limit": 100})
        return page.get("objects", page.get("results", []))

    def resolve_jurisdiction(self, value: str | int | None) -> int | None:
        """Map ``6``, ``"6"``, ``"rheinland-pfalz"`` or ``"Rheinland-Pfalz"`` to the id.

        The API only accepts the numeric id — a slug yields HTTP 400
        ("Bitte eine gültige Auswahl treffen"), verified 2026-09-05.
        """
        if value in (None, ""):
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text.isdigit():
            return int(text)
        wanted = text.casefold()
        for j in self.list_jurisdictions():
            if wanted in (str(j.get("slug", "")).casefold(),
                          str(j.get("name", "")).casefold()):
                return int(j["id"])
        raise FdsError(
            f"Unknown jurisdiction {value!r}. Known slugs: "
            + ", ".join(sorted(str(j.get("slug")) for j in self.list_jurisdictions()))
        )

    def find_georegions(self, name: str) -> list[dict]:
        page = self.request("GET", "/georegion/", params={"name": name})
        return page.get("objects", page.get("results", []))

    def get_georegion(self, region_id: int) -> dict:
        return self.request("GET", f"/georegion/{region_id}/")

    def authorities_for_region(self, region_id: int, *, limit: int = 100) -> list[dict]:
        """Authorities whose ``regions`` cover this GeoRegion.

        The join returns the same body more than once (observed 2026-09-05 for region
        1899: total_count 2, both rows publicbody 4929) — ``paginate`` de-duplicates.
        """
        return list(itertools.islice(
            self.paginate("/publicbody/", {"regions": region_id}), limit))

    def list_requests(self, *, limit: int | None = None, **filters: Any) -> list[dict]:
        """Requests matching ``filters``; ``limit`` caps the result deliberately."""
        stream = self.paginate("/request/", filters)
        return list(stream) if limit is None else list(itertools.islice(stream, limit))

    def get_request(self, req_id: int) -> dict:
        return self.request("GET", f"/request/{req_id}/")

    def get_messages(self, req_id: int) -> list[dict]:
        return list(self.paginate("/message/", {"request": req_id}))

    def get_attachments(self, message_id: int) -> list[dict]:
        return list(self.paginate("/attachment/", {"belongs_to": message_id}))

    def get_attachment(self, attachment_id: int) -> dict:
        return self.request("GET", f"/attachment/{attachment_id}/")

    def whoami(self) -> dict:
        """GET /api/v1/user/ — 401 without a token (needs scope read:user)."""
        if not self._current_token():
            raise AuthRequired("GET /user/ needs an OAuth token. Run: fds-mcp login")
        return self.request("GET", "/user/")

    def download(self, url: str, target: Path) -> int:
        """Stream a file to ``target``. Reading only — no gate needed.

        ``url`` normally comes from an attachment's ``file_url``, i.e. out of an API
        response. It is checked against :data:`ALLOWED_HOSTS` before the bearer token is
        attached; httpx additionally strips the Authorization header on a cross-origin
        redirect, so the token cannot walk off the origin either way.
        """
        if not _host_allowed(url):
            raise ForeignHost(
                f"Refusing to download from {url}: not one of {ALLOWED_HOSTS}. "
                "An attachment file_url pointing elsewhere would leak the access token."
            )
        token = self._current_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        written = 0
        try:
            with self._http().stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise FdsError(f"GET {url} -> HTTP {resp.status_code}")
                with open(target, "wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
                        written += len(chunk)
        except httpx.HTTPError as exc:
            raise FdsError(f"GET {url} failed: {exc}") from exc
        return written

    # ---------------- writing ----------------

    def create_request(self, *, publicbody_ids: list[int], subject: str, body: str,
                       public: bool = True, full_text: bool = False,
                       tags: list[str] | None = None, reference: str = "") -> dict:
        """POST /api/v1/request/ — SENDS THE E-MAIL TO THE AUTHORITY IMMEDIATELY.

        There is no draft mode in the API and there is no undo. More than one id in
        ``publicbodies`` creates a FoiProject (bulk request), not a single request.
        """
        payload: dict[str, Any] = {
            "publicbodies": publicbody_ids,
            "subject": subject,
            "body": body,
            "public": public,
            "full_text": full_text,
        }
        if tags:
            payload["tags"] = tags
        if reference:
            payload["reference"] = reference
        return self.request("POST", "/request/", payload=payload)


    def find_request_by_slug(self, slug: str) -> dict | None:
        """Resolve a request by slug.

        ``POST /request/`` returns only ``{"status", "url"}`` — no id. FoiRequestFilter
        has ``slug`` in Meta.fields, so the slug is the way back to the object.
        """
        hits = self.list_requests(slug=slug, limit=2)
        return hits[0] if hits else None

    def set_request_law(self, req_id: int, law_id: int) -> dict:
        """PATCH the legal basis of an existing request.

        Verified writable on 2026-09-05: patching a non-existent law URI returns 400 with
        a ``law`` key, which a read-only field would not do. froide's own equivalent is
        ConcreteLawForm, which narrows a request filed under a meta act to one of its
        combined acts.

        Note that ``due_date`` is computed at creation and is NOT recalculated here.
        """
        return self.request(
            "PATCH", f"/request/{int(req_id)}/",
            payload={"law": f"{self.base}/law/{int(law_id)}/"},
        )


def make_request_url(pb_id: int, subject: str, body: str, *, law_type: str | None = None,
                     public: bool = True, hide_publicbody: bool = True,
                     reference: str = "", tags: str = "") -> str:
    """Build a prefilled web form URL. Sending stays with the human.

    This is the only route on which the legal basis (``law_type``) can be chosen at
    creation time — the REST API's MakeRequestSerializer has no such field.

    Parameter names follow ``MakeRequestView.get_initial()``
    (froide/foirequest/views/make_request.py:94-127): the query key is ``ref`` (not
    ``reference``) and ``redirect`` (not ``redirect_url``); ``public`` and ``full_text``
    are read as "1"/"0". ``address`` and ``language`` are NOT read from the query string.
    """
    params: dict[str, str] = {"subject": subject, "body": body}
    if law_type:
        params["law_type"] = law_type
    params["public"] = "1" if public else "0"
    if hide_publicbody:
        params["hide_publicbody"] = "1"
    if reference:
        params["ref"] = reference
    if tags:
        params["tags"] = tags
    return f"{BASE_URL}/anfrage-stellen/an/{pb_id}/?{urllib.parse.urlencode(params)}"
