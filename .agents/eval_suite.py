"""Eval harness: do ty le pass THAT cua tung model qua Recipe C.

Khac voi smoke_suite (do engine bang ScriptedCoder), file nay do CHAT LUONG MODEL:
moi task duoc chay lai tu dau trong mot thu muc tam rieng, voi model that qua
`opencode run`, va ket qua duoc kiem chung doc lap bang chinh verify command.

Nguyen tac do:
  - 1 task = 1 thu muc tam sach (khong tai su dung trang thai giua cac lan)
  - ket qua "pass" chi khi: pipeline applied VA verify doc lap exit 0
  - model het quota / khong ton tai -> danh dau unavailable va bo cac task con lai
    (khong tinh la "model lam sai", va khong dot thoi gian)
  - ghi ket qua ngay tung dong (results.jsonl) de co the dung giua chung va --resume
  - khong bao gio ghi vao du an that

Cach dung:
    python .agents/eval_suite.py --models "opencode/a-free,opencode/b-free"
    python .agents/eval_suite.py --tasks T01,T05 --debug
    python .agents/eval_suite.py --resume --out .agents/eval/20261020-1200
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE / "tests") not in sys.path:
    sys.path.insert(0, str(HERE / "tests"))

import recipe_c                      # noqa: E402
import system_bot                    # noqa: E402
import work_order as work_order_module  # noqa: E402
import workers                       # noqa: E402
import tasks as task_defs            # noqa: E402

TEMP_ROOT = Path(tempfile.gettempdir()) / "opencode"
PASS_GATE = 0.8
MAX_ROUNDS = 2
STOP_KINDS = {"coder_quota", "coder_model"}
FATAL_CONSECUTIVE_TIMEOUTS = 2


def model_slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_").replace("#", "_")


def prepare_root(task: dict, work_root: Path) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"eval_{task['id']}_", dir=str(work_root)))
    for name, content in task["files"].items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    return root


def build_order(task: dict):
    return work_order_module.create(
        task_id=task["id"], title=task["title"], level=task["expect_level"],
        objective=task["prompt"],
        boundaries=f"Chi sua {task['target']}; khong tao file moi, khong sua file test",
        input_context=", ".join(task["sandbox_files"]),
        acceptance_criteria=f"lenh verify phai exit 0: {task['verify']}",
        verify_command=task["verify"], target_files=[task["target"]],
        sandbox_files=task["sandbox_files"], max_rounds=MAX_ROUNDS,
    )


def _stage_of(feedback: str) -> str:
    marker = "o buoc ["
    if marker not in feedback:
        return "unknown"
    return feedback.split(marker, 1)[1].split("]", 1)[0]


def classify(report: dict, coder, verify_exit) -> tuple[bool, str]:
    """Tra ve (pass, kind). kind la nhan loi ngan gon de thong ke."""
    if report["status"] == "applied" and verify_exit == 0:
        return True, "pass"
    if report["status"] == "applied":
        return False, "verify_after_apply"
    kinds = getattr(coder, "error_kinds", []) or []
    last_kind = kinds[-1] if kinds else None
    stage = _stage_of(report["feedback"][-1]) if report["feedback"] else "unknown"
    if stage == "coder":
        return False, f"coder_{last_kind or 'error'}"
    if stage.startswith("verify-ban-nhap"):
        return False, "draft_verify"
    if stage.startswith("format"):
        return False, "ladder"
    if stage.startswith("reviewer"):
        return False, "reviewer"
    if stage.startswith("apply"):
        return False, "apply_verify"
    if stage.startswith("ghi"):
        return False, "io"
    if last_kind in STOP_KINDS:
        return False, f"coder_{last_kind}"
    return False, "aborted"


def run_case(task: dict, model: str, *, timeout: int, work_root: Path,
             debug_dir: Path | None, verbose: bool) -> dict:
    root = prepare_root(task, work_root)
    order = build_order(task)
    coder = workers.AgenticCoder(model, timeout=timeout, debug_dir=debug_dir)
    reviewer = workers.HeuristicReviewer()
    started = time.perf_counter()
    crash = None
    try:
        report = recipe_c.run(
            order, coder=coder, reviewer=reviewer, root=root,
            run_id=f"eval-{task['id'].lower()}", log=True,
        )
    except Exception as exc:                        # noqa: BLE001
        crash = f"{type(exc).__name__}: {exc}"[:300]
        report = {"status": "aborted", "feedback": [], "rounds": 0, "requests": 0,
                  "patch_level": None, "file_changed": False, "error": crash,
                  "worker_edits_detected": 0, "coder_calls": coder.calls}
    seconds = time.perf_counter() - started

    # Kiem chung doc lap: tu chay verify, khong tin bao cao cua pipeline.
    verify = system_bot.run_command(
        task["verify"], cwd=root, timeout=min(timeout, 120), log=False,
    ) if not crash else {"exit_code": None}

    passed, kind = classify(report, coder, verify.get("exit_code"))
    if crash:
        kind = "crash"
    if not passed and debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        target = root / task["target"]
        if target.exists():
            try:
                text, _encoding = workers.read_source_file(target)
                (debug_dir / "final.txt").write_text(text, encoding="utf-8")
            except (OSError, ValueError):
                pass
        draft = root / ".agents" / "draft" / task["id"] / task["target"]
        if draft.exists():
            try:
                shutil.copy2(draft, debug_dir / "draft.txt")
            except OSError:
                pass
        (debug_dir / "feedback.txt").write_text(
            "\n\n".join(report.get("feedback") or [])[:4000], encoding="utf-8")
    usage = getattr(coder, "usage", {}) or {}
    record = {
        "model": model,
        "task": task["id"],
        "kind_task": task["kind"],
        "pass": passed,
        "kind": kind,
        "status": report.get("status"),
        "rounds": report.get("rounds", 0),
        "requests": getattr(coder, "calls", 0),
        "reviewer_calls": report.get("reviewer_calls", 0),
        "seconds": round(seconds, 1),
        "patch_level": report.get("patch_level"),
        "file_changed": report.get("file_changed"),
        "worker_edits": report.get("worker_edits_detected", 0),
        "verify_exit": verify.get("exit_code"),
        "tokens_in": usage.get("input"),
        "tokens_out": usage.get("output"),
        "cost": usage.get("cost", 0),
        "coder_errors": list(getattr(coder, "errors", [])),
        "error": (report.get("error") or crash or "")[:300] or None,
    }
    if verbose:
        mark = "PASS" if passed else "FAIL"
        print(f"  {task['id']:>3}  {mark}  {kind:<16} {seconds:5.1f}s  "
              f"rounds={record['rounds']} bac={record['patch_level']}")
    shutil.rmtree(root, ignore_errors=True)
    return record


def summarize(records: list[dict]) -> dict:
    models: dict[str, dict] = {}
    for record in records:
        entry = models.setdefault(record["model"], {
            "model": record["model"], "total": 0, "pass": 0, "seconds": 0.0,
            "requests": 0, "tokens_in": 0, "tokens_out": 0, "kinds": {},
            "unavailable": False,
        })
        entry["total"] += 1
        entry["pass"] += 1 if record["pass"] else 0
        entry["seconds"] += record["seconds"]
        entry["requests"] += record["requests"] or 0
        entry["tokens_in"] += record["tokens_in"] or 0
        entry["tokens_out"] += record["tokens_out"] or 0
        entry["kinds"][record["kind"]] = entry["kinds"].get(record["kind"], 0) + 1
    for entry in models.values():
        done = entry["total"] or 1
        entry["rate"] = round(entry["pass"] / done, 3)
        entry["avg_seconds"] = round(entry["seconds"] / done, 1)
        entry["avg_rounds"] = round(
            sum(r["rounds"] for r in records if r["model"] == entry["model"]) / done, 2)
        entry["verdict"] = "DAT" if entry["rate"] >= PASS_GATE else "CHUA DAT"
    return models


def recommend(models: dict) -> str | None:
    usable = [m for m in models.values() if m["rate"] >= PASS_GATE]
    if not usable:
        return None
    usable.sort(key=lambda m: (-m["rate"], m["avg_seconds"]))
    return usable[0]["model"]


def render(models: dict, records: list[dict]) -> str:
    lines = []
    header = f"{'MODEL':<38} {'PASS':>7} {'RATE':>6} {'AVG_S':>7} {'REQ':>5} {'TOK_IN':>8} {'TOK_OUT':>8}  VERDICT"
    lines.append(header)
    lines.append("-" * len(header))
    for entry in sorted(models.values(), key=lambda m: (-m["rate"], m["avg_seconds"])):
        lines.append(
            f"{entry['model'][:38]:<38} {entry['pass']:>3}/{entry['total']:<3} "
            f"{entry['rate'] * 100:>5.0f}% {entry['avg_seconds']:>7.1f} "
            f"{entry['requests']:>5} {entry['tokens_in']:>8} {entry['tokens_out']:>8}  "
            f"{entry['verdict']}"
            + ("  (khong dung duoc: het quota/khong co model)" if entry.get("unavailable") else "")
        )
    lines.append("")
    tasks = []
    for record in records:
        if record["task"] not in tasks:
            tasks.append(record["task"])
    if tasks:
        lines.append("MA TRAN TASK (P = pass, - = fail):")
        lines.append("MODEL".ljust(38) + " " + " ".join(f"{t:>4}" for t in tasks))
        for entry in sorted(models.values(), key=lambda m: -m["rate"]):
            cells = []
            for task_id in tasks:
                found = next((r for r in records
                              if r["model"] == entry["model"] and r["task"] == task_id), None)
                cells.append("   P" if found and found["pass"] else "   -")
            lines.append(entry["model"][:38].ljust(38) + " " + " ".join(cells))
    lines.append("")
    lines.append("PHAN LOAI LOI:")
    for entry in sorted(models.values(), key=lambda m: -m["rate"]):
        detail = ", ".join(f"{k}={v}" for k, v in sorted(entry["kinds"].items()))
        lines.append(f"  {entry['model'][:38]:<38} {detail}")
    best = recommend(models)
    lines.append("")
    lines.append("KHUYEN NGHI: " + (f"{best} (dat gate {PASS_GATE:.0%}, nhanh nhat trong nhom dat)"
                                     if best else "khong model nao dat gate 80%"))
    return "\n".join(lines)


def default_models() -> list[str]:
    chain = []
    config_path = HERE / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    for role in ("coder", "reviewer"):
        entry = (data.get("roles") or {}).get(role) or {}
        for model in entry.get("chain") or []:
            if isinstance(model, str) and model not in chain:
                chain.append(model)
    return chain


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Do chat luong model that qua Recipe C.")
    parser.add_argument("--models", default=None,
                        help="danh sach model, cach nhau bang dau phay (mac dinh: chain trong config.json)")
    parser.add_argument("--tasks", default="all", help="vd: T01,T05 hoac all")
    parser.add_argument("--timeout", type=int, default=240, help="timeout moi lan goi model")
    parser.add_argument("--out", default=None, help="thu muc bao cao")
    parser.add_argument("--resume", action="store_true", help="dung lai results.jsonl da co")
    parser.add_argument("--debug", action="store_true", help="luu prompt/output cua model")
    parser.add_argument("--budget", type=int, default=1800, help="gioi han tong thoi gian (giay)")
    parser.add_argument("--json", action="store_true", help="chi in JSON ra stdout")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    models = ([item.strip() for item in args.models.split(",") if item.strip()]
              if args.models else default_models())
    if not models:
        print("khong co model nao de do (truyen --models)", file=sys.stderr)
        return 2

    all_tasks = {task["id"]: task for task in task_defs.TASKS}
    if args.tasks == "all":
        selected = list(all_tasks.values())
    else:
        wanted = [item.strip() for item in args.tasks.split(",") if item.strip()]
        missing = [item for item in wanted if item not in all_tasks]
        if missing:
            print(f"task khong ton tai: {', '.join(missing)}", file=sys.stderr)
            return 2
        selected = [all_tasks[item] for item in wanted]

    out_dir = Path(args.out) if args.out else (
        HERE / "eval" / time.strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    records: list[dict] = []
    if args.resume and results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    done = {(r["model"], r["task"]) for r in records}

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    work_root = Path(tempfile.mkdtemp(prefix="eval_work_", dir=str(TEMP_ROOT)))
    deadline = time.time() + args.budget
    unavailable: set[str] = set()
    consecutive_timeouts: dict[str, int] = {}

    if not args.quiet:
        print(f"models : {len(models)}")
        print(f"tasks  : {len(selected)}")
        print(f"out    : {out_dir}")
        print("")

    with results_path.open("a", encoding="utf-8") as sink:
        for model in models:
            if model in unavailable:
                continue
            if not args.quiet:
                print(f"[{model}]")
            for task in selected:
                if (model, task["id"]) in done:
                    continue
                if time.time() > deadline:
                    if not args.quiet:
                        print("  -- het ngan sach thoi gian, dung lai --")
                    break
                debug_dir = (out_dir / "debug" / model_slug(model) / task["id"]
                             if args.debug else None)
                record = run_case(task, model, timeout=args.timeout,
                                  work_root=work_root, debug_dir=debug_dir,
                                  verbose=not args.quiet)
                records.append(record)
                sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                sink.flush()
                if record["kind"] in STOP_KINDS:
                    unavailable.add(model)
                    if not args.quiet:
                        print(f"  -- model khong dung duoc ({record['kind']}), bo qua --")
                    break
                consecutive_timeouts[model] = (
                    consecutive_timeouts.get(model, 0) + 1
                    if record["kind"] == "coder_timeout" else 0)
                if consecutive_timeouts[model] >= FATAL_CONSECUTIVE_TIMEOUTS:
                    if not args.quiet:
                        print("  -- timeout lien tiep, bo qua model nay --")
                    break
            if time.time() > deadline:
                break

    shutil.rmtree(work_root, ignore_errors=True)
    models_summary = summarize(records)
    for model in unavailable:
        if model in models_summary:
            models_summary[model]["unavailable"] = True
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gate": PASS_GATE,
        "models": models_summary,
        "recommendation": recommend(models_summary),
        "results": records,
    }
    (out_dir / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(models_summary, records))
        print("")
        print(f"bao cao: {out_dir / 'report.json'}")
    return 0 if all(m["rate"] >= PASS_GATE for m in models_summary.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
