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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import difflib
import replay
import schema

MAX_CONTENT_BYTES = 200_000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
_GENERATE_LOCK = threading.Lock()


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
HTML = (Path(__file__).resolve().with_name("dashboard_ui.html")).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------
def make_handler(root: Path, ledger: Path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SwarmDashboard/1.0"

        def log_message(self, fmt, *args):        # im lang: khong spam stderr
            return

        def _send(self, status: int, body: bytes, content_type: str,
                  extra_headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(str(key), str(value))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            """Chan DNS rebinding: chi nhan Host localhost/127.0.0.1."""
            host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
            return host in ("127.0.0.1", "localhost", "::1", "[::1]")

        def do_GET(self):                       # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            if not self._host_ok():
                self._json({"error": "forbidden host"}, 403)
                return
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
                    notify_config: dict = {}
                    try:
                        notify_config = json.loads(
                            (Path(root) / ".agents" / "notify.json").read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        pass
                    telegram = bool((notify_config.get("telegram") or {}).get("bot_token"))
                    notify_on = bool(notify_config.get("enabled")
                                     or notify_config.get("url") or telegram)
                    self._json({"ok": True, "ledger": str(ledger),
                                "runs": len(load_runs(root, ledger)),
                                "schema": schema.SCHEMA_SEMVER,
                                "notify": notify_on, "telegram": telegram})
                elif route == "/api/plan":
                    import job as job_module
                    self._json(job_module.read_plan_state(root))
                elif route == "/api/plan/md":
                    import job as job_module
                    import plan_md
                    plan_state = job_module.read_plan_state(root)
                    plan = plan_state.get("plan")
                    if not plan:
                        self._json({"error": "no plan submitted"}, 404)
                        return
                    path = plan_md.write_markdown(
                        root, plan, plan_state.get("decision"),
                        job_module.read_state(root))
                    body = path.read_bytes()
                    query = parse_qs(parsed.query)
                    download = (query.get("download") or ["0"])[0].lower() in (
                        "1", "true", "yes")
                    disposition = "attachment" if download else "inline"
                    self._send(200, body, "text/markdown; charset=utf-8", {
                        "Content-Disposition": f'{disposition}; filename="{path.name}"'})
                elif route == "/api/quota":
                    import quota as quota_module
                    tracker = quota_module.QuotaTracker(root=root).refresh()
                    models = list(quota_module.CAPS.keys()) + [
                        "opencode/deepseek-v4.1-flash-free",
                        "opencode/glm-5.3-flash-free",
                    ]
                    entries = []
                    for model in models:
                        entry = tracker.remaining(model)
                        entries.append({
                            "model": model,
                            "ok": entry["ok"],
                            "unlimited": entry["unlimited"],
                            "worst_pct": entry["worst_pct"],
                            "windows": {name: value["pct"]
                                        for name, value in (entry.get("windows") or {}).items()},
                        })
                    payload = tracker.snapshot()
                    payload["models"] = entries
                    payload["usage"] = tracker.usage
                    self._json(payload)
                elif route == "/api/workspaces":
                    import quota as quota_module
                    try:
                        self._json({"workspaces": quota_module.workspaces()})
                    except Exception as exc:            # noqa: BLE001
                        self._json({"workspaces": [], "error": str(exc)[:300]})
                elif route == "/api/job":
                    import job as job_module
                    plan_state = job_module.read_plan_state(root)
                    self._json({
                        "state": job_module.read_state(root),
                        "plan": plan_state.get("plan"),
                        "decision": plan_state.get("decision"),
                        "control": job_module.read_control(root),
                    })
                elif route == "/api/team":
                    import team_view
                    self._json(team_view.build(root, ledger))
                elif route == "/api/draft":
                    import job as job_module
                    query = parse_qs(parsed.query)
                    relative = (query.get("path") or [""])[0]
                    job_id = (query.get("job_id") or [""])[0]
                    try:
                        if relative:
                            self._json({"path": relative,
                                        "content": job_module.read_draft(root, relative)})
                        else:
                            self._json({"job_id": job_id,
                                        "drafts": job_module.list_drafts(root, job_id)})
                    except (ValueError, FileNotFoundError) as exc:
                        self._json({"error": str(exc)}, 404)
                else:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
            except Exception as exc:            # noqa: BLE001
                self._json({"error": str(exc)[:300]}, 500)

        def do_POST(self):                      # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path.rstrip("/") or "/"
            if not self._host_ok():
                self._json({"error": "forbidden host"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 64 * 1024:
                    self._json({"error": "payload too large"}, 413)
                    return
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    self._json({"error": "content-type must be application/json"}, 415)
                    return
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw.decode("utf-8") or "{}")
                if route == "/api/plan/decision":
                    import job as job_module
                    try:
                        result = job_module.decide(
                            root, payload.get("decision") or "",
                            token=str(payload.get("token") or ""))
                    except PermissionError as exc:
                        self._json({"error": str(exc)}, 403)
                        return
                    except ValueError as exc:
                        self._json({"error": str(exc)}, 400)
                        return
                    self._json(result)
                elif route == "/api/run":
                    import job as job_module
                    try:
                        result = job_module.start_run(
                            root, token=str(payload.get("token") or ""),
                            reviewer=str(payload.get("reviewer") or "heuristic"),
                            model=payload.get("model") or None,
                            coder=str(payload.get("coder") or "agentic"))
                    except PermissionError as exc:
                        self._json({"error": str(exc)}, 403)
                        return
                    except ValueError as exc:
                        self._json({"error": str(exc)}, 400)
                        return
                    self._json(result)
                elif route == "/api/plan/generate":
                    import job as job_module
                    import planner
                    goal = str(payload.get("goal") or "").strip()
                    if not goal:
                        self._json({"error": "goal is empty"}, 400)
                        return
                    with _GENERATE_LOCK:
                        state = job_module.read_state(root)
                        if state.get("phase") in ("running", "paused"):
                            self._json({"error": "a job is running; wait or stop it"}, 409)
                            return
                        try:
                            plan = planner.generate(goal, root=root)
                        except Exception as exc:            # noqa: BLE001
                            self._json({"error": f"planner failed: {exc}"[:400]}, 400)
                            return
                        out = Path(root) / ".agents" / "tmp" / "auto_plan.json"
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
                        try:
                            info = job_module.submit_plan(
                                plan, root, source=".agents/tmp/auto_plan.json")
                        except Exception as exc:            # noqa: BLE001
                            self._json({"error": f"plan rejected: {exc}"[:400]}, 400)
                            return
                        self._json({"plan": plan, "job_id": info["job_id"],
                                    "token": info["token"]})
                elif route == "/api/control":
                    import job as job_module
                    try:
                        result = job_module.set_control(
                            root, payload.get("action") or "",
                            token=str(payload.get("token") or ""),
                            value=payload.get("value"))
                    except PermissionError as exc:
                        self._json({"error": str(exc)}, 403)
                        return
                    except ValueError as exc:
                        self._json({"error": str(exc)}, 400)
                        return
                    self._json(result)
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
    parser.add_argument("--open", action="store_true", help="open the browser automatically")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return serve(args.port, root=args.root, ledger=args.ledger, host=args.host,
                 open_browser=args.open)


if __name__ == "__main__":
    raise SystemExit(main())
