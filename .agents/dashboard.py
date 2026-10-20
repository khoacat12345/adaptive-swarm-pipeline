#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web Dashboard (Task 4.3) - quan sat pipeline bang trinh duyet, 0 framework.

    python .agents/dashboard.py --port 8080
    python .agents/dashboard.py --root C:\\du-an --port 9000

Chi dung thu vien chuan (`http.server`). Doc truc tiep:

    <root>/.agents/ledger.jsonl   (su kien)
    <root>/.agents/backups/       (ban truoc khi ghi -> diff)
    <root>/.agents/draft/         (ban nhap cua Coder -> diff)

API:
    GET /                     SPA (dark mode, 3 cot)
    GET /api/runs             danh sach run + tong hop
    GET /api/runs/<run_id>    timeline + event chuan hoa
    GET /api/diff?run_id=...  diff ban goc vs ban da ghi / ban nhap
    GET /api/health           trang thai server + so run

Server chi bind 127.0.0.1 (khong mo ra mang).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import difflib
import replay
import schema

MAX_CONTENT_BYTES = 200_000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


# --------------------------------------------------------------------------
# Du lieu
# --------------------------------------------------------------------------
def ledger_for(root: Path) -> Path:
    return (root if root.name == ".agents" else root / ".agents") / "ledger.jsonl"


def _start_data(events: list[dict]) -> dict[str, dict]:
    starts: dict[str, dict] = {}
    for event in events:
        if event.get("event_type") == "recipe.start":
            starts[str(event.get("run_id"))] = dict(event.get("data") or {})
    return starts


def load_runs(root: Path, ledger: Path, *, task_only: bool = False) -> list[dict]:
    events = schema.read_events(ledger)
    runs = replay.list_runs(events)
    starts = _start_data(events)
    for entry in runs:
        start = starts.get(entry["run_id"], {})
        entry["verify_command"] = start.get("verify_command")
        entry["dry_run"] = bool(start.get("dry_run"))
    if task_only:
        runs = [entry for entry in runs if entry.get("task_id")]
    return runs


def load_run(root: Path, ledger: Path, run_id: str) -> dict:
    events = schema.read_events(ledger)
    timeline = replay.build_timeline(events, run_id, limit=500)
    normalized = [schema.normalize(event) for event in events
                  if str(event.get("run_id")) == str(run_id)]
    run = next((item for item in replay.list_runs(events)
                if item["run_id"] == run_id), None)
    start = next((schema.normalize(event) for event in events
                  if str(event.get("run_id")) == run_id
                  and event.get("event_type") == "recipe.start"), {})
    verify = None
    for event in events:
        if str(event.get("run_id")) != str(run_id):
            continue
        if event.get("event_type") == "recipe.start":
            verify = (event.get("data") or {}).get("verify_command")
        if verify is None and event.get("event_type") == "safe_writer.verify":
            verify = (event.get("data") or {}).get("command")
    if run is None:
        run = {"run_id": run_id, "events": len(normalized), "status": None}
    run["verify_command"] = verify
    run["level"] = (start.get("details") or {}).get("level")
    return {"run": run, "timeline": timeline,
            "events": [{key: value for key, value in item.items() if key != "extra"}
                       for item in normalized]}


