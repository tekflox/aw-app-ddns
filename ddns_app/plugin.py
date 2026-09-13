"""Entrypoint referenced by aw-app.json's runtime.entrypoint
("ddns_app.plugin:DdnsAppPlugin").

This app is deliberately one of the smallest Tier-1 apps in the workspace: it
registers a backend sub-app through the gated ``ctx.routes`` facade
(capability ``routes:register``) and nothing else. No system CLIs, no owned
tables, no background task.

That is the design. The consent state lives in aw-backend's
``DnsPublication`` table rather than here, because the published DNS record
has to outlive this app — an uninstall that silently left a home address
resolving would be the worst failure this feature can have, and an app cannot
withdraw a record after it stops existing. aw-backend withdraws on disable,
on workspace delete, on remote-host revoke, and on THIS APP being uninstalled
(``app_installs.py``'s ``_teardown_for_app``, keyed on ``app_id == "ddns"``)
for the same reason. Uninstall was the one that was missing at first, and it
is the worst of the four to miss: nothing is left rendering the row, so the
record just resolves forever with no way to see it from inside the product.
"""

from __future__ import annotations

import logging

from . import routes as routes_mod

log = logging.getLogger("aw_apps.ddns")


class DdnsAppPlugin:
    async def activate(self, ctx) -> None:
        ctx.routes.register(routes_mod.build_routes())
        log.info("ddns activated: routes mounted at /api/apps/ddns")

    async def deactivate(self) -> None:
        # Nothing to undo here. In particular this does NOT withdraw the DNS
        # record: deactivate also runs on an ordinary workspace restart, and
        # taking someone's name out of DNS every time the workspace reboots
        # would be a worse bug than the one it looks like it prevents.
        # Real withdrawal is aw-backend's, on disable/delete/revoke/uninstall.
        log.info("ddns deactivated")
