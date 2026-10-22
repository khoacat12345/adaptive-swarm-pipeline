---
name: adaptive-swarm-pipeline
description: Run, observe and harden the Adaptive Swarm Pipeline in the project's `.agents/` folder. Use when the user asks to run a coding task through the swarm (Recipe C), to inspect or replay a run, to open the local dashboard, to see cost/token/fallback statistics, to get memory suggestions from past runs, or to verify the pipeline health (Phase 0 / 0.5 / 1 / 2 / 3 / 4 acceptance suites).
---

# Adaptive Swarm Pipeline

Pipeline giao viec cho nhieu model AI, nhung **chi Lead duoc ghi file** va **chi may
that duoc quyen noi "xong"**. Ban la nguoi van hanh skill nay: chay dung lenh, doc
ket qua that, khong tu suy dien la thanh cong.

## Khi nao dung

- Nguoi dung giao mot task sua code cu the (1 file, co lenh verify).
- Can xem lai "lan chay truoc da lam gi" (replay / dashboard).
- Can biet model nao dang loi, ton bao nhieu token, fallback may lan.
- Can nghiem thu lai toan bo pipeline truoc khi giao viec that.

## 2 buoc de chay mot task

### Buoc 1 - Scout (1 lan moi may / khi doi model)

```powershell
python .agents/scout.py                  # preview, KHONG ghi
python .agents/scout.py --write          # ghi config.json (co backup)
python .agents/scout.py --write --verify # + ping thu 1 request/vai tro
```

Scout doc catalog + auth cua OpenCode, xep hang model theo vai tro va ghi
`.agents/config.json`. Khong co buoc nay thi pipeline van chay bang config cu.

### Buoc 2 - Giao task

Tao `order.json` (Work Order 5 truong bat buoc):

```json
{
  "task_id": "T01",
  "title": "off-by-one",
  "level": 1,
  "objective": "Sua ham sum_range de tinh tong dung n phan tu",
  "boundaries": "Chi sua calc.py, khong doi API",
  "input_context": "calc.py dong 3: for i in range(n - 1)",
  "acceptance_criteria": "check.py exit 0, sum_range(5) == 10",
  "verify_command": "\"C:\\...\\python.exe\" check.py",
  "target_files": ["calc.py"],
  "sandbox_files": ["calc.py", "check.py"],
  "max_rounds": 2
}
```

```powershell
python .agents/recipe_c.py --order order.json          # chay that
python .agents/recipe_c.py --order order.json --json   # may doc
python .agents/recipe_c.py --order order.json --dry-run
```

Ket qua: `status=applied` (da ghi + verify) hoac `aborted` (khong ghi gi).

## Quan sat (0 request, 0 token)

```powershell
python .agents/replay.py --last              # timeline run gan nhat
python .agents/replay.py --list              # liet ke cac run (moi nhat truoc)
python .agents/replay.py <run_id> --json     # may doc
python .agents/dashboard.py --port 8080 --open
python .agents/job.py --plan scratch/swarm_demo/plan.json --root . --wait --open   # plan + duyet tren web
python .agents/cost_report.py                # token / request / fallback
python .agents/memory.py                     # loi hay gap + goi y
```

Dashboard: `http://127.0.0.1:8080/` (sidebar run history | chat da agent | diff &
inspector). 4 stage pages: `#plan` (intelligence + dependency phases + subtasks),
`#warroom`, `#verify`, `#report`. `#analyze` redirect to `#plan`, `#done` alias to `#report`. Chat la chatbox:
Lead ben phai, cac vai khac ben trai. Full plan `.md` tai `/api/plan/md`.
Run mac dinh `heuristic` (0 reviewer); wave chi khi chon/`--reviewer wave`.
Server chi bind loopback, khong mo ra mang.

## Nghiem thu

