"""Routes + backend-client tests for aw-app-ddns.

The theme of this file is that the app is a PASS-THROUGH. Most of what could
go wrong here is the app deciding something it has no business deciding, so
several tests assert on what the app does NOT do.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ddns_app.backend_client import (
    BackendRequestFailed,
    BackendUnavailable,
    DnsBackend,
)
from ddns_app.routes import build_routes


@pytest.fixture()
def client():
    app = build_routes()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def linked(monkeypatch):
    monkeypatch.setenv("AW_BACKEND_URL", "https://api.example.test")
    monkeypatch.setenv("AW_WORKSPACE", "acme")
    monkeypatch.setenv("AW_WORKSPACE_HOST_TOKEN", "awlk_test")


def _stub(monkeypatch, method: str, payload: dict, status: int = 200):
    """Replace one httpx verb with a canned response, capturing the request."""
    seen: dict = {}

    def _call(url, *a, **kw):
        seen["url"] = url
        seen["json"] = kw.get("json")
        seen["headers"] = kw.get("headers")
        return httpx.Response(
            status, json=payload, request=httpx.Request(method.upper(), url)
        )

    monkeypatch.setattr(f"httpx.{method}", _call)
    return seen


# ----------------------------------------------------------------------
# The app never measures and never writes DNS.
# ----------------------------------------------------------------------

def test_app_ships_no_ip_measurement_and_no_dns_write():
    """The load-bearing property of this app, asserted against its source.

    If someone adds a "helpful" local measurement or a boto3 call here, the
    control plane stops being the only thing that decides what is published —
    which is the whole security argument of the design. An app measuring its
    own egress would also measure the CONTAINER's address, not the host's WAN,
    producing a record that looks right and receives no SIP.
    """
    import pathlib

    import ddns_app

    src = ""
    for p in pathlib.Path(ddns_app.__file__).parent.glob("*.py"):
        src += p.read_text()

    for forbidden in ("boto3", "route53", "change_resource_record_sets",
                      "ifconfig.me", "cdn-cgi/trace", "ipify"):
        assert forbidden not in src, (
            f"{forbidden!r} appears in the app — measuring an IP or writing DNS "
            "belongs in aw-backend, not here"
        )


def test_enable_sends_only_intent_never_an_address(client, linked, monkeypatch):
    """The request body carries a kind and an optional probe port. It must
    never carry an IP — aw-backend measures that itself."""
    seen = _stub(monkeypatch, "post", {"enabled": True, "published_value": "1.2.3.4"})

    client.post("/enable", json={"kind": "wan", "probe_port": 5060})

    assert seen["json"] == {"probe_port": 5060}
    body = json.dumps(seen["json"])
    assert "ip" not in body.lower()
    assert seen["url"].endswith("/api/workspaces/acme/dns-publications/wan")


# ----------------------------------------------------------------------
# Auth + URL shape.
# ----------------------------------------------------------------------

def test_calls_are_scoped_to_this_workspace_with_its_own_token(
    client, linked, monkeypatch
):
    seen = _stub(monkeypatch, "get", {"publications": []})
    client.get("/status")
    assert seen["url"] == "https://api.example.test/api/workspaces/acme/dns-publications"
    assert seen["headers"]["Authorization"] == "Bearer awlk_test"


def test_unlinked_workspace_says_so_rather_than_failing_opaquely(client, monkeypatch):
    for var in ("AW_BACKEND_URL", "AW_WORKSPACE", "AW_WORKSPACE_HOST_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    r = client.get("/status")

    assert r.status_code == 503
    assert r.json()["reason"] == "no_cloud_link"
    assert "link" in r.json()["error"].lower()


# ----------------------------------------------------------------------
# aw-backend's refusal messages must survive the hop.
# ----------------------------------------------------------------------

def test_cgnat_refusal_text_is_passed_through_verbatim(client, linked, monkeypatch):
    """That sentence IS the product — it is the whole answer to "why can't I
    turn this on". Collapsing it into a generic error would leave the user
    with nothing to act on."""
    msg = ("your internet connection hands out a carrier-grade NAT address "
           "(100.64.0.1, RFC 6598). ... Nothing has been published.")
    _stub(monkeypatch, "post", {"error": msg, "reason": "not_routable"}, status=409)

    r = client.post("/enable", json={})

    assert r.status_code == 409
    assert r.json()["error"] == msg
    assert r.json()["reason"] == "not_routable"


def test_hosted_refusal_is_passed_through(client, linked, monkeypatch):
    msg = "Not applicable — this workspace runs on TekFlox infrastructure, ..."
    _stub(monkeypatch, "post", {"error": msg, "reason": "hosted_placement"}, status=409)

    r = client.post("/enable", json={})

    assert r.status_code == 409
    assert r.json()["reason"] == "hosted_placement"
    assert "TekFlox infrastructure" in r.json()["error"]


def test_status_passes_through_the_panel_fields(client, linked, monkeypatch):
    _stub(monkeypatch, "get", {
        "slug": "acme", "eligible": True,
        "wan_name": "acme.workspace.ddns.tekflox.com",
        "publications": [{"kind": "wan", "enabled": True,
                          "published_value": "65.109.66.88",
                          "reachability": "timeout"}],
    })

    d = client.get("/status").json()

    assert d["wan_name"] == "acme.workspace.ddns.tekflox.com"
    assert d["publications"][0]["published_value"] == "65.109.66.88"


def test_disable_hits_the_delete_verb(client, linked, monkeypatch):
    seen = _stub(monkeypatch, "delete", {"enabled": False, "published_value": None})
    r = client.post("/disable", json={"kind": "wan"})
    assert r.status_code == 200
    assert seen["url"].endswith("/dns-publications/wan")


# ----------------------------------------------------------------------
# The panel.
# ----------------------------------------------------------------------

def test_panel_renders_and_supplies_its_own_padding(client):
    """An iframe panel is rendered cross-origin, so no host stylesheet reaches
    inside and padding on the <iframe> element would only clip it."""
    r = client.get("/panel/ddns")
    assert r.status_code == 200
    assert "padding: 12px" in r.text


def test_panel_never_renders_a_timeout_as_cgnat(client):
    """A probe timeout means "we cannot tell". Drawing it as a CGNAT detection
    would be a guess presented as a measurement."""
    body = client.get("/panel/ddns").text
    assert "could not verify" in body
    assert "CGNAT detected" not in body