def _read_text(path: Path) -> str:
    try:
        data = path.read_bytes()[:MAX_CONTENT_BYTES]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _diff_multi(root: Path, selected: list[dict], *, run_id: str, files: list[str],
                task_id: str) -> dict:
    """Diff cho run Level 3: tung file (backup vs hien tai) + diff gop."""
    entries: list[dict] = []
    combined: list[str] = []
    changed = 0
    for relative in files:
        target = root / relative
        backup = None
        for event in selected:
            if event.get("event_type") != "safe_writer.backup":
                continue
            data = event.get("data") or {}
            try:
                same = Path(str(data.get("target") or "")).resolve() == target.resolve()
            except OSError:
                same = False
            if same and data.get("backup_path"):
                backup = Path(str(data["backup_path"]))
        if backup is not None and not _inside(backup, root / ".agents"):
            backup = None
        draft = root / ".agents" / "draft" / task_id / relative
        old_source = backup if (backup and backup.exists()) else (target if target.exists() else None)
        new_source = target if target.exists() else (draft if draft.exists() else None)
        if old_source is None or new_source is None:
            entries.append({"target": relative, "error": "khong tim thay file de diff"})
            continue
        old_text = _read_text(old_source)
        new_text = _read_text(new_source)
        diff = "\n".join(difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile=f"{relative} (goc)", tofile=f"{relative} (hien tai)",
            lineterm="", n=3))
        count = sum(1 for line in diff.splitlines()
                    if line.startswith(("+", "-"))
                    and not line.startswith(("+++", "---")))
        changed += count
        entries.append({
            "target": relative,
            "old_label": "backup" if old_source is backup else "hien tai",
            "new_label": "hien tai" if new_source is target else "ban nhap",
            "old": old_text[:MAX_CONTENT_BYTES],
            "new": new_text[:MAX_CONTENT_BYTES],
            "diff": diff[:MAX_CONTENT_BYTES * 2],
            "changed_lines": count,
        })
        combined.append(diff)
    return {
        "run_id": run_id,
        "target": ", ".join(files),
        "files": entries,
        "old_label": "backup",
        "new_label": "hien tai",
        "old": entries[0].get("old", "") if entries else "",
        "new": entries[0].get("new", "") if entries else "",
        "diff": "\n".join(combined)[:MAX_CONTENT_BYTES * 2],
        "changed_lines": changed,
    }


def diff_for_run(root: Path, ledger: Path, run_id: str) -> dict:
    """Ban goc (backup truoc khi ghi) vs hien tai; khong co backup -> ban nhap."""
    events = schema.read_events(ledger)
    selected = [event for event in events if str(event.get("run_id")) == str(run_id)]
    if not selected:
        return {"error": f"khong thay run {run_id}"}

    start = next((event.get("data") or {} for event in selected
                  if event.get("event_type") == "recipe.start"), {})
    target_rel = str(start.get("target") or "")
    task_id = str(start.get("task_id") or "task")

    # Level 3: mot run co nhieu file -> tra ve diff cua TUNG file + diff gop.
    file_list = [str(item) for item in (start.get("files") or []) if str(item)]
    if len(file_list) > 1:
        return _diff_multi(root, selected, run_id=run_id, files=file_list, task_id=task_id)

    target = Path(target_rel) if target_rel else None
    if target is not None and not target.is_absolute():
        target = root / target_rel

    backup_path = None
    for event in selected:
        if event.get("event_type") == "safe_writer.backup":
            backup_path = (event.get("data") or {}).get("backup_path")
    backup = Path(backup_path) if backup_path else None
    if backup is not None and not _inside(backup, root / ".agents"):
        backup = None

    draft = None
    if target_rel:
        candidate = root / ".agents" / "draft" / task_id / target_rel
        if candidate.exists():
            draft = candidate

    old_source = backup if backup and backup.exists() else target
    new_source = target if (target and target.exists()) else draft
    if old_source is None or new_source is None:
        return {"error": "khong tim thay file de diff", "target": target_rel}

    old_text = _read_text(old_source)
    new_text = _read_text(new_source)
    diff = "\n".join(difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=str(old_source), tofile=str(new_source), lineterm="", n=3))
    return {
        "run_id": run_id,
        "target": target_rel,
        "old_label": "backup" if old_source is backup else "hien tai",
        "new_label": "hien tai" if new_source is target else "ban nhap",
        "old": old_text[:MAX_CONTENT_BYTES],
        "new": new_text[:MAX_CONTENT_BYTES],
        "diff": diff[: MAX_CONTENT_BYTES * 2],
        "changed_lines": sum(1 for line in diff.splitlines()
                             if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))),
    }


