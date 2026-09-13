"""Standalone-mode boot smoke + the error branches the happy path misses."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from ddns_app.__main__ import build_standalone_app
from ddns_app.backend_client import BackendRequestFailed, DnsBackend


@pytest.fixture()
def linked(monkeypatch):
    monkeypatch.setenv("AW_BACKEND_URL", "https://api.example.test")
    monkeypatch.setenv("AW_WORKSPACE", "acme")
    monkeypatch.setenv("AW_WORKSPACE_HOST_TOKEN", "awlk_test")


def test_standalone_mounts_at_the_same_prefix_as_integrated():
    """One path shape in both modes — see routes.py."""
    client = TestClient(build_standalone_app(), raise_server_exceptions=False)
    r = client.get("/api/apps/ddns/panel/ddns")
    assert r.status_code == 200
    assert "Dynamic DNS" in r.text or "ddns" in r.text.lower()


def test_standalone_root_redirects_to_the_panel():
    client = TestClient(build_standalone_app(), follow_redirects=False)
    r = client.get("/")
    assert r.status_code in (302, 307)
    assert r.headers["location"].endswith("/api/apps/ddns/panel/ddns")


def test_main_binds_loopback_by_default(monkeypatch):
    """The routes carry a workspace host credential, so the default bind
    matters more here than for most apps."""
    seen = {}

    def _run(app, host, port):
        seen["host"] = host
        seen["port"] = port

    monkeypatch.setattr("uvicorn.run", _run)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("HOST", raising=False)

    from ddns_app.__main__ import main

    main()

    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 9410


def test_main_honours_port_and_host_env(monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: seen.update(
        host=host, port=port))
    monkeypatch.setenv("PORT", "9411")
    monkeypatch.setenv("HOST", "0.0.0.0")

    from ddns_app.__main__ import main

    main()

    assert seen == {"host": "0.0.0.0", "port": 9411}


# ----------------------------------------------------------------------
# backend_client error branches.
# ----------------------------------------------------------------------

def test_non_json_error_body_falls_back_to_raw_text(linked, monkeypatch):
    """aw-backend behind a proxy can answer HTML. The client must still
    surface something rather than raising a JSON decode error."""
    monkeypatch.setattr("httpx.get", lambda url, *a, **k: httpx.Response(
        502, text="<html>bad gateway</html>",
        request=httpx.Request("GET", url)))

    with pytest.raises(BackendRequestFailed) as e:
        DnsBackend().status()

    assert e.value.status_code == 502
    assert "bad gateway" in e.value.detail


def test_enable_raises_with_reason_on_error(linked, monkeypatch):
    monkeypatch.setattr("httpx.post", lambda url, *a, **k: httpx.Response(
        409, json={"error": "cgnat", "reason": "not_routable"},
        request=httpx.Request("POST", url)))

    with pytest.raises(BackendRequestFailed) as e:
        DnsBackend().enable()

    assert e.value.reason == "not_routable"


def test_disable_raises_with_reason_on_error(linked, monkeypatch):
    monkeypatch.setattr("httpx.delete", lambda url, *a, **k: httpx.Response(
        502, json={"error": "route53 down", "reason": "withdraw_failed"},
        request=httpx.Request("DELETE", url)))

    with pytest.raises(BackendRequestFailed) as e:
        DnsBackend().disable()

    assert e.value.reason == "withdraw_failed"


def test_unexpected_exception_becomes_a_502_not_a_500(linked, monkeypatch):
    """An httpx transport error (DNS failure, connection refused) must not
    escape as an unhandled 500 — the panel shows the message."""
    def _boom(*a, **k):
        raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr("httpx.get", _boom)
    client = TestClient(build_standalone_app(), raise_server_exceptions=False)

    r = client.get("/api/apps/ddns/status")

    assert r.status_code == 502
    assert r.json()["reason"] == "unexpected"


def test_enable_without_probe_port_sends_an_empty_body(linked, monkeypatch):
    seen = {}
    monkeypatch.setattr("httpx.post", lambda url, *a, **k: (
        seen.update(json=k.get("json")),
        httpx.Response(200, json={"enabled": True},
                       request=httpx.Request("POST", url)))[1])

    DnsBackend().enable()

    assert seen["json"] == {}


def test_disable_route_surfaces_a_failed_withdrawal(linked, monkeypatch):
    """A withdrawal that failed must reach the user as the 502 aw-backend
    sent, not a generic error — the record is still out there and they need
    to know a retry is needed."""
    monkeypatch.setattr("httpx.delete", lambda url, *a, **k: httpx.Response(
        502, json={"error": "could not withdraw the DNS record: route53 down",
                   "reason": "withdraw_failed"},
        request=httpx.Request("DELETE", url)))
    client = TestClient(build_standalone_app(), raise_server_exceptions=False)

    r = client.post("/api/apps/ddns/disable", json={"kind": "wan"})

    assert r.status_code == 502
    assert r.json()["reason"] == "withdraw_failed"
    assert "route53 down" in r.json()["error"]
