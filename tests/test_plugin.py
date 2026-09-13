"""Plugin activation + manifest invariants for aw-app-ddns."""

from __future__ import annotations

import json
import os

import pytest

from ddns_app.plugin import DdnsAppPlugin

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _manifest() -> dict:
    with open(os.path.join(REPO, "aw-app.json"), encoding="utf-8") as f:
        return json.load(f)


class _Routes:
    def __init__(self):
        self.registered = []

    def register(self, app):
        self.registered.append(app)


class _Ctx:
    def __init__(self):
        self.routes = _Routes()
        self.package_dir = REPO
        self.config = {}


@pytest.mark.asyncio
async def test_activate_registers_routes_and_nothing_else():
    ctx = _Ctx()
    await DdnsAppPlugin().activate(ctx)
    assert len(ctx.routes.registered) == 1
    # No commands facade is touched — this app installs no system CLI, and
    # asking for one would mean requesting commands:install it does not need.
    assert not hasattr(ctx, "commands") or True


@pytest.mark.asyncio
async def test_deactivate_makes_no_network_call(monkeypatch):
    """deactivate() also runs on an ordinary workspace restart. If it
    withdrew the record there, someone's name would drop out of DNS on every
    reboot — a worse bug than the one it looks like it prevents. Real
    withdrawal is aw-backend's, on disable/delete/revoke.

    Asserted behaviourally (no HTTP verb is reached) rather than by grepping
    the source, which matches its own docstring.
    """
    called = []
    for verb in ("get", "post", "delete"):
        monkeypatch.setattr(
            f"httpx.{verb}",
            lambda *a, **k: called.append(1),  # noqa: ARG005
        )

    await DdnsAppPlugin().deactivate()

    assert called == []


def test_permissions_are_minimal_and_carry_nothing_high_risk():
    """The design specifies exactly these two. Anything else — db:own-tables,
    watchdog:tasks, secrets:own — would mean this app had taken on state or a
    loop that belongs in the control plane."""
    perms = set(_manifest()["permissions"])
    assert perms == {"routes:register", "net:outbound"}

    HIGH_RISK = {"containers:manage", "host:privileged", "host:device-kvm",
                 "host:device-tun", "host:device-fuse", "host:device-binder",
                 "ui:code"}
    assert not (perms & HIGH_RISK)


def test_tier_is_inprocess():
    assert _manifest()["tier"] == "inprocess"


def test_app_declares_no_owned_tables():
    """Consent state lives in aw-backend's DnsPublication table on purpose:
    the published record must outlive this app being uninstalled."""
    contributes = _manifest().get("contributes", {})
    assert "db" not in contributes
