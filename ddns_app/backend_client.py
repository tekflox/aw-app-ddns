"""HTTP client to aw-backend's ``/api/workspaces/{slug}/dns-publications``.

Auth: the workspace's OWN host credential, ``AW_WORKSPACE_HOST_TOKEN`` (an
``awlk_`` token minted by the aw-remote-host ``/link`` handshake and present
in this process's environment). aw-backend's ``require_workspace_actor``
accepts it only for the slug in the URL, so this app can act on its own
workspace and no other. Same pattern as ``aw-app-secrets``'s
``backend_client.py`` — no new credential type is invented here.

This module is deliberately thin. It measures nothing and it writes no DNS:
both of those live in aw-backend, because the control plane must decide what
gets published rather than trusting a value posted from a machine its owner
controls. See the app's README for why.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("aw_apps.ddns")

DEFAULT_TIMEOUT = 45.0  # an enable triggers a real measurement on the host


class BackendUnavailable(RuntimeError):
    """This workspace has no cloud link, so there is no control plane to ask."""


class BackendRequestFailed(RuntimeError):
    """aw-backend answered with a non-2xx.

    Carries the real status and the message aw-backend wrote, because those
    messages ARE the product here — "your ISP gives you a carrier-grade NAT
    address" is the whole answer to "why is this off", and httpx's generic
    "Client error '409 Conflict'" would throw it away.
    """

    def __init__(self, status_code: int, detail: str, reason: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        self.reason = reason
        super().__init__(f"aw-backend ({status_code}): {detail}")


def _detail(r: httpx.Response) -> tuple[str, str]:
    try:
        body = r.json()
    except Exception:
        return r.text[:300], ""
    return (
        body.get("error") or body.get("detail") or r.text[:300],
        body.get("reason") or "",
    )


class DnsBackend:
    def __init__(self, backend_url: str | None = None, workspace: str | None = None,
                 token: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.backend_url = (backend_url or os.environ.get("AW_BACKEND_URL", "")).rstrip("/")
        self.workspace = workspace or os.environ.get("AW_WORKSPACE", "")
        self.token = token or os.environ.get("AW_WORKSPACE_HOST_TOKEN", "")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.backend_url and self.token and self.workspace)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _base(self) -> str:
        return f"{self.backend_url}/api/workspaces/{self.workspace}/dns-publications"

    def _require(self) -> None:
        if not self.configured:
            raise BackendUnavailable(
                "no cloud link: AW_BACKEND_URL, AW_WORKSPACE and "
                "AW_WORKSPACE_HOST_TOKEN must all be set. A workspace that never "
                "completed the aw-remote-host /link handshake has no control "
                "plane to publish through."
            )

    def status(self) -> dict:
        self._require()
        r = httpx.get(self._base(), headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            detail, reason = _detail(r)
            raise BackendRequestFailed(r.status_code, detail, reason)
        return r.json()

    def enable(self, kind: str = "wan", probe_port: int | None = None) -> dict:
        self._require()
        body: dict = {}
        if probe_port:
            body["probe_port"] = int(probe_port)
        r = httpx.post(f"{self._base()}/{kind}", json=body,
                       headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            detail, reason = _detail(r)
            raise BackendRequestFailed(r.status_code, detail, reason)
        return r.json()

    def disable(self, kind: str = "wan") -> dict:
        self._require()
        r = httpx.delete(f"{self._base()}/{kind}",
                         headers=self._headers(), timeout=self.timeout)
        if r.status_code >= 400:
            detail, reason = _detail(r)
            raise BackendRequestFailed(r.status_code, detail, reason)
        return r.json()
