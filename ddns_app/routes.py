"""ddns_app's mode-agnostic FastAPI sub-app.

Mounted at ``/api/apps/ddns`` in both modes — by ``ctx.routes.register`` in
integrated mode (behind the runtime's ``IdentityGuard``; apps never implement
their own auth there) and by ``__main__.py`` at the same prefix standalone.
Every path below is RELATIVE, so one path shape works in both.

**This app measures nothing and writes no DNS.** Every route here is a thin
pass-through to aw-backend's ``/api/workspaces/{slug}/dns-publications``. That
is the design, not an accident of layering: a BYOD workspace runs on hardware
its owner controls, so an address posted from here would be an
attacker-controlled field deciding what resolves under a public name — and it
would be the WRONG address anyway, since a container measures its own egress
rather than the host's WAN, and inbound SIP arrives at the latter.
"""

from __future__ import annotations

import html
import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from .backend_client import BackendRequestFailed, BackendUnavailable, DnsBackend

log = logging.getLogger("aw_apps.ddns")


def _error_response(exc: Exception) -> JSONResponse:
    """Pass aw-backend's own words through.

    Those sentences are the product: "your ISP gives you a carrier-grade NAT
    address" is the entire answer to "why can't I turn this on", and replacing
    it with a generic failure would leave the user with nothing to act on.
    """
    if isinstance(exc, BackendUnavailable):
        return JSONResponse({"error": str(exc), "reason": "no_cloud_link"}, status_code=503)
    if isinstance(exc, BackendRequestFailed):
        return JSONResponse(
            {"error": exc.detail, "reason": exc.reason}, status_code=exc.status_code
        )
    log.exception("ddns: unexpected error talking to aw-backend")
    return JSONResponse({"error": str(exc), "reason": "unexpected"}, status_code=502)


def build_routes() -> FastAPI:
    app = FastAPI(title="ddns")

    @app.get("/status")
    async def status():
        """Enabled/disabled, the assigned hostname, the current external IP on
        record, and the reachability verdict. A plain read — it never triggers
        a measurement on the host, so the panel can poll it."""
        try:
            return DnsBackend().status()
        except Exception as exc:
            return _error_response(exc)

    @app.post("/enable")
    async def enable(body: dict | None = None):
        """Consent. aw-backend measures, runs its four refusals and publishes;
        this call just carries the intent and returns whatever it decided."""
        body = body or {}
        try:
            return DnsBackend().enable(
                kind=body.get("kind", "wan"), probe_port=body.get("probe_port")
            )
        except Exception as exc:
            return _error_response(exc)

    @app.post("/disable")
    async def disable(body: dict | None = None):
        body = body or {}
        try:
            return DnsBackend().disable(kind=body.get("kind", "wan"))
        except Exception as exc:
            return _error_response(exc)

    @app.get("/panel/ddns", response_class=HTMLResponse)
    async def panel():
        return HTMLResponse(_PANEL_HTML)

    return app


# The declarative window vocabulary has no table/toggle widget, so the panel
# is an iframe at this route — the same shape aw-app-tunnel uses. It supplies
# its OWN body padding on purpose: the host renders it cross-origin, so no
# stylesheet of the host's can reach inside, and padding on the <iframe>
# element would only clip the right-hand side.
_PANEL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body { padding: 12px; margin: 0; font: 13px/1.5 -apple-system, BlinkMacSystemFont,
         "Segoe UI", Roboto, sans-serif; color: #e6e6e6; background: transparent; }
  .row { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0;
         border-bottom: 1px solid rgba(255,255,255,.08); }
  .row:last-of-type { border-bottom: 0; }
  .k { opacity: .65; }
  .v { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; text-align: right;
       word-break: break-all; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; }
  .on   { background: rgba(52,199,89,.18);  color: #34c759; }
  .off  { background: rgba(255,255,255,.10); color: #aaa; }
  .warn { background: rgba(255,159,10,.18); color: #ff9f0a; }
  .bad  { background: rgba(255,69,58,.18);  color: #ff453a; }
  button { font: inherit; padding: 7px 14px; border-radius: 7px; border: 0;
           cursor: pointer; background: #2f6fed; color: #fff; }
  button.danger { background: rgba(255,69,58,.9); }
  button[disabled] { opacity: .5; cursor: default; }
  .msg { margin-top: 10px; padding: 9px 11px; border-radius: 7px; font-size: 12.5px;
         white-space: pre-wrap; }
  .msg.err  { background: rgba(255,69,58,.12);  border: 1px solid rgba(255,69,58,.3); }
  .msg.info { background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12); }
  .actions { margin-top: 14px; display: flex; gap: 8px; align-items: center; }
</style></head><body>
<div id="app">loading…</div>
<script>
const API = '/api/apps/ddns';
const el = document.getElementById('app');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function row(k, v) { return `<div class="row"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`; }

// Three states, rendered as three different things. A timeout is NOT a CGNAT
// detection and must never be drawn as one.
function reach(p) {
  if (!p.reachability) return row('Reachable from outside', '<span class="pill off">not checked</span>');
  const m = {
    reachable: ['on',   'reachable'],
    refused:   ['warn', 'no port forward'],
    timeout:   ['warn', 'could not verify'],
  }[p.reachability] || ['off', p.reachability];
  return row('Reachable from outside', `<span class="pill ${m[0]}">${esc(m[1])}</span>`)
       + (p.reachability_detail ? `<div class="msg info">${esc(p.reachability_detail)}</div>` : '');
}

async function load() {
  let d;
  try {
    const r = await fetch(`${API}/status`);
    d = await r.json();
    if (!r.ok) throw new Error(d.error || 'could not load status');
  } catch (e) {
    el.innerHTML = `<div class="msg err">${esc(e.message)}</div>`;
    return;
  }

  if (!d.eligible) {
    el.innerHTML = `<div class="msg info">${esc(d.ineligible_reason)}</div>`;
    return;
  }

  const wan = (d.publications || []).find(p => p.kind === 'wan');
  const on = wan && wan.enabled;
  let h = row('Status', on ? '<span class="pill on">published</span>'
                           : '<span class="pill off">not published</span>');
  h += row('Name', esc(d.wan_name));
  h += row('External IP on record', on && wan.published_value ? esc(wan.published_value) : '—');
  if (on) {
    h += row('Updated', wan.published_at ? new Date(wan.published_at * 1000).toLocaleString() : '—');
    h += row('TTL', `${esc(wan.ttl)}s`);
    h += reach(wan);
  }
  if (wan && wan.last_error)   h += `<div class="msg err">${esc(wan.last_error)}</div>`;
  if (wan && wan.config_error) h += `<div class="msg err">${esc(wan.config_error)}</div>`;

  h += `<div class="actions">
    <button id="btn" class="${on ? 'danger' : ''}">${on ? 'Turn off' : 'Publish my address'}</button>
    <span class="k">${on ? 'Removes the name from public DNS.'
                         : 'Nothing resolves until you turn this on.'}</span>
  </div>`;
  el.innerHTML = h;

  document.getElementById('btn').onclick = async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = on ? 'Turning off…' : 'Publishing…';
    try {
      const r = await fetch(`${API}/${on ? 'disable' : 'enable'}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(on ? {kind: 'wan'} : {kind: 'wan', probe_port: 5060}),
      });
      const b = await r.json();
      if (!r.ok) {
        await load();
        el.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(b.error || 'failed')}</div>`);
        return;
      }
    } catch (e) {
      el.insertAdjacentHTML('beforeend', `<div class="msg err">${esc(e.message)}</div>`);
      return;
    }
    load();
  };
}
load();
</script></body></html>
"""
