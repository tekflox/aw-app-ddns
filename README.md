# aw-app-ddns — Dynamic DNS

A stable public name for a BYOD workspace that follows its home internet
connection: `<slug>.workspace.ddns.tekflox.com`, an A record with a 60s TTL,
kept current as the ISP moves the address.

Built for the SIP/PSTN case — reaching a home Linksys SPA3102 through the
`aw-app-call-agent` bridge from outside the house, without putting the caller
on the mesh and without an IP allowlist — but nothing here is SIP-specific.

## What this app is, and what it deliberately is not

**It is a consent toggle and a status panel. That is the whole app.**

It does **not** measure an IP address, and it does **not** write DNS. Both
live in aw-backend (`src/api/routes/dns_publication.py`). That split is the
load-bearing design decision, for two independent reasons:

1. **Security.** A BYOD workspace runs on hardware its owner controls. If
   this app posted `{"ip": ...}` and the control plane wrote it, then "which
   address resolves under a public name" would be an attacker-controlled
   field on a workspace someone else can reach.
2. **Correctness.** It would be the *wrong address*. An app measuring its own
   egress measures the **container's** egress, which diverges from the host's
   whenever an exit gate or VPN is set — the divergence `container_egress`
   exists to expose. Inbound SIP arrives at the **host's** WAN address, so
   publishing container egress produces a record that looks right and
   receives nothing.

aw-backend already has an honest measurement (`host_link._measure_public_ip`,
which is never cached — a host that cannot be asked returns `null`, not a
stale number), so publishing what *it* measured costs nothing extra and
removes the whole class.

## What the panel shows

Four things, plus the switch: **enabled/disabled**, the **assigned hostname**,
the **current external IP on record**, and the **reachability verdict**.

The reachability verdict has three states and they are kept apart on purpose:

| State | Meaning |
|---|---|
| `reachable` | Something accepted a connection from the internet. Conclusive. |
| `refused` | Routable, and a host actively refused. **Not CGNAT** — just no port forward. |
| `timeout` | No answer at all. **Either** a missing port forward **or** CGNAT — we cannot tell which from here. |

A timeout is never rendered as "CGNAT detected". It is not one, and saying so
would be a guess presented as a measurement.

## The refusals (all server-side)

The panel text is a courtesy; the refusal is aw-backend's, which is what makes
it true. Enable runs four checks in order:

1. **Hosted placement** — a workspace running on TekFlox infrastructure is
   refused as not-applicable. This reads `is_hosted_workspace()`, *not*
   `placement_driver` (which flips to `"remote-host"` for hosted workspaces
   the moment their outer host dials `/link`, making it useless at steady
   state) and *not* `RemoteHost.placement` (never set to `"hosted"` in any
   production path).
2. **Measurement failure** — surfaced verbatim, since "offline" /
   "agent too old" / "no internet" need different things done about them.
3. **Non-routable address** — a hard refusal, with CGNAT (RFC 6598,
   `100.64.0.0/10`) named explicitly. Publishing an unreachable address is
   worse than refusing: the record resolves, so everything downstream reports
   success and nothing ever answers.
4. **Reachability probe** — advisory, recorded, never blocking.

## Nothing resolves until you turn it on

`DnsPublication.enabled` defaults to False, and the record is withdrawn on
disable, on workspace delete, and on remote-host revoke. It lives in
aw-backend's table rather than this app's config precisely so it can outlive
the app: an uninstall that silently left a home address resolving would be the
worst failure this feature can have, and an app cannot withdraw a record after
it stops existing.

Note that `deactivate()` does **not** withdraw — that also runs on an ordinary
workspace restart, and dropping someone's name out of DNS on every reboot
would be a worse bug than the one it looks like it prevents.

## Zone and credential

Records go to a **delegated** hosted zone, `ddns.tekflox.com`, under an IAM
credential scoped to that zone alone (`aw-ddns-publisher`). It is **not** the
Caddy/ACME credential and must never be.

The parent `tekflox.com` zone holds the apex, `www`, `api`, `mcp`, the
`aw.tekflox.com` tree and every `_acme-challenge` TXT the Caddy certs depend
on. A DDNS writer with change rights there would be one bad `ChangeBatch` away
from a TLS outage; in a zone containing nothing but DDNS A records, the worst
case is "DDNS breaks".

aw-backend reads `AW_DDNS_ZONE_ID`, `AW_DDNS_AWS_ACCESS_KEY_ID` and
`AW_DDNS_AWS_SECRET_ACCESS_KEY` from its environment. With none set,
publishing is disabled and the panel says so rather than accepting a consent
it cannot honour.

## Routes

All relative to `/api/apps/ddns`, identical in integrated and standalone mode:

| Route | Does |
|---|---|
| `GET /status` | Panel state. Never measures — safe to poll. |
| `POST /enable` | `{kind, probe_port?}` — consent. aw-backend measures, refuses or publishes. |
| `POST /disable` | Withdraw the record. |
| `GET /panel/ddns` | The panel itself (iframe body). |

Auth is the workspace's own `AW_WORKSPACE_HOST_TOKEN`, which aw-backend's
`require_workspace_actor` accepts only for its own slug — so a workspace can
only ever act on itself. No new credential type.

## Develop

```bash
pytest tests/ -q --cov=ddns_app            # 100% gate, see pyproject.toml
python tests/validate_manifest.py aw-app.json
python -m ddns_app                          # standalone on 127.0.0.1:9410
```

Standalone still talks to a real aw-backend — set `AW_BACKEND_URL`,
`AW_WORKSPACE` and `AW_WORKSPACE_HOST_TOKEN`, or every route answers 503
"no cloud link", which is the honest result rather than a mock.

## Design

`docs/knowledge_base/docs/architecture/aw-workspace-dynamic-dns.md` in the
agentic-workspace repo — including the rejected alternatives (running our own
authoritative DNS server, writing into `tekflox.com`, a host-side updater,
letting the app post its own IP) and what this makes harder later (IPv6).
