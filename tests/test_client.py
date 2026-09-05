"""HTTP client: URL building, jurisdiction resolution, pagination, prefill URLs."""

from __future__ import annotations

import pytest

from fds_mcp import rules
from fds_mcp.client import (
    AuthRequired,
    FdsClient,
    FdsError,
    TruncatedResult,
    make_request_url,
)
from fds_mcp.config import API_URL, BASE_URL


class FakeClient(FdsClient):
    """Records calls instead of performing them."""

    def __init__(self, pages, **kwargs):
        super().__init__(**kwargs)
        self.pages = list(pages)
        self.calls = []

    def request(self, method, path, *, params=None, payload=None):
        self.calls.append((method, path, dict(params or {})))
        return self.pages.pop(0)


def page(objects, limit=50):
    return {"meta": {"limit": limit, "total_count": len(objects)}, "objects": objects}


def test_base_url_is_the_documented_v1_endpoint():
    assert FdsClient().base == API_URL == "https://fragdenstaat.de/api/v1"


def test_paginate_stops_on_a_short_page():
    client = FakeClient([page([{"id": i} for i in range(50)]), page([{"id": 50}])])
    assert len(list(client.paginate("/request/"))) == 51


def test_paginate_raises_instead_of_truncating_silently():
    client = FakeClient([page([{"id": i} for i in range(50)])])
    with pytest.raises(TruncatedResult):
        list(client.paginate("/request/", max_items=10))


def test_paginate_deduplicates_across_pages():
    """LIMIT/OFFSET over an unsorted queryset can repeat rows — dedupe by id."""
    first = page([{"id": i} for i in range(50)])
    second = page([{"id": 49}, {"id": 50}])
    client = FakeClient([first, second])
    assert [o["id"] for o in client.paginate("/request/")][-2:] == [49, 50]


def test_list_requests_limit_does_not_raise():
    client = FakeClient([page([{"id": i} for i in range(50)])])
    assert len(client.list_requests(limit=10)) == 10


def test_paginate_advances_the_offset():
    client = FakeClient([page([{"id": i} for i in range(50)]), page([])])
    list(client.paginate("/request/"))
    assert [c[2]["offset"] for c in client.calls] == [0, 50]


def test_authorities_for_region_deduplicates():
    """The regions join returns the same body twice (observed for region 1899)."""
    duplicated = [{"id": 4929, "name": "VG Kaisersesch"},
                  {"id": 4929, "name": "VG Kaisersesch"}]
    client = FakeClient([page(duplicated)])
    assert len(client.authorities_for_region(1899)) == 1


def test_resolve_jurisdiction_passes_numbers_through():
    assert FakeClient([]).resolve_jurisdiction(6) == 6
    assert FakeClient([]).resolve_jurisdiction("6") == 6
    assert FakeClient([]).resolve_jurisdiction(None) is None


def test_resolve_jurisdiction_maps_slug_and_name():
    listing = page([{"id": 6, "name": "Rheinland-Pfalz", "slug": "rheinland-pfalz"}])
    assert FakeClient([listing]).resolve_jurisdiction("rheinland-pfalz") == 6
    assert FakeClient([listing]).resolve_jurisdiction("Rheinland-Pfalz") == 6


def test_resolve_jurisdiction_rejects_the_unknown():
    listing = page([{"id": 6, "name": "Rheinland-Pfalz", "slug": "rheinland-pfalz"}])
    with pytest.raises(FdsError):
        FakeClient([listing, listing]).resolve_jurisdiction("atlantis")


def test_whoami_without_a_token_fails_before_the_network():
    with pytest.raises(AuthRequired):
        FdsClient().whoami()


# --- prefilled form URL ---------------------------------------------------

def test_make_request_url_targets_the_form_and_carries_law_type():
    url = make_request_url(4929, "Betreff", "Text", law_type="IFG")
    assert url.startswith(f"{BASE_URL}/anfrage-stellen/an/4929/?")
    assert "law_type=IFG" in url
    assert "hide_publicbody=1" in url


def test_make_request_url_encodes_umlauts():
    assert "%C3%9C" in make_request_url(4929, "Übersicht", "Text")


def test_make_request_url_marks_a_non_public_request():
    """MakeRequestView.get_initial() reads public as "1"/"0", not as "True"/"False"."""
    assert "public=0" in make_request_url(4929, "Betreff", "Text", public=False)
    assert "public=1" in make_request_url(4929, "Betreff", "Text", public=True)


def test_make_request_url_uses_ref_not_reference():
    """froide reads the query key `ref`; `reference` would be ignored."""
    url = make_request_url(4929, "Betreff", "Text", reference="campaign:x")
    assert "ref=campaign" in url
    assert "reference=" not in url


def test_a_long_body_exceeds_the_measured_url_limit():
    """Measured 2026-09-05: above ~4096 bytes fragdenstaat.de answers HTTP 400."""
    url = make_request_url(4929, "Betreff", "Angaben " * 700, law_type="IFG")
    assert len(url) > rules.MAX_PREFILL_URL_LENGTH


# --------------------------------------------------------------------------
# host allowlist: absolute URLs and attachment file_urls come out of API
# responses, so they must not be able to redirect the bearer token elsewhere.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://evil.example/steal",
    "https://fragdenstaat.de.evil.example/steal",
    "http://127.0.0.1:8080/",
    "https://media.frag-den-staat.de.evil.example/x.pdf",
])
def test_request_refuses_a_foreign_absolute_url(url):
    from fds_mcp.client import ForeignHost

    with pytest.raises(ForeignHost):
        FdsClient(token="SECRET").request("GET", url)


@pytest.mark.parametrize("url", [
    "https://evil.example/x.pdf",
    "https://fragdenstaat.de@evil.example/x.pdf",
])
def test_download_refuses_a_foreign_file_url(tmp_path, url):
    from fds_mcp.client import ForeignHost

    with pytest.raises(ForeignHost):
        FdsClient(token="SECRET").download(url, tmp_path / "out.bin")


@pytest.mark.parametrize("url", [
    "https://fragdenstaat.de/api/v1/request/1/",
    "https://media.frag-den-staat.de/files/foi/1/x.pdf?token=abc",
])
def test_the_real_hosts_pass_the_allowlist(url):
    from fds_mcp.client import _host_allowed

    assert _host_allowed(url)


def test_a_foreign_base_url_is_refused_too(tmp_path):
    """base is a dataclass field; pointing it at an internal host must not work (SSRF)."""
    from fds_mcp.client import ForeignHost

    client = FdsClient(token="SECRET", base="http://169.254.169.254/latest/meta-data")
    with pytest.raises(ForeignHost):
        client.request("GET", "/iam/")