# --------------------------------------------------------------------------
# HTML (SPA)
# --------------------------------------------------------------------------
HTML = r"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adaptive Swarm - Live</title>
<style>
  :root{
    --bg:#0b0f14; --panel:#111823; --panel2:#0e141d; --line:#1e2a3a;
    --text:#d7e2ee; --dim:#7d8fa3; --accent:#38bdf8;
    --lead:#38bdf8; --coder:#a78bfa; --reviewer:#fbbf24; --systembot:#34d399;
    --security:#f87171; --qa:#94a3b8; --system:#64748b;
    --ok:#34d399; --fail:#f87171; --wait:#fbbf24;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 "Segoe UI",system-ui,sans-serif;height:100vh;overflow:hidden}
  header{height:56px;display:flex;align-items:center;gap:14px;padding:0 18px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,#0b0f14,#0f1722)}
  header h1{font-size:15px;margin:0;letter-spacing:.4px}
  header h1 span{color:var(--accent)}
  #statusMessage{color:var(--dim);font-size:12px;margin-left:auto;font-variant-numeric:tabular-nums}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);display:inline-block;margin-right:7px}
  main{display:grid;grid-template-columns:280px minmax(0,1fr) minmax(0,1.15fr);height:calc(100vh - 56px)}
  .col{overflow:auto;min-width:0}
  .col + .col{border-left:1px solid var(--line)}
  .colhead{position:sticky;top:0;z-index:2;background:var(--panel2);padding:11px 14px;border-bottom:1px solid var(--line);
           font-size:11px;letter-spacing:1.2px;color:var(--dim);text-transform:uppercase}
  /* sidebar */
  .run{padding:10px 14px;border-bottom:1px solid #16202c;cursor:pointer}
  .run:hover{background:#131c28}
  .run.active{background:#14212f;border-left:3px solid var(--accent)}
  .run .id{font-family:Consolas,monospace;font-size:11px;color:var(--dim);word-break:break-all}
  .run .row{display:flex;align-items:center;gap:8px;margin-top:5px}
  .tag{font-size:10px;padding:1px 7px;border-radius:999px;border:1px solid var(--line)}
  .tag.applied{color:var(--ok);border-color:#1d4b3d;background:#0f2a22}
  .tag.aborted,.tag.rejected{color:var(--fail);border-color:#4b1d1d;background:#2a1010}
  .tag.dry-run,.tag.other{color:var(--wait);border-color:#4b3d1d;background:#2a2310}
  .run .meta{font-size:11px;color:var(--dim)}
  /* chat */
  #chat{padding:14px 16px 40px;display:flex;flex-direction:column;gap:12px}
  .bubble{display:flex;gap:10px;align-items:flex-start;animation:in .18s ease-out}
  @keyframes in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  .avatar{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;font-size:11px;font-weight:700;
          color:#06121c;flex:0 0 auto;margin-top:2px}
  .body{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:9px 12px;max-width:92%}
  .who{display:flex;gap:8px;align-items:baseline;font-size:11px;color:var(--dim);margin-bottom:3px}
  .who b{color:var(--text);font-size:11px;letter-spacing:.3px}
  .msg{white-space:pre-wrap;word-break:break-word}
  .chip{font-size:10px;padding:1px 7px;border-radius:999px;margin-left:6px}
  .chip.pass,.chip.ok{color:var(--ok);background:#0f2a22}
  .chip.fail,.chip.error{color:var(--fail);background:#2a1010}
  .chip.blocked,.chip.timeout{color:var(--wait);background:#2a2310}
  .chip.info{color:var(--dim);background:#16202c}
  .model{font-family:Consolas,monospace;font-size:10px;color:var(--dim)}
  /* right */
  #inspector{padding:14px 16px 40px}
  .cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
  .card .k{font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
  .card .v{font-size:18px;margin-top:4px;font-variant-numeric:tabular-nums}
  .verify{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:14px}
  .verify code{font-family:Consolas,monospace;font-size:12px;color:#9fe3c0;word-break:break-all}
  .diffhead{display:flex;justify-content:space-between;align-items:center;margin:4px 0 8px}
  .diffhead .t{font-size:12px;color:var(--dim)}
  pre.diff{margin:0;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 0;
           overflow:auto;font:12px/1.5 Consolas,monospace;max-height:calc(100vh - 300px)}
  pre.diff div{padding:0 12px;white-space:pre}
  .add{background:#0f2a22;color:#9be7c4}
  .del{background:#2a1010;color:#ffb4b4}
  .hunk{color:#7dd3fc;background:#0e1c29}
  .ctx{color:#9aa9ba}
  .empty{color:var(--dim);padding:30px 16px;text-align:center;font-size:12px}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#1c2937;border-radius:8px}
  ::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>
<header>
  <h1><span>&#9679;</span> Adaptive Swarm <span>Pipeline</span></h1>
  <div id="statusMessage"><span class="dot"></span>dang tai...</div>
</header>
<main>
  <section class="col" id="sidebar">
    <div class="colhead">Run history
      <label style="float:right;text-transform:none;letter-spacing:0;color:var(--dim);cursor:pointer">
        <input type="checkbox" id="showAll"> tat ca</label>
    </div>
    <div id="runs"><div class="empty">Chua co run nao</div></div>
  </section>

  <section class="col">
    <div class="colhead">Phong hop da Agent</div>
    <div id="chat"><div class="empty">Chon mot run o cot trai</div></div>
  </section>

  <section class="col">
    <div class="colhead">Diff &amp; Inspector</div>
    <div id="inspector"><div class="empty">Chua co du lieu</div></div>
  </section>
</main>
<script>
const NODE = {
  lead:{c:'#38bdf8',n:'Lead',a:'LD'}, coder:{c:'#a78bfa',n:'Coder',a:'CD'},
  reviewer:{c:'#fbbf24',n:'Reviewer',a:'RV'}, systembot:{c:'#34d399',n:'SystemBot',a:'SB'},
  security:{c:'#f87171',n:'Security',a:'SC'}, qa:{c:'#94a3b8',n:'QA',a:'QA'},
  system:{c:'#64748b',n:'System',a:'SY'}
};
let state = {runs:[], selected:null, data:null, timer:null, showAll:false,
             sig:{runs:'', detail:''}};

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '');
const sigOf = (value) => { try { return JSON.stringify(value); } catch (_) { return String(value); } };
const fmt = (ms) => ms >= 60000 ? (ms/60000).toFixed(1)+'m' : ms >= 1000 ? (ms/1000).toFixed(1)+'s' : (ms||0)+'ms';
const num = (n) => (n||0).toLocaleString('en-US');

function statusClass(s){
  if (['applied','pass','ok'].includes(s)) return 'pass';
  if (['aborted','rejected','fail','error'].includes(s)) return 'fail';
  if (['blocked','timeout','dry-run'].includes(s)) return 'blocked';
  return 'info';
}

async function getJSON(url){
  const r = await fetch(url,{cache:'no-store'});
  if(!r.ok) throw new Error(url+' -> '+r.status);
  return await r.json();
}

function renderRuns(force=false){
  // Poll 3s: neu danh sach + selected khong doi thi khong dung lai DOM.
  const sig = sigOf({runs: state.runs, selected: state.selected});
  if(!force && sig === state.sig.runs) return;
  state.sig.runs = sig;
  const box = el('runs');
  if(!state.runs.length){ box.innerHTML = '<div class="empty">Chua co task nao<br><span style="font-size:11px">bat "tat ca" de xem run scout/acceptance</span></div>'; return; }
  box.innerHTML = state.runs.map(r=>{
    const st = r.status || 'other';
    const cls = ['applied'].includes(st)?'applied':['aborted','rejected'].includes(st)?'aborted':['dry-run'].includes(st)?'dry-run':'other';
    return `<div class="run ${r.run_id===state.selected?'active':''}" data-run="${esc(r.run_id)}">
      <div class="id">${esc(r.run_id)}</div>
      <div class="row"><span class="tag ${cls}">${esc(st)}</span>
        <span class="meta">${r.events} ev</span>
        <span class="meta">${fmt(r.duration_ms)}</span></div>
      <div class="meta">${esc(r.task_id||'-')} &middot; L${esc(r.level??'-')} &middot; ${num(r.requests)} req</div>
    </div>`;
  }).join('');
  box.querySelectorAll('.run').forEach(node=>{
    node.onclick = () => selectRun(node.dataset.run);
  });
}

function renderChat(){
  const box = el('chat');
  const tl = (state.data && state.data.timeline) || [];
  if(!tl.length){ box.innerHTML='<div class="empty">Run nay chua co event</div>'; return; }
  box.innerHTML = tl.map(item=>{
    const meta = NODE[item.node] || NODE.system;
    const chat = item.status ? `<span class="chip ${statusClass(item.status)}">${esc(item.status)}</span>` : '';
    const model = item.model ? `<span class="model">${esc(item.model)}</span>` : '';
    const dur = item.duration_ms ? `<span class="model">${item.duration_ms}ms</span>` : '';
    return `<div class="bubble">
      <div class="avatar" style="background:${meta.c}">${meta.a}</div>
      <div class="body">
        <div class="who"><b style="color:${meta.c}">${meta.n}</b>${chat}${model}${dur}
          <span class="model">${item.at_s.toFixed(2)}s</span></div>
        <div class="msg">${esc(item.message)}</div>
      </div>
    </div>`;
  }).join('');
  box.lastElementChild?.scrollIntoView({block:'nearest'});
}

function renderInspector(diff){
  const run = (state.data && state.data.run) || {};
  const events = (state.data && state.data.events) || [];
  const done = events.filter(e=>e.event_type==='recipe.done').pop() || {usage:{}, details:{}};
  const usage = done.usage || {in_tokens:0,out_tokens:0};
  const totalMs = run.duration_ms || 0;
  const requests = run.requests || 0;
  const lines = diff && diff.diff ? diff.diff.split('\n') : [];
  const rendered = lines.map(line=>{
    let cls='ctx';
    if(line.startsWith('+++')||line.startsWith('---')) cls='ctx';
    else if(line.startsWith('@@')) cls='hunk';
    else if(line.startsWith('+')) cls='add';
    else if(line.startsWith('-')) cls='del';
    return `<div class="${cls}">${esc(line)||'&nbsp;'}</div>`;
  }).join('');
  el('inspector').innerHTML = `
    <div class="cards">
      <div class="card"><div class="k">Requests</div><div class="v">${num(requests)}</div></div>
      <div class="card"><div class="k">Token in / out</div><div class="v">${num(usage.in_tokens)} / ${num(usage.out_tokens)}</div></div>
      <div class="card"><div class="k">Thoi gian</div><div class="v">${fmt(totalMs)}</div></div>
    </div>
    <div class="verify"><div class="k" style="font-size:10px;color:var(--dim);letter-spacing:1px">LENH VERIFY MAY THAT</div>
      <code>${esc(run.verify_command || '(khong ghi nhan)')}</code></div>
    <div class="diffhead"><div class="t">${esc(diff?.target || '')} ${diff?`(${diff.changed_lines} dong thay doi)`:''}</div>
      <div class="t">${esc(diff?.old_label||'')} &rarr; ${esc(diff?.new_label||'')}</div></div>
    <pre class="diff">${rendered || '<div class="ctx">Khong co thay doi / chua co file</div>'}</pre>`;
}

async function selectRun(runId, force=false){
  if(state.selected !== runId){ state.selected = runId; force = true; }
  renderRuns();
  if(force) el('statusMessage').innerHTML = '<span class="dot"></span>dang tai run...';
  try{
    const [data, diff] = await Promise.all([
      getJSON('/api/runs/'+encodeURIComponent(runId)),
      getJSON('/api/diff?run_id='+encodeURIComponent(runId)).catch(()=>({}))
    ]);
    // Poll: data + diff y nguyen -> bo qua toan bo phan ve lai (chat/inspector/status).
    const sig = sigOf({data, diff});
    if(!force && sig === state.sig.detail) return;
    state.sig.detail = sig;
    state.data = data;
    renderChat(); renderInspector(diff);
    const run = data.run || {};
    el('statusMessage').innerHTML = `<span class="dot"></span>${esc(run.status||'?')} &middot; ${run.events||0} event &middot; cap nhat ${new Date().toLocaleTimeString()}`;
  }catch(err){
    el('statusMessage').textContent = 'loi: '+err.message;
  }
}

async function refresh(force=false){
  try{
    const data = await getJSON('/api/runs?filter=' + (state.showAll ? 'all' : 'recipe'));
    state.runs = data.runs || [];
    renderRuns(force);          // chi ve lai khi danh sach hoac selected doi
    if(!state.selected && state.runs.length) await selectRun(state.runs[0].run_id, true);
    else if(state.selected) await selectRun(state.selected);
  }catch(err){
    const message = 'mat ket noi: '+err.message;
    if(el('statusMessage').textContent !== message) el('statusMessage').textContent = message;
  }
}

el('showAll').addEventListener('change', (event) => {
  state.showAll = event.target.checked;
  state.selected = null;
  refresh();
});

refresh();
state.timer = setInterval(refresh, 3000);
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
def make_handler(root: Path, ledger: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SwarmDashboard/1.0"

        def log_message(self, fmt, *args):        # im lang: khong spam stderr
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self):                       # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            try:
                if route == "/":
                    self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
                elif route == "/api/runs":
                    query = parse_qs(parsed.query)
                    mode = (query.get("filter") or ["recipe"])[0]
                    self._json({"runs": load_runs(root, ledger, task_only=mode != "all")})
                elif route.startswith("/api/runs/"):
                    run_id = route.split("/api/runs/", 1)[1]
                    runs = {item["run_id"] for item in load_runs(root, ledger)}
                    if run_id not in runs:
                        self._json({"error": f"khong thay run {run_id}"}, 404)
                    else:
                        self._json(load_run(root, ledger, run_id))
                elif route == "/api/diff":
                    query = parse_qs(parsed.query)
                    run_id = (query.get("run_id") or [""])[0]
                    if not run_id:
                        self._json({"error": "thieu run_id"}, 400)
                    else:
                        self._json(diff_for_run(root, ledger, run_id))
                elif route == "/api/health":
                    self._json({"ok": True, "ledger": str(ledger),
                                "runs": len(load_runs(root, ledger)),
                                "schema": schema.SCHEMA_SEMVER})
                else:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            except Exception as exc:            # noqa: BLE001
                self._json({"error": str(exc)[:300]}, 500)

    return Handler


def make_server(port: int = DEFAULT_PORT, *, root=".", ledger=None,
                host: str = DEFAULT_HOST) -> ThreadingHTTPServer:
    root_path = Path(root).resolve()
    ledger_path = Path(ledger).resolve() if ledger else ledger_for(root_path)
    server = ThreadingHTTPServer((host, int(port)), make_handler(root_path, ledger_path))
    server.daemon_threads = True
    return server


def serve(port: int = DEFAULT_PORT, *, root=".", ledger=None, host: str = DEFAULT_HOST,
          open_browser: bool = False) -> int:
    server = make_server(port, root=root, ledger=ledger, host=host)
    actual = server.server_address[1]
    print(f"dashboard: http://{host}:{actual}/  (ledger={Path(ledger).resolve() if ledger else ledger_for(Path(root).resolve())})")
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(f"http://{host}:{actual}/")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("dashboard: dung")
    finally:
        server.server_close()
    return 0


def build_parser():
    import argparse
    parser = argparse.ArgumentParser(description="Web dashboard doc ledger (0 framework).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--root", default=".")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--open", action="store_true", help="tu mo trinh duyet")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return serve(args.port, root=args.root, ledger=args.ledger, host=args.host,
                 open_browser=args.open)


if __name__ == "__main__":
    raise SystemExit(main())