```powershell
python .agents/run_phase0_acceptance.py     # 35/35
python .agents/run_phase05_acceptance.py    # 69/69
python .agents/tests/smoke_suite.py         # 53/53 engine + 10/10 task
python .agents/tests/test_phase3.py --fast  # 51/51
python .agents/tests/test_phase4.py         # 36/36 (gom ca 4 suite tren)
python .agents/tests/test_level3.py         # 47/47 (Level 3 + hoi quy 5 suite)
python .agents/tests/test_phase5.py         # 57/57 (router + FallbackReviewer + hoi quy)
python .agents/tests/test_quota.py          # 26/26 (quota that + route theo quota)
python .agents/tests/test_phase6.py         # 70/70 (plan + duyet + state/control + chay)
python .agents/tests/test_planner.py        # 17/17 (goal -> plan + webhook)
python .agents/tests/test_wave.py           # 21/21 (4 reviewer song song)
python .agents/tests/test_telegram.py       # 15/15 (bot 2 chieu)
```

## Bat bien (khong duoc vi pham)

1. **Chi Lead ghi file du an.** Coder chi ghi trong `.agents/draft/` va thu muc tam
   ngoai du an; moi thay doi vao file that phai qua `safe_writer`.
2. **Verify bang may that.** Khong co `verify_command` hoac verify fail -> khong apply.
3. **Rollback nguyen byte.** Backup truoc khi ghi; verify fail -> tra ve dung hash cu.
   Level 3: backup TAT CA file truoc, verify fail -> batch rollback toan bo, khong de
   file nao o trang thai do dang (co journal de khoi phuc sau crash).
4. **Tran cung:** 8 request / 300 giay moi run; cham tran -> dung, khong ghi.
5. **Secret khong ra khoi may.** `.env`, khoa, token bi chan/redact truoc khi gui model.
6. **Ledger append-only.** Khong sua, khong xoa event; schema v1.0.0 chi doc, khong
   rewrite file cu.
7. **Khong tu doi config.json / roadmap** khi chua duoc nguoi dung duyet.

## Xu ly su co

| Hien tuong | Nguyen nhan thuong gap | Cach xu ly |
|---|---|---|
| `Work Order bi tu choi` | thieu verify_command / file dich khong ton tai / muc tieu mo ho | sua order theo dung 1 khoi `CAN LAM RO` |
| `plan bi tu choi` | > 6 node, > 2 nhanh, node type la, thieu verify | sua plan hoac de recipe tu sinh |
| `status=aborted` | het vong, verify fail, cham tran | xem `replay.py` + `memory.py` de biet loi that |
| `fallback: tat ca model that bai` | het quota / mat mang | doi `--model`, cho cooldown, xem `tmp/health.json` |
| Dashboard trong | sai `--root` | `--root <du an>` (mac dinh `.`) |

## File quan trong

| File | Vai tro |
|---|---|
| `.agents/config.json` | ke hoach model theo vai tro (scout sinh ra) |
| `.agents/ledger.jsonl` | nhat ky append-only, nguon du lieu duy nhat cho quan sat |
| `.agents/schema.py` | schema v1.0.0 + chuan hoa event cu |
| `.agents/recipe_c.py` | dong co pipeline (verify 2 lan, circuit breaker, budget) |
| `.agents/router.py` | chon model theo loai viec W0-W8 (main/escalate, khac ho) |
| `.agents/quota.py` | doc usage that tu `opencode stats`, cat theo tran Go |
| `.agents/job.py` | plan + duyet tren dashboard + chay tuan tu |
| `.agents/planner.py` | goal -> plan.json (W4 planner) |
| `.agents/notify.py` | webhook 1 chieu (Discord/Telegram/generic) |
| `.agents/telegram_bot.py` | bot Telegram 2 chieu (/approve /pause /say) |
| `.agents/fallback.py` | chuoi du phong + health thu dong |
| `.agents/safe_writer.py` | nguoi ghi file duy nhat (backup/atomic/rollback) |
| `.agents/draft/` | ban nhap cua Coder (de xem diff) |
| `.agents/backups/` | ban truoc khi ghi (de rollback/diff) |
