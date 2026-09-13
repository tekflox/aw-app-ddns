"""Standalone entrypoint (ADR Decision 4) — run this app WITHOUT the
aw-workspace runtime, e.g. to develop the panel on its own:

    python -m ddns_app                # binds 127.0.0.1:9410 (default)
    PORT=9411 python -m ddns_app

Mounts the SAME ``build_routes()`` sub-app at the SAME prefix used in
integrated mode (``/api/apps/ddns``), so client code and docs never need a
mode-specific path — see ``routes.py``. The panel is served by the sub-app
itself at ``/api/apps/ddns/panel/ddns``; this app ships no JS bundle, so
there is no ``ui/dist`` to mount.

It still talks to a REAL aw-backend — set ``AW_BACKEND_URL``,
``AW_WORKSPACE`` and ``AW_WORKSPACE_HOST_TOKEN`` in the environment, or every
route answers 503 "no cloud link", which is the honest result rather than a
mock.

Auth: standalone has **no** ``IdentityGuard`` — that is aw-workspace runtime
machinery, not app code. Binds ``127.0.0.1`` only, which matters more here
than for most apps: the routes below carry a workspace host credential.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from .routes import build_routes


def build_standalone_app() -> FastAPI:
    app = FastAPI(title="ddns (standalone)")
    app.mount("/api/apps/ddns", build_routes())

    @app.get("/")
    async def _root():
        return RedirectResponse("/api/apps/ddns/panel/ddns")

    return app


def main() -> None:
    port = int(os.environ.get("PORT", "9410"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(build_standalone_app(), host=host, port=port)


if __name__ == "__main__":
    main()
