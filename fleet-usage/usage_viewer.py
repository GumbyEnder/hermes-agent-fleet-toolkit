#!/usr/bin/env python3
"""Fleet Token Usage viewer — serves the precomputed usage.json + a dashboard page.

GET /                -> viewer HTML (7d / 30d / all, per-profile + per-model tables)
GET /api/usage       -> raw usage.json
GET /health          -> status
"""
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get("USAGE_JSON", os.path.expanduser("~/.local/share/fleet-usage/usage.json"))
BIND = os.environ.get("USAGE_BIND", "127.0.0.1")
PORT = int(os.environ.get("USAGE_PORT", "8091"))

PAGE = """<!DOCTYPE html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Token Usage</title><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<style>
:root{--bg:#07101f;--panel:rgba(14,24,48,.94);--text:#eaf0ff;--muted:#9aabc9;--border:#21314f;--accent:#78c8ff;--good:#9cffbf;--warn:#ffd27a}
*{box-sizing:border-box}body{margin:0;font-family:Inter,system-ui,sans-serif;color:var(--text);background:radial-gradient(circle at top left,rgba(120,200,255,.18),transparent 26%),linear-gradient(180deg,#07101f,#0b1730);min-height:100vh}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 60px}
h1{margin:0 0 4px;font-size:1.6rem}.sub{color:var(--muted);font-size:.9rem;margin-bottom:16px}
.tabs{display:flex;gap:8px;margin-bottom:16px}.tabs button{border:1px solid var(--border);background:rgba(9,17,34,.75);color:var(--text);border-radius:999px;padding:8px 16px;font-weight:600;cursor:pointer}.tabs button.active{background:rgba(120,200,255,.14);border-color:rgba(120,200,255,.6);color:var(--accent)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:14px}
.card .num{font-size:1.5rem;font-weight:800}.card .lbl{color:var(--muted);font-size:.82rem;margin-top:2px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:.9rem}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:600;text-align:right}th:first-child,td:first-child{text-align:left}
tr:last-child td{border-bottom:0}.bar{height:5px;background:rgba(120,200,255,.25);border-radius:3px;margin-top:3px}.bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
h2{font-size:1.05rem;color:var(--good);margin:24px 0 8px}
.updated{color:var(--muted);font-size:.82rem;margin-top:18px}
</style></head><body><div class="wrap">
<h1>Fleet Token Usage</h1>
<div class="sub">All Hermes agents &middot; collected from each profile's state.db &middot; <a href="/api/usage">JSON</a></div>
<div class="tabs"><button data-w="7d" class="active">Last 7 days</button><button data-w="30d">Last 30 days</button><button data-w="all">All time</button></div>
<div class="cards" id="cards"></div>
<h2>By agent (profile)</h2><table id="tp"></table>
<h2>By model</h2><table id="tm"></table>
<div class="updated" id="upd"></div>
</div>
<script>
const fmt=n=>n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':''+Math.round(n);
const fmtc=n=>'$'+(n||0).toFixed(2);
let data=null,win='7d';
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function render(){
 const w=data.windows[win];if(!w)return;
 const t=w.total;
 document.getElementById('cards').innerHTML=
  [['Sessions',t.sessions],['Input tokens',fmt(t.input)],['Output tokens',fmt(t.output)],['Cache reads',fmt(t.cache_read)],['Reasoning',fmt(t.reasoning)],['Est. cost',fmtc(t.est_cost)]]
  .map(([l,v])=>`<div class="card"><div class="num">${v}</div><div class="lbl">${l}</div></div>`).join('');
 const mx=Math.max(...Object.values(w.by_profile).map(b=>b.input),1);
 const order=Object.entries(w.by_profile).sort((a,b)=>b[1].input-a[1].input);
 document.getElementById('tp').innerHTML='<tr><th>Profile</th><th>Sessions</th><th>Input</th><th>Output</th><th>Cache R</th><th>Last active</th></tr>'+
  order.map(([p,b])=>`<tr><td>${esc(p)}</td><td>${b.sessions}</td><td>${fmt(b.input)}<div class="bar"><i style="width:${100*b.input/mx}%"></i></div></td><td>${fmt(b.output)}</td><td>${fmt(b.cache_read)}</td><td>${b.last_active?new Date(b.last_active*1000).toLocaleString('en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'—'}</td></tr>`).join('');
 const mo=Object.entries(w.by_model).sort((a,b)=>b[1].input-a[1].input).slice(0,15);
 const mmx=Math.max(...mo.map(([,b])=>b.input),1);
 document.getElementById('tm').innerHTML='<tr><th>Model</th><th>Sessions</th><th>Input</th><th>Output</th><th>Est. cost</th></tr>'+
  mo.map(([m,b])=>`<tr><td>${esc(m)}</td><td>${b.sessions}</td><td>${fmt(b.input)}<div class="bar"><i style="width:${100*b.input/mmx}%"></i></div></td><td>${fmt(b.output)}</td><td>${fmtc(b.est_cost)}</td></tr>`).join('');
 document.getElementById('upd').textContent='Collected '+new Date(data.generated*1000).toLocaleString();
}
document.querySelectorAll('.tabs button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');win=b.dataset.w;render()}));
fetch('/api/usage').then(r=>r.json()).then(d=>{data=d;render()});setInterval(()=>fetch('/api/usage').then(r=>r.json()).then(d=>{data=d;render()}),60000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok", "port": PORT}).encode()
        elif self.path == "/api/usage":
            body = open(DATA, "rb").read() if os.path.exists(DATA) else b'{"windows":{}}'
        elif self.path == "/favicon.svg":
            self.send_response(200); self.send_header("Content-Type", "image/svg+xml"); self.end_headers()
            self.wfile.write(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#07101f"/><path d="M8 22V10l8 6 8-6v12" fill="none" stroke="#78c8ff" stroke-width="2"/></svg>')
            return
        else:
            body = PAGE.encode()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json" if self.path.startswith("/api") or self.path == "/health" else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
