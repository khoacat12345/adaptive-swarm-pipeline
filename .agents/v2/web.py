#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal v2 Usage-approval surface for the local dashboard.

The page is intentionally standalone (/v2). It does not edit a plan: the user
approves the exact plan + usage estimate that is already on disk. The plan hash
is kept for audit, but the gate is Usage approval under P0-06.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import approval_policy

from .errors import PlanChangedError, StateTransitionError
from .plan import plan_hash
from .runner import V2Runner
from .state import resolve_state_root

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Swarm v2.1 sign-off</title>
<style>
  :root{--bg:#07111b;--panel:#0b1926;--line:#173247;--text:#dbeafe;--muted:#7f9bb5;--ok:#51d88a;--warn:#f5b84b;--bad:#ff6b6b;--accent:#39a7ff}
  *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,Segoe UI,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center}
  h1{font-size:18px;margin:0}small{color:var(--muted)}main{max-width:1100px;margin:0 auto;padding:24px;display:grid;gap:16px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.grow{flex:1}.muted{color:var(--muted)}
  button{background:#12324a;color:var(--text);border:1px solid #24506e;border-radius:8px;padding:8px 12px;cursor:pointer}
  button.primary{background:#0b6d96;border-color:#19a7d8}button:disabled{opacity:.45;cursor:not-allowed}
  input{background:#06101a;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:260px}
  pre{background:#050b12;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;max-height:420px;white-space:pre-wrap}
  .pill{border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:11px;color:var(--muted)}
  .ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
  .runs{display:grid;gap:8px}.run{display:flex;gap:10px;align-items:center;padding:10px;border:1px solid var(--line);border-radius:8px;cursor:pointer}
  .run:hover{border-color:#2d6385}.run.active{border-color:var(--accent);background:#0d2233}
  code{font-family:Consolas,monospace;font-size:12px}
</style>
</head>
<body>
<header><h1>Swarm v2.1</h1><small>Usage approval - local only</small><span class="grow"></span><span class="pill">P0-06</span></header>
<main>
  <section class="card">
    <div class="row"><b>Runs</b><span class="grow"></span><button onclick="loadRuns()">Refresh</button></div>
    <div id="runs" class="runs" style="margin-top:12px"><span class="muted">Loading...</span></div>
  </section>
  <section class="card">
    <div class="row"><b>Selected run</b><span id="selected" class="muted">none</span><span class="grow"></span><span id="status" class="pill">-</span></div>
    <div id="hash" class="muted" style="margin-top:8px"></div>
    <div class="row" style="margin-top:12px">
      <input id="token" type="password" placeholder="run token from prepare output" autocomplete="off">
      <button class="primary" id="approve" onclick="approve()" disabled>Approve Usage</button>
      <button id="pause" onclick="control('pause')" disabled>Pause</button>
      <button id="resume" onclick="control('resume')" disabled>Resume</button>
      <button id="stop" onclick="control('stop')" disabled>Stop</button>
      <button id="accept" onclick="control('accept')" disabled>Accept handoff</button>
    </div>
    <div id="statusline" class="muted" style="margin-top:10px"></div>
    <div id="budget" class="muted" style="margin-top:6px"></div>
    <div id="msg" class="muted" style="margin-top:10px"></div>
    <pre id="plan" style="margin-top:12px">Select a run.</pre>
  </section>
</main>
<script>
let selected = null;
const $ = (id) => document.getElementById(id);
async function api(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}
async function loadRuns() {
  try {
    const data = await api('/api/v2/runs');
    const runs = data.runs || [];
    $('runs').innerHTML = runs.length ? '' : '<span class="muted">No v2 runs yet. Run prepare from the CLI first.</span>';
    for (const run of runs) {
      const el = document.createElement('div');
      el.className = 'run' + (selected === run.run_id ? ' active' : '');
      el.innerHTML = `<b>${escapeHtml(run.run_id)}</b><span class="grow"></span><span class="pill">${escapeHtml(run.status)}</span>`;
      el.onclick = () => selectRun(run.run_id);
      $('runs').appendChild(el);
    }
  } catch (err) { $('runs').innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`; }
}
async function selectRun(id) {
  selected = id;
  $('selected').textContent = id;
  $('msg').textContent = '';
  await loadRuns();
  try {
    const data = await api('/api/v2/runs/' + encodeURIComponent(id));
    $('status').textContent = data.status || '-';
    $('status').className = 'pill ' + (data.status === 'AWAITING_SIGNOFF' ? 'warn' : data.status === 'PROVISIONING' || data.status === 'PHASE_EXECUTING' ? 'ok' : '');
    $('hash').textContent = data.plan_hash ? 'plan_hash: ' + data.plan_hash : 'No plan submitted.';
    $('statusline').textContent = data.workspace?.worktree_path ? ('worktree: ' + data.workspace.worktree_path) : '';
    $('budget').textContent = data.budget ? `budget: ${data.budget.global?.used_requests || 0} used / ${data.budget.global?.reserved_requests || 0} reserved / phase ${data.budget.phases ? Object.keys(data.budget.phases).length : 0}` : 'budget: not started';
    $('plan').textContent = JSON.stringify(data.plan || {}, null, 2);
    $('approve').disabled = !data.can_approve_usage;
    $('pause').disabled = !data.can_control?.pause;
    $('resume').disabled = !data.can_control?.resume;
    $('stop').disabled = !data.can_control?.stop;
    $('accept').disabled = !data.can_control?.accept;
    if (data.usage?.status === 'approved') $('msg').innerHTML = '<span class="ok">Usage approved. Any plan change requires reapproval.</span>';
  } catch (err) { $('msg').innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`; }
}
async function control(action) {
  if (!selected) return;
  $('msg').textContent = action + '...';
  try {
    const data = await api('/api/v2/control', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id:selected, token:$('token').value, action})});
    $('msg').innerHTML = `<span class="ok">${escapeHtml(action)} → ${escapeHtml(data.status || '')}</span>`;
    await selectRun(selected);
  } catch (err) { $('msg').innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`; }
}
async function approve() {
  if (!selected) return;
  $('approve').disabled = true;
  $('msg').textContent = 'Approving Usage...';
  try {
    const data = await api('/api/v2/usage/approve', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({run_id:selected, token:$('token').value})});
    $('msg').innerHTML = '<span class="ok">Usage approved: ' + escapeHtml(data.usage?.estimate_hash || data.signoff?.plan_hash || '') + '</span>';
    await selectRun(selected);
  } catch (err) { $('msg').innerHTML = `<span class="bad">${escapeHtml(err.message)}</span>`; $('approve').disabled = false; }
}
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
loadRuns();
</script>
</body>
</html>
"""


def page() -> str:
    return PAGE


def _state_runs(root: str | Path) -> Path:
    return resolve_state_root(root) / "runs"


def list_runs(root: str | Path) -> dict[str, Any]:
    base = _state_runs(root)
    runs: list[dict[str, Any]] = []
    if base.exists():
        for run_dir in sorted(base.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not run_dir.is_dir():
                continue
            manifest = run_dir / "run_manifest.json"
            if not manifest.exists():
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            runs.append({
                "run_id": payload.get("run_id"),
                "goal": payload.get("goal"),
                "status": payload.get("status"),
                "created_at": payload.get("created_at"),
                "plan_hash": payload.get("plan_hash"),
            })
    return {"runs": runs}


def _store(root: str | Path, run_id: str):
    runner = V2Runner(root)
    return runner, runner.load(run_id)


def run_detail(root: str | Path, run_id: str) -> dict[str, Any]:
    runner, store = _store(root, run_id)
    manifest = store.load()
    plan_path = store.run_dir / "plan.json"
    plan: dict[str, Any] = {}
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            plan = {}
    signoff_path = store.run_dir / "signoff.json"
    current_hash = plan_hash(plan) if plan else None
    signed = False
    if signoff_path.exists():
        try:
            signed_record = json.loads(signoff_path.read_text(encoding="utf-8"))
            signed = bool(current_hash) and signed_record.get("plan_hash") == current_hash
        except (OSError, json.JSONDecodeError):
            signed = False
    usage_status: dict[str, Any] = {}
    if plan:
        try:
            decision = runner.usage_decision(run_id, plan)
            usage_status = approval_policy.approval_status(
                runner.usage_approval_path(run_id), decision,
            )
        except Exception as exc:  # noqa: BLE001
            usage_status = {"status": "invalid", "error": str(exc)[:300]}
    usage_approved = usage_status.get("status") == "approved"
    signed = signed or usage_approved
    budget = {}
    if store.budget_path.exists():
        try:
            budget = json.loads(store.budget_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            budget = {}
    status = manifest.get("status")
    can_control = {
        "pause": status in {"PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING"},
        "resume": status == "PAUSED",
        "stop": status in {"PROVISIONING", "PHASE_EXECUTING", "PHASE_VERIFYING", "PAUSED", "BLOCKED"},
        "accept": status == "HANDOFF_READY",
    }
    can_approve_usage = (
        status == "AWAITING_SIGNOFF"
        and bool(plan)
        and usage_status.get("status") == "awaiting"
    )
    return {
        "run_id": run_id,
        "goal": manifest.get("goal"),
        "status": status,
        "plan_hash": current_hash or manifest.get("plan_hash"),
        "budget_hash": manifest.get("budget_hash"),
        "workspace": manifest.get("workspace") or {},
        "plan": plan,
        "budget": budget,
        "phases": [
            {"id": phase.get("id"), "title": phase.get("title"),
             "status": "committed" if any(
                 item.get("phase_id") == phase.get("id")
                 for item in (manifest.get("checkpoints") or [])
             ) else "pending"}
            for phase in (plan.get("phases") or [])
        ],
        "signed": signed,
        "usage": usage_status,
        "can_sign": can_approve_usage,
        "can_approve_usage": can_approve_usage,
        "can_control": can_control,
    }


def plan_detail(root: str | Path, run_id: str) -> dict[str, Any]:
    runner, store = _store(root, run_id)
    plan_path = store.run_dir / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    signoff_path = store.run_dir / "signoff.json"
    signoff = json.loads(signoff_path.read_text(encoding="utf-8")) if signoff_path.exists() else {}
    usage_path = store.run_dir / "usage_approval.json"
    usage = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.exists() else {}
    return {
        "plan": plan,
        "plan_hash": plan_hash(plan) if plan else None,
        "signoff": signoff,
        "usage": usage,
    }


def approve(root: str | Path, run_id: str, token: str) -> dict[str, Any]:
    runner = V2Runner(root)
    if not runner.verify_signoff_token(run_id, token):
        raise PermissionError("invalid sign-off token")
    store = runner.load(run_id)
    store.assert_state("AWAITING_SIGNOFF")
    plan_path = store.run_dir / "plan.json"
    if not plan_path.exists():
        raise StateTransitionError("run has no plan")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    # The hash is recomputed from disk; the client cannot approve a different body.
    expected = plan_hash(plan)
    manifest = store.load()
    if manifest.get("plan_hash") and manifest.get("plan_hash") != expected:
        raise PlanChangedError("plan file changed after submission")
    result = runner.signoff(run_id, approved_by="dashboard-user")
    return {"ok": True, **result}


def control(root: str | Path, run_id: str, token: str, action: str) -> dict[str, Any]:
    runner = V2Runner(root)
    if not runner.verify_signoff_token(run_id, token):
        raise PermissionError("invalid run token")
    action = str(action or "").lower()
    if action == "pause":
        manifest = runner.pause(run_id)
    elif action == "resume":
        manifest = runner.resume(run_id)
    elif action == "stop":
        manifest = runner.stop(run_id)
    elif action == "accept":
        manifest = runner.accept_handoff(run_id)
    else:
        raise StateTransitionError(f"unknown control action: {action}")
    return {"ok": True, "action": action, "status": manifest.get("status")}
