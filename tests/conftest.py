"""Shared fixtures.

Network access is off by default. Tests marked ``live`` are the only ones allowed to
talk to fragdenstaat.de, and only if pytest-socket is installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

EXAMPLE_DRAFT = ROOT / "examples" / "request-draft.yaml"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/.config/fds-mcp during a test run."""
    home = tmp_path / "fds-mcp-home"
    home.mkdir()
    monkeypatch.setenv("FDS_MCP_HOME", str(home))
    monkeypatch.delenv("FDS_TOKEN", raising=False)
    monkeypatch.delenv("FDS_MCP_CLIENT_ID", raising=False)
    monkeypatch.delenv("FDS_MCP_CLIENT_SECRET", raising=False)
    return home


@pytest.fixture
def valid_request():
    """A minimal, rule-conforming draft used as the base for mutations."""
    return {
        "status": "draft",
        "submit_via": "web_form",
        "public": True,
        "full_text": False,
        "subject": "Antrag nach dem LTranspG - Benutzungsordnung Schutzhuette",
        "body": "Angaben zur Benutzungsordnung der Schutzhuette, im Einzelnen die "
                "geltende Fassung und alle Aenderungen seit 2025.",
        "publicbody": {
            "id": 4929,
            "name": "Verbandsgemeindeverwaltung Kaisersesch",
            "ermittelt_ueber": "/api/v1/publicbody/search/?q=Kaisersesch",
        },
        "law": {"wunsch_id": 16, "wunsch_law_type": "IFG", "api_default_id": 18},
    }


@pytest.fixture
def submittable_draft(valid_request):
    """A draft that passes every submit gate — the baseline the gate tests mutate.

    Note ``wunsch_id == api_default_id``: only then may the API be used at all, because
    MakeRequestSerializer cannot set law_type.
    """
    draft = dict(valid_request)
    draft["status"] = "approved"
    draft["submit_via"] = "api"
    draft["law"] = {"wunsch_id": 18, "wunsch_law_type": None, "api_default_id": 18}
    draft["confirmation_token"] = "SEND-DEADBEEF"
    return draft


@pytest.fixture
def draft_file(tmp_path, submittable_draft):
    """The submittable draft, written to disk."""
    from fds_mcp import drafts

    path = tmp_path / "draft.yaml"
    drafts.save(submittable_draft, path)
    return path


@pytest.fixture
def example_draft():
    import yaml

    with open(EXAMPLE_DRAFT, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="session")
def client():
    from fds_mcp.client import FdsClient

    return FdsClient()


@pytest.fixture(autouse=True)
def _network_only_for_live_tests(request):
    """pytest-socket blocks the network globally; ``live`` tests may reach fragdenstaat.de.

    Kept from the prototype repo, including the deliberate absence of a
    ``disable_socket()`` in the teardown: pytest-socket resets its own state per test and
    switching it off by hand collides with pytest-asyncio's event_loop teardown.
    """
    try:
        import pytest_socket as ps
    except ImportError:  # pytest-socket is optional
        yield
        return

    if not request.node.get_closest_marker("live"):
        ps.disable_socket(allow_unix_socket=True)
        yield
        return

    import socket

    ps.enable_socket()
    allowed = {info[4][0] for info in socket.getaddrinfo("fragdenstaat.de", 443)}
    ps.socket_allow_hosts(sorted(allowed), allow_unix_socket=True)
    yield
