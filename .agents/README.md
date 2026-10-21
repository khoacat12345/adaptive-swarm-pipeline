# .agents/ — Safety, Verification & Scout (Adaptive Swarm Pipeline)

Lop ha tang nay **doc lap hoan toan** voi ma nguon du an: no khong import, khong sua,
khong phu thuoc vao bat ky file nao cua du an. Du an chi bi tac dong qua
`safe_writer` (backup -> ghi atomic -> verify -> rollback).

- **Phase 0** — lop an toan: profile_detector, system_bot, ledger, safe_writer.
- **Phase 0.5** — *Scout*: doc catalog model + auth, anh xa vai tro, sinh `config.json`.
- **Phase 1** - pipeline Level 1-3: classifier, work_order, permissions, format ladder,
  recipe_c (co circuit breaker), smoke suite 10 task.
- **Phase 2** — eval harness: do ty le pass that cua tung model.
- **Phase 3** — chiu loi & bao mat: fallback chain + health, plan validator, session
  policy, secret scanner, clarify gate, budget cap, cost report.
- **Phase 4** — quan sat & dong goi: schema v1.0.0 dong bang, replay CLI, web dashboard
  3 cot (0 framework), memory engine rut kinh nghiem, SKILL.md + tai lieu.

## Kien truc 5 tang

```
+-----------------------------------------------------------------------+
| 5. DONG GOI        SKILL.md | test suites | README | eval/             |
+-----------------------------------------------------------------------+
| 4. QUAN SAT        ledger.jsonl -> schema v1.0.0                      |
|                    replay.py | dashboard.py | cost_report | memory.py |
+-----------------------------------------------------------------------+
| 3. THI HANH        workers (Coder/Reviewer) | fallback.py (health)     |
|                    session policy | format ladder 0/1/2               |
+-----------------------------------------------------------------------+
| 2. DIEU PHOI       classifier -> work_order -> clarify -> plan_validator
|                    recipe_c: vong [coder -> draft verify -> reviewer]  |
+-----------------------------------------------------------------------+
| 1. AN TOAN         system_bot (guard) | permissions (role/write scope)|
|                    safe_writer: backup -> atomic -> verify -> rollback |
+-----------------------------------------------------------------------+
```

Luong chay mot task:

```
task -> classify Level -> Work Order (5 truong + verify_command)
     -> clarify (1 lan hoi) -> plan validator (<=6 node, <=2 nhanh)
     -> [ Coder (chain: deepseek -> glm -> minimax)
          -> format ladder (whole -> replace -> ...)
          -> SystemBot verify ban nhap
          -> Reviewer doc lap ]  lap <= max_rounds
     -> Lead: backup -> ghi atomic -> verify lan 2 -> apply | rollback
     -> ledger -> replay / dashboard / cost_report / memory
```

Hai dieu khong doi qua ca 4 phase:

1. **Chi Lead duoc ghi file du an.** Coder chi cham vao `.agents/draft/`.
2. **Chi may that duoc noi "xong".** Khong verify duoc thi khong apply.

## Dung nhanh trong 2 buoc

```powershell
# Buoc 1 (1 lan / khi doi may): scout -> ghi config.json
python .agents/scout.py --write

# Buoc 2 (moi task): giao Work Order
python .agents/recipe_c.py --order order.json
```

`order.json` toi thieu:

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

Xem lai ket qua (khong ton token):

```powershell
python .agents/replay.py --last
python .agents/dashboard.py --port 8080 --open
```

## Cau truc

```
.agents/
├── README.md              <- file nay
├── .gitignore             <- chan tmp/, backups/, ledger, secret
├── profile.json           <- ho so du an (do profile_detector.py sinh ra)
├── config.json            <- [PHASE 0.5] ke hoach model theo vai tro (CO commit)
├── ledger.jsonl           <- nhat ky su kien append-only (JSON Lines)
├── ledger.py              <- [0.4] doc/ghi/validate ledger
├── system_bot.py          <- [0.3] chay lenh that: guard + timeout + JSON
├── profile_detector.py    <- [0.2] nhan dien stack -> profile.json
├── safe_writer.py         <- [0.5] backup + atomic write + rollback
├── scout.py               <- [0.5.1-0.5.4] do catalog + sinh config.json
├── inspect_plan.py        <- [0.5] doc config.json -> bang ke hoach de doc
├── classifier.py          <- [1.1] phan loai task Level 0/1/2/3 (rules-first)
├── work_order.py          <- [1.2] Work Order 5 truong + validator
├── permissions.py         <- [1.3] phan quyen Coder/Reviewer/Lead + FindingsSink
├── format_ladder.py       <- [1.4] thang ghi file bac 0/1/2 + tu ha bac
├── workers.py             <- [1.5] Coder/Reviewer: scripted + agentic + opencode JSON
├── recipe_c.py            <- [1.5-1.6] dong co pipeline + circuit breaker + guard
├── eval_suite.py          <- [2] do ty le pass THAT cua tung model
├── fallback.py            <- [3.1-3.2] chuoi du phong + bo nho suc khoe thu dong
├── plan_validator.py      <- [3.3] tham dinh ke hoach (dong node type, tran nhanh)
├── secret_scan.py         <- [3.5] chan/redact secret truoc khi gui model
├── clarify.py             <- [3.6] gom moi diem mo ho vao 1 lan hoi
├── cost_report.py         <- [3.8] ke toan chi phi tu ledger
├── schema.py              <- [4.1] schema ledger v1.0.0 + chuan hoa event cu
├── replay.py              <- [4.2] timeline run tu ledger (0 request, 0 token)
├── dashboard.py           <- [4.3] web dashboard 3 cot (http.server, 0 framework)
├── memory.py              <- [4.4] rut kinh nghiem loi/model -> goi y cho Coder
├── SKILL.md               <- [4.5] OpenCode Skill (kich hoat bang 1 lenh)
├── tests/
│   ├── tasks.py           <- [1.7] 10 task mau (5 bug, 3 refactor, 2 bay)
│   ├── smoke_suite.py     <- [1.7] chay 10 task + do pass rate / apply fail
│   ├── test_phase3.py     <- [3] nghiem thu fallback/secret/plan/budget/session
│   └── test_phase4.py     <- [4] nghiem thu schema/replay/dashboard/memory + hoi quy
├── eval/                  <- [2] ket qua do model (results.jsonl + report.json)
├── run_phase0_acceptance.py  <- kich ban nghiem thu Phase 0
├── run_phase05_acceptance.py <- kich ban nghiem thu Phase 0.5
├── draft/                 <- code nhap (CO commit — de lam bang chung)
├── backups/               <- ban sao luu file truoc khi ghi (KHONG commit)
└── tmp/                   <- log tam cua SystemBot + health.json (KHONG commit)

agents/                    <- cau noi de chay `python -m agents.<module>`
├── __init__.py            <- them .agents/ vao sys.path
├── scout.py               <- chuyen tiep sang .agents/scout.py
└── tests/smoke_suite.py   <- chuyen tiep sang .agents/tests/smoke_suite.py
```

## Quick start

```powershell
# 0.2 — nhan dien du an (chay 1 lan moi khi doi may)
python .agents/profile_detector.py --root .

# 0.3 — chay lenh kiem tra that
python .agents/system_bot.py --cmd "python -m py_compile scratch/test_calc.py" --human
python .agents/system_bot.py --check "curl http://x"       # chi kiem tra guard -> 126

# 0.5 — ghi file an toan (co verify, tu rollback neu fail)
python .agents/safe_writer.py write --path scratch/a.py --content-file new.py `
       --verify "python -m py_compile scratch/a.py"

# 0.4 — doc nhat ky
python .agents/ledger.py tail -n 10
python .agents/ledger.py validate
python .agents/ledger.py stats

# PHASE 0.5 — scout: xem truoc roi moi ghi
python .agents/scout.py                 # preview, KHONG ghi (mac dinh)
python .agents/scout.py --write         # ghi config.json (tu backup ban cu)
python .agents/scout.py --write --verify        # + micro-ping 1 request/vai tro
python .agents/scout.py --free-only     # rang buoc cung: chi model mien phi
python .agents/scout.py --cost-policy cost-first
python .agents/scout.py --no-cli        # offline: dung snapshot du phong
python .agents/scout.py --json --write
python .agents/inspect_plan.py          # doc config.json -> bang ke hoach de doc

# PHASE 1 — pipeline
python .agents/classifier.py --task "Sua typo trong readme.md"
python .agents/tests/smoke_suite.py            # 10 task + do pass rate
python -m agents.tests.smoke_suite             # cach goi tuong duong
python .agents/recipe_c.py --order order.json  # mac dinh --coder agentic

# PHASE 2 — do chat luong model that
python .agents/eval_suite.py --models "opencode/a-free,opencode/b-free"
python .agents/eval_suite.py --tasks T01,T05 --debug
python .agents/eval_suite.py --resume --out .agents/eval/phase2-free

# PHASE 3 — chiu loi / bao mat / ngan sach
python .agents/recipe_c.py --order order.json            # tu dung chuoi fallback
python .agents/recipe_c.py --order order.json --no-fallback --model opencode/x-free
python .agents/recipe_c.py --order order.json --max-requests 8 --max-seconds 300
python .agents/cost_report.py                            # bang chi phi tu ledger
python .agents/cost_report.py --run <run_id> --json
python .agents/plan_validator.py --plan plan.json        # tham dinh ke hoach
python .agents/secret_scan.py src/app.py .env            # quet truoc khi gui model
python .agents/clarify.py --order order.json             # 1 lan hoi duy nhat

# PHASE 4 — quan sat / bo nho / dong goi
python .agents/schema.py --describe                      # hop dong du lieu v1.0.0
python .agents/schema.py --ledger .agents/ledger.jsonl   # kiem tra ledger
python .agents/replay.py --last                          # timeline run gan nhat
python .agents/replay.py --list                          # lich su cac run
python .agents/dashboard.py --port 8080 --open           # web dashboard 3 cot
python .agents/memory.py                                 # loi hay gap + goi y
python .agents/memory.py --json --limit 5

# nghiem thu
python .agents/run_phase0_acceptance.py
python .agents/run_phase05_acceptance.py
python .agents/tests/smoke_suite.py
python .agents/tests/test_phase3.py            # + --fast (bo smoke) + --real (model that)
python .agents/tests/test_phase4.py            # + --fast (bo 4 suite hoi quy)
```

## Phase 0.5 — Scout

### Quy tac cung

| # | Quy tac |
|---|---|
| 1 | Scout chay **1 lan khi cai dat / doi may**. TUYET DOI khong chay trong luc thuc thi task |
| 2 | Mac dinh **KHONG probe** — khong ban token nao. Chi probe khi co co `--verify` |
| 3 | Mac dinh **khong ghi file** — chi in Preview Diff. Phai co `--write` moi ghi |
| 4 | Ghi qua `safe_writer` -> luon co backup truoc khi ghi de |
| 5 | Moi vai tro phai co chuoi `[primary, fallback_1, fallback_2]` (toi thieu 2 model) |
| 6 | `config.json` khong bao gio chua API key (chi ghi `auth_status`) |

### Nguon catalog (theo thu tu uu tien)

1. `--catalog-file PATH` — JSON day du metadata (dung cho CI / offline).
2. `opencode models` — danh sach model that cua may (nguon chinh).
3. `<config_dir>/opencode.json` — provider nao co `apiKey`, model nao duoc khai bao.
4. **Builtin snapshot** — danh sach model chup tu may nay (co ngay chup), dung khi
   khong goi duoc CLI; luon kem canh bao "model co the da doi ten hoac bi go".

### Auth status

`opencode auth list` + `provider.options.apiKey` trong config + bien moi truong
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...).

| Gia tri | Nghia | Xu ly |
|---|---|---|
| `available` | Co credential (console auth / apiKey / env) | Dung |
| `unknown` | Provider duoc khai bao nhung khong ro credential | Dung (co canh bao) |
| `unconfigured` | Khong co credential | **Loai bo** (tru khi `--include-unconfigured`) |

### Metadata

`context_window`, `pricing`, `supports_cache`, `is_free`, `cost_tier` duoc dien tu:

- bang `CURATED_METADATA` — chi ghi nhung gi **da kiem chung** bang cong cu models;
- `--catalog-file` (neu co);
- con lai -> `null`, `metadata_source = "heuristic-v1"`.

**Truong khong biet thi de `null`, khong bao gio bia so.**

### Anh xa vai tro (heuristic-v1)

Diem moi vai tro = `sum(trong_so[k] * nang_luc[k]) + bonus_ho - phat_chi_phi`,
voi `nang_luc` (code / reasoning / long_context / tooling) suy ra tu ho model + token
ten model (`thinking`, `codex`, `flash`, `opus`, `pro`, ...).

| Vai tro | Trong so chinh | Uu tien ho |
|---|---|---|
| `coder` | code 0.60, long_context 0.20 | deepseek, qwen, kimi, gpt-codex, glm |
| `reviewer` | reasoning 0.45, code 0.30 | claude, gpt, gemini, grok |
| `security` | reasoning 0.60, code 0.30 | claude, gpt, deepseek, glm, nemotron |
| `architect` | long_context 0.45, reasoning 0.30 | gemini, claude, gpt, grok |
| `test_writer` | code 0.55, tooling 0.20 | gpt-codex, deepseek, qwen, claude |

### Chon primary: bai toan gan co rang buoc

5 `primary` duoc chon bang cach giai bai toan gan: **5 vai tro thuoc 5 HO KHAC NHAU**,
toi da hoa `sum(diem[vai_tro][ho] * trong_so_vai_tro)`. Khac ho la rang buoc cung, vi
reviewer/security trung ho nhau thi gia tri phan bien doc lap bi triet tieu.

`trong_so_vai_tro` — thu tu *chi phi khi sai*, khong phai so thich:

| Vai tro | Trong so | Ly do |
|---|---|---|
| coder | 1.0 | Sai o day lan ra toan bo san pham |
| reviewer | 0.9 | De lot loi trong san pham |
| security | 0.9 | De lot lo hong |
| architect | 0.8 | Sua thiet ke ton kem nhung khong lam hong ngay |
| test_writer | 0.7 | Test sai thi cuu duoc |

Vi du: neu toi da hoa **tong diem khong trong so**, `deepseek-v4.1-flash-free` bi day
sang `test_writer` (vi coder con `glm-5.3-flash-free` diem gan bang), tuc model code
manh nhat lai di viet test. Trong so vai tro sua dung su vo ly do.

### Chuoi du phong: 2 cua thoat cho 2 kieu su co da quan sat

Moi vai tro co toi da 3 model, xep theo *loai su co* chu khong chi theo diem:

| Vi tri | Chon gi | De chong su co |
|---|---|---|
| `primary` | model diem cao nhat (5 primary khac ho nhau) | — |
| `du phong 1` | model **mien phi** tot nhat | Het quota / `Insufficient account funds` |
| `du phong 2` | model cua **provider khac** | Provider sap / model bi go |

Ly do: do tren may that, kieu hong thuong gap nhat la het quota, kieu thu hai la
provider/proxy chet. Neu ca 3 vi tri cung mot tier gia thi ca chuoi chet cung luc.

He qua: **mot config dung duoc o ca hai trang thai tai khoan** — co quota thi chay
`primary`, het quota thi Phase 1 tu tut xuong `du phong 1` (mien phi). Neu so ho co
san < 5, scout lui ve greedy va cho phep trung ho.

### Cost policy

| Co | Phat moi bac chi phi | Dung khi |
|---|---|---|
| `--cost-policy capability-first` | 0.00 | Can chat luong cao nhat, khong quan tam gia |
| `--cost-policy balanced` (mac dinh) | 0.03 | Can bang |
| `--cost-policy cost-first` | 0.06 | Tiet kiem toi da, uu tien model free |

Gia tri dang dung duoc ghi vao `config.json` -> `cost_policy`.

### `--free-only` — rang buoc cung (khac voi uu tien)

`--cost-policy` chi la **uu tien** (tru diem). `--free-only` la **rang buoc cung**: loai
bo moi model `is_free=False` truoc khi anh xa. Dung khi tai khoan khong con quota va ban
can mot ke hoach chac chan chay duoc. Ghi vao `config.json` -> `constraints.free_only`.

### `config.json` (rut gon)

```json
{
  "schema_version": 1,
  "version": "0.5.0",
  "scouted_at": "2026-10-19T18:30:24.123Z",
  "generated_by": "scout.py",
  "mapping_method": "heuristic-v1",
  "cost_policy": "balanced",
  "roles": {
    "coder": {
      "chain": ["opencode/deepseek-v4.1-flash-free", "...", "..."],
      "primary": "opencode/deepseek-v4.1-flash-free",
      "fallbacks": ["...", "..."],
      "primary_provider": "opencode",
      "primary_family": "deepseek",
      "primary_cost_tier": "free",
      "why": "coder-score=1.017; family=deepseek; code=0.92; ...; cost=free"
    }
  },
  "models":      { "<id>": { "provider": "...", "auth_status": "...", "context_window": null,
                             "pricing": null, "is_free": false, "cost_tier": "...",
                             "supports_cache": null, "capabilities": {}, "scoring": "heuristic-v1" } },
  "catalog":     { "source": "opencode-cli", "stats": {}, "providers": {} },
  "platform":    { "os": "nt", "python": "3.12.10", "opencode_cli": "...", "opencode_version": "opencode v2.0.32" },
  "verify":      { "enabled": false, "mode": "off", "results": null },
  "warnings":    []
}
```

`roles.<vai_tro>.why` la **bang chung giai thich** tai sao model do duoc chon.

### Preview Diff — khong bao gio ghi ngoai y muon

- Chua co config -> in bang `TAO MOI` voi tung chuoi model.
- Da co config -> in bang diff theo vai tro (`primary` doi + `chain` doi) va
  **bang `==` la khong doi**, `>>` la doi.
- Config cu > **30 ngay** -> canh bao model co the da bi doi ten hoac go bo.
- Khong co `--write` -> khong ghi gi. Sua tay config roi chay lai (khong co `--write`)
  **khong lam mat thay doi cua nguoi dung**.

### Che do `--json`

Khi dung `--json`, **stdout chi chua JSON** (moi thong bao khac di ra stderr) de
script khac parse duoc.

### `--verify` (mac dinh TAT)

```powershell
python .agents/scout.py --verify                        # toi da 5 request (1/vai tro)
python .agents/scout.py --verify --verify-roles coder   # chi 1 request
python .agents/scout.py --verify --verify-template "..."  # doi lenh ping (dung cho test)
```

Gui dung **1 request micro-ping cho moi vai tro** (`opencode run -m <model> "Reply with
exactly: OK"`), ghi ket qua vao `config.json` -> `verify.results` va ghi ledger
`scout.verify`. Co `--verify-timeout` (mac dinh 90s).

### Lenh `python -m`

Python **khong ho tro** ten module bat dau bang dau cham:

```powershell
python -m .agents.scout   # -> "Relative module names not supported"  (Python tu choi)
```

Cac dang dung duoc:

```powershell
python -m agents.scout    # qua cau noi agents/ (cwd = thu muc goc)
python .agents/scout.py   # truc tiep
cd .agents; python -m scout
```

### Ket qua nghiem thu Phase 0.5

`python .agents/run_phase05_acceptance.py` -> **69/69 PASS**.

| Ma | Noi dung |
|---|---|
| DoD-1 | `scout --write` sinh ra `config.json` |
| DoD-2 | Du 5 vai tro, moi vai tro >= 2 model (primary + du phong) |
| DoD-3 | Sua tay config -> chay lai khong `--write` -> **khong bi ghi de** |
| EX-1 | Preview khong tu tao file |
| EX-2 | Ghi de -> co backup dung ban cu (chua nguyen ban sua tay) |
| EX-3 | Canh bao khi config cu > 30 ngay |
| EX-4 | `--verify` mac dinh TAT: khong sinh request nao |
| EX-5 | `--verify` bat len: co micro-ping + ghi ledger + ghi vao config |
| EX-6 | `--no-cli` -> dung snapshot du phong |
| EX-7 | `--cost-policy cost-first` -> coder la model free |
| EX-8 | Config va ledger deu qua validate |
| EX-9 | `python -m agents.scout` chay duoc; `python -m .agents.scout` bi tu choi |
| EX-10 | `config.json` khong ro ri API key |
| EX-11 | `--free-only`: moi model deu mien phi, van du 5 vai tro x 5 ho khac nhau |
| EX-12 | Kich ban nghiem thu tra `config.json` ve dung ban goc (khong pha trang thai nguoi dung) |
| EX-13 | Moi chuoi vai tro co san ca 2 cua thoat: model free + provider khac |

Micro-ping that (khong dung template gia) da duoc chay cho ca 5 vai tro — xem muc
"Bang chung do tren may that" ben duoi.

## Bang chung do tren may that (`--verify`)

Chay `--verify` ngay sau khi sinh ke hoach la cach duy nhat biet ke hoach co *goi duoc*
that khong. Ket qua do tren may nay (2026-10-19):

**Ke hoach mac dinh (`balanced`) — 1/5:**

| Vai tro | Primary | Ket qua |
|---|---|---|
| coder | `opencode/deepseek-v4.1-flash-free` | OK, 3.9 s |
| reviewer | `9router/ag/claude-opus-4-6-thinking` | FAIL sau 82 s — proxy local khong dung duoc model |
| security | `opencode/gpt-5.5-pro` | FAIL — `Insufficient account funds` |
| architect | `opencode/gemini-3.1-pro` | FAIL — `Insufficient account funds` |
| test_writer | `opencode/gpt-5.3-codex` | FAIL — `Insufficient account funds` |

**Ke hoach `--free-only` — 5/5:**

| Vai tro | Primary | Ket qua |
|---|---|---|
| coder | `opencode/deepseek-v4.1-flash-free` | OK, 2.8 s |
| reviewer | `opencode/minimax-m3.1-flash-free` | OK, 1.7 s |
| security | `opencode/nemotron-3-ultra-free` | OK, 3.4 s |
| architect | `opencode/longcat-2.5-preview-free` | OK, 4.8 s |
| test_writer | `opencode/glm-5.3-flash-free` | OK, 4.6 s |

Ket luan: tren may nay **chi tang model mien phi la goi duoc** (tai khoan OpenCode Console
het quota; proxy `9router` o `127.0.0.1:20128` khong dung duoc model). Scout tu sinh
canh bao tu ket qua verify, nen ke hoach `balanced` se di kem 4 canh bao "KHONG goi duoc"
chu khong am tham dua ra mot ke hoach chet.

**Ke hoach dang dung trong `config.json`**: `--free-only` (5/5 verify OK) — quyet dinh cua
Product Owner. Sau khi nap them quota, quay lai ke hoach chat luong cao bang:

```powershell
python .agents/scout.py --write --verify      # ve ke hoach balanced
python .agents/scout.py --free-only --write --verify   # quay lai chi model free
```

## Phase 1 - Pipeline Level 1-3 (Recipe C)

Muc tieu: fix bug / sua 1-2 file hang ngay chay tu dong, ton 1-2 request, co verify
bang may that, va khong the lam hong file.

### Luong chay

```
Task
 -> Lead: classifier + Work Order                      (rules-first, 0 request)
 -> lap round 1..max_rounds (=2):
      Coder     -> patch JSON                          (1 request)
      Ladder    -> ap vao BAN NHAP .agents/draft/...   (bac 0/1/2, tu ha bac)
      SystemBot -> verify ban nhap trong shadow dir    <-- chua cham du an
      Reviewer  -> doc diff (chi Level 2, read-only)   <-- 0 request (luat)
      Lead      -> safe_writer: backup -> ghi -> verify -> rollback neu fail
    verify fail -> feedback -> vong tiep
 -> het luot -> ABORT + bao cao, file goc nguyen ven
```

Hai lan verify la co y: lan 1 tren ban nhap de **khong cham vao du an** khi con sai;
lan 2 tren file that de chot hop dong "may that chung minh", va luon co rollback.

### Level

| Level | Khi nao | Xu ly |
|---|---|---|
| 0 | cau hoi / giai thich | khong dung code |
| 1 | sua 1 cho ro rang trong 1 file | 1 vong, khong reviewer |
| 2 | bug logic, refactor 1 file, hoac vung rui ro (auth, token, db, migration...) | co reviewer doc diff |
| 3 | >= 2 file, refactor phu thuoc cheo | giao dich nguyen tu da file: backup tat ca -> ghi tat ca -> verify toan du an -> commit/rollback tat ca |

Rules-first: dem so file + khop tu khoa. Chi goi LLM khi tin hieu xung dot, prompt
<= 200 token; LLM tra loi rac -> Level 2. Smoke suite: phan loai dung 10/10 task,
**0 request** cho khau phan loai.

### Level 3 - giao dich da file

Work Order phai co `level: 3` va >= 2 `target_files`; thu tu trong danh sach chinh la
thu tu apply (file nen / dependency truoc). Level 1/2 chi nhan 1 file (validator tu choi).

- **Patch set:** AgenticCoder sua TAT CA file trong 1 lan goi (1 request), tra ve
  `{file: patch}`; file khong doi -> giu ban goc.
- **Verify ban nhap:** 1 shadow dir chua ca tap file + sandbox, chay verify 1 lan.
- **Reviewer:** doc TOAN BO diff cua tap file (`review_set`), bat loi lech logic giua
  module (doi ten ham o file A nhung noi goi o file B khong doi...).
- **Apply:** `safe_writer.Transaction` backup TAT CA truoc khi ghi byte nao, ghi atomic
  tung file, verify toan du an, roi commit hoac **batch rollback ve 100% byte goc**.
- **Journal:** `.agents/tmp/tx/<tx_id>.json`; process bi kill giua giao dich -> lan chay
  sau tu `recover_open_transactions()` rollback truoc khi lam gi khac.
- **Nghiem thu:** `tests/test_level3.py` (47 check): apply da file, rollback toan dien,
  crash recovery, patch_set, fallback, va hoi quy Phase 0-4.

### Router W0-W8 (Phase 5B)

`router.py` chon chuoi coder theo LOAI CONG VIEC, khong theo ten du an. Tin hieu
do duoc: so file, risk keyword, context token, modality, so lan fail/reject.

| Ma | Loai viec | MAIN | ESCALATE |
|---|---|---|---|
| W0 | phan loai | rules (0 request) | GLM-5.3-Flash Free |
| W1 | sua nho 1 file | DeepSeek Flash Free | GLM-5.3-Flash Free |
| W2 | logic / da file | DeepSeek V4 Pro | Kimi K2.7 Code |
| W3 | kiem toan / bao mat | GLM-5.3 | Claude Haiku 5.5 |
| W4 | thiet ke / chia viec | Qwen3.8 Max | GLM-5.3 |
| W5 | viet test | Kimi K2.7 Code | GLM-5.3 |
| W6 | giao dien / multimodal | GPT-6 Luna | Qwen3.8 Max (Kimi K3 on-demand) |
| W7 | tai lieu / context lon | Claude Haiku 5.5 | MiniMax M3 |
| W8 | trong tai khi bat dong | Qwen3.8 Max | GLM-5.3 |

Thu tu uu tien tin hieu: W8 > W6 > W3 > W5 > W4 > W0 > W7 > W2 > W1.
`validate_roles()` bat buoc main/escalate khac ho o W3/W5/W8. Dung:
`python .agents/recipe_c.py --order order.json --route`.

**Quota that:** `quota.py` doc `opencode.db` (`session_message`: cost/token tung
request) de tinh chi phi/request theo model, va goi **API console**
`/console/api/go/status` (token OAuth da luu trong `credential`) de lay **meters
chinh xac** cho Rolling/Weekly/Monthly (used/limit/resetsAt). Dashboard hien thi
dung % cua console; neu API khong truy cap duoc thi tu fallback ve uoc luong
`opencode.db` va ghi ro `local estimate`. Tat API bang `SWARM_CONSOLE=0`.
Fallback `opencode stats` da bo de tranh lech cua so.

`--route` chon theo thu tu: **main** (con >=10%) -> **escalate** (chi khi con >=50%,
hoac `--allow-escalate`) -> **fallback re/free** (deepseek/glm free) -> `exhausted`
(chan run, `--force` de bo qua). Khong bao gio tu nang cap khi main can ma escalate
cung khan. Xem nhanh: `python .agents/quota.py`.

### Job plan + duyet (Phase 6A)

```powershell
# chay demo co san (plan nam trong repo):
python .agents/job.py --plan scratch/swarm_demo/plan.json --root . --wait --open

# du an khac (thay bang duong dan that, KHONG dung dau < >):
python .agents/job.py --plan plan.json --root "C:\path\to\project" --wait --open
```

`--open`: tu khoi dong dashboard neu port trong + mo trinh duyet kem token.
Khong co `--open`: chi in URL de ban tu mo.

Windows: double-click `dashboard.cmd` de mo dashboard (tu chay server + mo browser).
Trang poll 3s va chi ve lai phan thay doi (khong nhay/F5); quota bar cap nhat toi da 1 lan/phut.

Plan gom `job_id`, `objective`, `subtasks[]`. Moi subtask: `id`, `title`, `objective`,
`verify_command`, `target_files`, `sandbox_files`, `level`, `max_rounds`, `depends_on`.

- Duyet **ca plan** mot lan; chay tuan tu theo topo, dung ngay khi 1 buoc fail.
- Khong duyet / timeout -> **khong ghi file nao**.
- Moi subtask tu chon model theo W0-W8 + quota (fallback ladder).
- Token 1 lan chi in ra CLI; dashboard khong tra token qua API.
- Dashboard (tieng Anh) hien them **quota bar** (plan chung 5h/7d/30d + model dang
  dung) va **quick access**: copy lenh `job.py --plan ... --wait --open` de chay lai.
- **5 stage pages** (deep link): `#analyze` (scope, dependency, verify/sandbox coverage,
  risks, agent readiness), `#plan` (subtask cards + approval), `#warroom` (Lead + 5
  agents: Coder, Security, QA, Perf, Docs; orange = running, blue = next, green =
  approved, gray = idle/standby), `#verify` (machine contract + ledger evidence),
  `#done` (files, duration, tokens, reproduce command). War room co replay slider
  (0 request) va canh bao `heuristic` (reviewer khong duoc goi).
- `GET /api/team` tra 5 agent + route + wave verdict + model health (read-only).
- **Timestamps**: gio bat dau run, gio tung event trong chat, gio cap nhat plan, gio
  doc quota (checked).
- **Hourly usage**: hover 1 cot gio de xem cost ($), tokens (in/out/cache) va so request.

### Mission control (stepper / roadmap / control bar)

- **Stepper 5 buoc**: Analyze -> Plan -> War room -> Verify -> Done (bam 1 buoc de xem
  tom tat: goal/plan, team, verify command, file da apply).
- **Project roadmap**: checklist subtask + file, tu cap nhat theo `job_state.json`.
- **Team lineup**: coder / fallback / reviewer cua buoc hien tai.
- **Drafts**: xem ban nhap trong `.agents/draft/` (read-only, chan path escape).
- **Control bar**: Pause/Resume, Stop, va o "Leader direction" — y kien duoc chen vao
  `input_context` cua subtask ke tiep.

State: `.agents/tmp/job_state.json`; control: `.agents/tmp/control.json` (ghi bang token
tu plan; dashboard khong tra token). Pause co hieu luc **sau subtask hien tai**.

### Work Order (5 truong)

`objective`, `boundaries`, `input_context`, `acceptance_criteria`, `verify_command`.

Thieu `verify_command` -> `WorkOrderRejected` **ngay**, khong goi model. Validator con
chay `verify_command` qua guard cua SystemBot truoc khi bat dau (fail fast) va chan
cung `max_rounds > 4`.

### Phan quyen

| Vai tro | Quyen ghi | Quyen doc |
|---|---|---|
| Coder | CHI `.agents/draft/<task>/` | file du an (tru secret) |
| Reviewer | KHONG. Chi duoc trao `FindingsSink` (append ledger) | file du an (tru secret) |
| Lead | file du an, va **chi qua `safe_writer`** | tat ca |

Reviewer khong phai "duoc bao la read-only": no khong he duoc trao mot ham ghi file
nao. Moi lan tu choi deu ghi ledger (`permission.denied`).

### Format ladder

| Bac | Cach | Dieu kien |
|---|---|---|
| 0 | `whole` — ghi de ca file | file <= **150 dong** |
| 1 | `replace` — khoi SEARCH/REPLACE | khoi search phai khop **duy nhat** |
| 2 | `symbol` — thay theo ten ham/class (dung `ast`) | ten phai duy nhat |

That bai -> **tu ha bac va thu lai 1 lan**. Vi du that (T09): Coder tra `whole` cho
file 184 dong -> bac 0 bi tu choi dung luat -> engine tu sinh khoi SEARCH/REPLACE tu
diff roi ap dung lai -> `replace*`.

Chuan hoa khoang trang 2 muc: bo trailing space, roi khop chiu duoc lech indent
(tab vs 4 space) va tu chinh lai indent theo file dich.

Bat bien: `apply_patch()` **khong bao gio** tra ve code sai cu phap — file goc parse
duoc thi ket qua cung phai parse duoc, neu khong thi `PatchFailed`.

Bat bien thu hai: **khong doi byte ngoai y muon**. Kieu xuong dong (CRLF/LF), BOM va
encoding cua file goc duoc giu nguyen; file cp1252 duoc ghi tra bang cp1252. Khong
dung `str.splitlines()` (no cat ca `\x0c`, `\x85`, `\u2028` ben trong string literal).

### Circuit breaker

`max_rounds = 2` (`MAX_ROUNDS_HARD_CAP`; validator tu choi neu lon hon). Het 2 vong:
`status = aborted`, bao cao kem feedback tung vong, file goc khong doi.

### Ket qua smoke suite (`python .agents/tests/smoke_suite.py`)

```
[1/4] Engine checks (classifier/work_order/ladder/permissions): 53/53

ID   KIND      LVL  CLS  RDS  BAC       STATUS   VERIFY  TIME    RESULT
T01  bug       1    ok   1    replace   applied  pass    0.47    PASS
T02  bug       2    ok   1    symbol    applied  pass    0.43    PASS
T03  bug       2    ok   1    replace   applied  pass    0.46    PASS
T04  bug       1    ok   1    replace   applied  pass    0.45    PASS
T05  bug       2    ok   2    replace   applied  pass    0.47    PASS
T06  refactor  2    ok   1    whole     applied  pass    0.45    PASS
T07  refactor  2    ok   1    symbol    applied  pass    0.45    PASS
T08  refactor  2    ok   1    replace   applied  pass    0.46    PASS
T09  trap      2    ok   1    replace*  applied  pass    0.45    PASS
T10  trap      2    ok   1    replace   applied  pass    0.45    PASS

[2/4] Circuit breaker (2 vong sai -> abort): PASS (rounds=2 status=aborted)

Pass rate       : 10/10 (100.0%)   gate >= 80%   PASS
Apply fail      : 0/10 (0.0%)      gate < 10%    PASS
Calls / task    : 1.1 coder + 0.8 reviewer = 1.9
Giay / task     : 0.45s (chua tinh do tre model)
File hong       : 0/10   PASS
Ma nguon        : 27 file, khong doi   PASS
Ledger          : 10/10 hop le (89 event, 0 invalid)   PASS
KET LUAN: DAT
```

| Task | Loai | Level | Bac | Vong | Chung minh |
|---|---|---|---|---|---|
| T01 | bug | 1 | replace | 1 | sua 1 dong; Level 1 khong ton reviewer |
| T02 | bug | 2 | symbol | 1 | bac 2 thay ca than ham |
| T03 | bug | 2 | replace | 1 | mutable default argument |
| T04 | bug | 1 | replace | 1 | `split('=', 1)` giu du lieu |
| T05 | bug | 2 | replace | **2** | vong 1 sai -> feedback -> vong 2 dung |
| T06 | refactor | 2 | whole | 1 | bac 0 tren file ngan |
| T07 | refactor | 2 | symbol | 1 | thay than ham, giu indent method |
| T08 | refactor | 2 | replace | 1 | 2 edit trong 1 file |
| T09 | trap | 2 | replace* | 1 | file 184 dong -> tu ha bac 0 -> 1 |
| T10 | trap | 2 | replace | 1 | file tab + trailing space |

Ngoai 10 task con co: 51 check engine (classifier 10, work order 6, ladder 15,
permissions 8, workers 10, source/encoding 2) va 1 kich ban circuit breaker (2 vong
sai -> abort, file nguyen ven 100% byte).

### Ca bien da xu ly (loi that, co test hoi quy)

| Ca | Truoc | Sau |
|---|---|---|
| File CRLF (Windows) | bi doi thanh LF ca file -> diff rac | giu nguyen CRLF (do kieu xuong dong cua file goc) |
| File BOM | BOM bi nhan doi / mat | giu dung 1 BOM |
| String chua `\x0c` | `splitlines()` cat doi -> doi noi dung chuoi | khong dung `splitlines()`, chi cat theo `\n` |
| File cp1252/latin-1 | `UnicodeDecodeError` lam sap pipeline | do encoding, ghi tra dung encoding do |
| File rong | - | bac 0 xu ly duoc |
| File dich khong ton tai | - | `WorkOrderRejected` |

Day la nhom loi nguy hiem nhat vi no **khong lam test fail**: code van chay dung,
nhung toan bo file bi doi byte (git blame mat tac dung, review khong doc duoc diff).

### Do luong: do *engine*, khong phai do model

Smoke suite dung `ScriptedCoder` (kich ban tat dinh), nen ket qua 100% la **nang luc
engine**, khong phai "model fix duoc 100%". Chat luong model la bien so khac: do bang
`eval_suite.py` (xem **Phase 2** o duoi).

### Dung voi model that

`order.json`:

```json
{
  "task_id": "fix-042",
  "title": "off-by-one trong sum_range",
  "level": 2,
  "objective": "Sua off-by-one trong sum_range cua calc.py",
  "boundaries": "Chi sua calc.py, khong sua test",
  "input_context": "calc.py, test_calc.py",
  "acceptance_criteria": "python test_calc.py tra exit code 0",
  "verify_command": "python test_calc.py",
  "target_files": ["calc.py"],
  "sandbox_files": ["calc.py", "test_calc.py"],
  "max_rounds": 2
}
```

```powershell
python .agents/recipe_c.py --order order.json                      # agentic (mac dinh)
python .agents/recipe_c.py --order order.json --dry-run            # chay den reviewer, khong ghi file that
python .agents/recipe_c.py --order order.json --model opencode/deepseek-v4.1-flash-free
python .agents/recipe_c.py --order order.json --coder opencode     # che do xin JSON (it model chiu)
```

Model coder mac dinh lay tu `config.json` (`roles.coder.primary`) — hien la model free.
`--reviewer heuristic` (mac dinh) ton 0 request; `--reviewer llm` moi ton them 1;
`--reviewer none` tat han reviewer (chi nen dung khi da biet ro rui ro).

**Da ho tro Level 3 (da file):** `level: 3` + >= 2 `target_files` -> pipeline dung
giao dich nguyen tu `safe_writer.Transaction` (backup het -> ghi het -> verify toan du
an -> commit / batch rollback) + journal khoi phuc sau crash. Level 1/2 van chi nhan
1 file; khai nhieu file o Level 2 bi validator tu choi.

### Vi sao co `AgenticCoder` (va vi sao no an toan)

Do bang thuc nghiem tren chinh may nay: khi yeu cau model tra ve JSON patch, model
qua `opencode run` **khong** lam theo — no goi tool de sua file (round 1 khong co JSON,
va file that bi sua). Doi sang agent `plan` (read-only) thi model lai tra loi bang
van xuoi kieu ke hoach. Ca hai deu sai hop dong.

Nen `AgenticCoder` lam nguoc lai: **cho model dung tool**, nhung trong mot **ban sao
tam** cua cac file cua task. Sau khi model sua xong, pipeline **doc lai file** va dua
ve dang patch `whole`. Phan con lai khong doi: ladder -> verify ban nhap -> reviewer ->
`safe_writer`. Model duoc loi the cua no (doc file, chay test), con bat bien "chi Lead
ghi file" van dung, va moi thay doi deu phai qua kiem chung doc lap.

Ba lop chan, khong lop nao dua vao long tot cua model:

| Lop | Noi dung |
|---|---|
| 1. `cwd` ngoai du an | model chay trong `%TEMP%/opencode/swarm_worker_<run_id>/r<N>`, khong thay du an |
| 2. snapshot | truoc moi vong, hash `target` + `sandbox_files`; sau vong, khac hash thi khoi phuc nguyen byte va ghi `recipe.guard.restore` |
| 3. Lead ghi | ket qua van phai qua `safe_writer` (backup -> atomic -> verify -> rollback) |

Lop 2 bat duoc ca truong hop model dung `shell` de ghi file (agent `build` co quyen
shell). Test hoi quy: `guard: worker sua file that -> khoi phuc, van apply dung`.

### Lenh `python -m`

`python -m .agents.tests.smoke_suite` **khong chay duoc**: dau `.` dau tien lam Python
hieu la relative import ngoai package. Ba cach dung duoc:

```powershell
python .agents/tests/smoke_suite.py          # truc tiep
python -m agents.tests.smoke_suite           # qua shim agents/
cd .agents; python -m tests.smoke_suite      # tu trong .agents
```

## Phase 2 — Do chat luong model that (eval harness)

Phase 1 chung minh **engine dung**. Phase 2 tra loi cau hoi khac: **model nao lam
duoc viec that, va lam tot den dau**.

### Nguyen tac do

| # | Quy tac |
|---|---|
| 1 | Moi (model, task) chay trong **mot thu muc tam sach**; khong tai su dung trang thai |
| 2 | "Pass" chi khi pipeline `applied` **va** verify doc lap (harness tu chay lai) exit 0 |
| 3 | Model het quota / khong ton tai -> danh dau `unavailable`, **bo cac task con lai** (khong tinh la model lam sai, khong dot thoi gian) |
| 4 | Ghi tung dong vao `results.jsonl` -> dung giua chung duoc, `--resume` chay tiep |
| 5 | Cung mot task fixture, cung mot prompt, cung `max_rounds=2` -> so sanh duoc giua cac model |
| 6 | Khong bao gio ghi vao du an that (moi thu trong `%TEMP%/opencode/eval_*`) |

### Lenh

```powershell
python .agents/eval_suite.py --models "opencode/a-free,opencode/b-free"
python .agents/eval_suite.py --tasks T01,T05 --debug      # 2 task, luu prompt/output
python .agents/eval_suite.py --resume --out .agents/eval/phase2-free
python .agents/eval_suite.py --json                      # stdout la JSON thuan
```

Tham so: `--timeout` (giay moi lan goi model, mac dinh 240), `--budget` (tong thoi
gian, mac dinh 1800), `--debug` (luu prompt + output + file cuoi cua ca that bai).

### Dau ra

```
.agents/eval/<lan>/
├── results.jsonl    <- 1 dong / (model, task): pass, kind, rounds, seconds, tokens...
├── report.json      <- tong hop theo model + khuyen nghi
└── debug/<model>/<task>/*.prompt.txt, *.output.txt, *.file.txt, final.txt, feedback.txt
```

### Phan loai loi (`kind`)

| kind | Nghia |
|---|---|
| `pass` | applied + verify doc lap exit 0 |
| `coder_timeout` | model qua thoi gian cho |
| `coder_quota` | het quota / khong du tien -> dung model do |
| `coder_model` | model khong ton tai |
| `coder_parse` | output khong dung dinh dang (chi o che do `--coder opencode`) |
| `ladder` | patch khong ap duoc vao file |
| `draft_verify` | ban nhap chay verify that bai (model sua sai) |
| `reviewer` | reviewer tu choi |
| `apply_verify` | ghi that xong nhung verify that bai -> da rollback |
| `verify_after_apply` | hiem: pipeline bao applied nhung verify doc lap fail (coi nhu loi nghiem trong) |
| `crash` | pipeline nem exception (khong duoc phep xay ra) |

### Ket qua do (2026-10-20, 4 model free)

May: Windows, Python 3.12.10, opencode 2.0.32. Cau hinh do: `--coder agentic`,
reviewer heuristic, `max_rounds=2`, 10 task, moi (model, task) mot thu muc tam sach.
Chi phi: **0 dong** (toan model free).

| Model | Pass | Ty le | TB giay/task | Request | Token vao | Token ra |
|---|---|---|---|---|---|---|
| `glm-5.3-flash-free` | 10/10 | 100% | 29.4 | 10 | 19.776 | 571 |
| `deepseek-v4.1-flash-free` | 10/10 | 100% | 31.2 | 11 | 8.130 | 781 |
| `minimax-m3.1-flash-free` | 10/10 | 100% | 80.2 | 11 | 4.311 | 1.414 |
| `nemotron-3-ultra-free` | 9/10 | 90% | 35.6 | 11 | 78.430 | 523 |

Tong: 40 luot do, ~43 request that, ~25 phut.

> Ghi chu (Phase 4): ID `nemotron-3-ultra-free` da duoc thay bang
> `opencode/nemotron-3.5-super-vl-free` trong `config.json` theo catalog moi. Bang
> tren la so lieu lich su cua model cu, khong do lai. Chuoi coder hien tai
> (deepseek -> glm -> minimax -> nemotron) do nguoi dung chot, khong phai de xuat
> cua muc 6 ben duoi.

Nhan xet:

1. **Ca 4 model free deu >= 90%** — pipeline dung duoc voi model that, khong chi voi
   kich ban gia lap. Day la ket qua quan trong nhat cua Phase 2.
2. **Chi phi token chenh lech rat lon**: nemotron dot 78k token vao cho cung 10 task
   (gan 10x deepseek). Voi model free co quota, day la tieu chi chon model, khong chi
   la toc do.
3. minimax cham 2.7x (80s/task) du token ra nhieu nhat.
4. **T09 (file 184 dong) pass o ca 4 model** -> co che tu ha bac `whole` -> `replace`
   hoat dong ca voi model that, khong chi voi patch scripted.
5. Fail duy nhat: nemotron T06 bi reviewer chan ("xoa qua nhieu code >50%"). Chay lai
   rieng T06 thi **pass** -> loi dao dong cua model, khong tai lap; reviewer khong
   chan nham mot cach he thong (nhung lan do ta khong giu duoc ban nhap vi debug
   chua kip bat).
6. `config.json` hien tai (deepseek primary, glm fallback) **duoc so lieu xac nhan**:
   deepseek 100% + it token nhat trong nhom nhanh; glm nhanh nhat nhung dot 2.4x token.

Khuyen nghi (chua tu doi config): giu `deepseek` lam coder chinh, `glm` lam du phong;
nemotron nen ra khoi chuoi coder (90% + 78k token/lan).

Gioi han cua lan do nay: 10 task nho (file <= 200 dong), 1 file/task, moi model chay
**1 lan** (chua lap lai de tinh phuong sai), model free co the doi hanh vi theo thoi
diem va theo quota con lai.

## Phase 3 — Chiu loi, bao mat & dieu phoi ban dong

Muc tieu: bien pipeline thanh he thong tu phuc hoi, khong de mot model chet / mot
lan 429 / mot file chua secret lam dut task, va khong cho mot task dot vo han
request hoac thoi gian.

Chuoi `roles.coder.chain` trong `config.json` da duoc cap nhat theo ket qua do
that Phase 2 (deepseek -> glm -> minimax -> nemotron). Luu y: chay lai
`scout.py --write` se sinh lai chuoi theo heuristic va **khong giu** thu tu nay.

### 1. Fallback chain + suc khoe thu dong (3.1, 3.2) — `fallback.py`

- Chuoi lay tu `config.json` (`roles.coder.chain`), `primary` luon dung dau.
- **Khong ping truoc.** Goi thang model dau; chi khi that bai moi chuyen con ke
  tiep **trong cung luot goi** — nguoi dung khong phai can thiep.
- Moi that bai ghi `{model, latency_ms, status, error_type}` vao
  `.agents/tmp/health.json` (khong commit) va dat cooldown:

  | error_type | Cooldown | Vi du |
  |---|---|---|
  | `quota` / `rate_limit` | 300 s | HTTP 429, het credit |
  | `timeout` | 120 s | CLI qua han |
  | `model` | 3600 s | ten model khong ton tai |
  | `cli` | 60 s | CLI loi khac |
  | `noop` / `patch` / `parse` | 0 s | model song nhung ban sua khong dung |

- Model dang cooldown **bi bo qua, khong dot request**. Lan chay sau doc lai
  health.json nen tranh duoc model vua chet.
- Loi chat luong (`noop`, `patch`) van chuyen model nhung **khong** cooldown:
  model con song, chi la lan nay khong sua duoc.

Bang chung (test that, `--real`): model dau la ten chet
`opencode/definitely-dead-model-free` -> ghi nhan 1 fail -> chuyen
`opencode/deepseek-v4.1-flash-free` -> task `applied`, file sua dung.

### 2. Plan Validator (3.3) — `plan_validator.py`

Lead duoc tu bat/tat node tuy chon theo recipe, nhung ke hoach phai qua validator
**truoc khi chay**. Chan cung:

| Dieu kien | Ly do tra ve |
|---|---|
| node type ngoai danh muc dong (`architect, coder, reviewer, security, perf, qa, systembot, lead`) | `node #i co type la '...'` |
| > 6 node | `qua nhieu node: 7 > 6` |
| > 2 nhanh song song (1 nhom > 2 node) | `nhom song song #i co 3 nhanh > 2` |
| node `systembot` thieu `verify_command` | `node 'systembot' thieu 'verify_command'` |
| bo node bat buoc (`lead/coder/systembot`) khong co `reason` | `thieu node bat buoc 'x' va khong co 'reason'` |

Recipe C tu sinh plan cua no (`lead -> coder -> [reviewer] -> systembot`) va tu
tham dinh; ke hoach sai thi Work Order bi tu choi truoc khi goi model.

```powershell
python .agents/plan_validator.py --plan plan.json
```

### 3. Session policy (3.4) — `workers.SessionPolicy`

- Vong 1..2: **giu phien** — vong 2 xuat phat tu chinh ban sua cua vong 1 (model
  nho ngu canh draft cua no).
- Tu vong 3: **cat phien**, copy lai tu ban goc + brief ngan
  (`PHIEN MOI` + draft gan nhat + loi chua xu ly) de chong phinh context va ao giac.
- `MAX_ROUNDS_HARD_CAP` nang 2 -> 4 de quy tac nay co hieu luc; **mac dinh Work
  Order van la 2**, va tran 8 request van chan truoc.
- Moi vong ghi `session_events` (`fresh | resume`, `context_reset`) vao report.

### 4. Secret Scanner (3.5) — `secret_scan.py`

Quet **truoc khi bat ky noi dung nao roi khoi may**:

| Nhom | Bat gi |
|---|---|
| Duong dan | `.env*`, `*.pem/.key/.p12/.pfx/.jks`, `id_rsa`, `id_ed25519`, `credentials*`, `.npmrc/.netrc/.pypirc`, `*secret*` |
| Noi dung | Anthropic `sk-ant-...`, OpenAI `sk-...`, AWS `AKIA...`, Google `AIza...`, Stripe `sk_live_...`, GitHub `ghp_...`, Slack `xox...`, JWT `eyJ...`, private key block, connection string co mat khau, `api_key/password/token = "..."` |

Chinh sach:

- File dich chua secret -> **Work Order bi tu choi** (khong the vua gui model vua
  giu nguyen byte file).
- File nhay cam trong `sandbox_files` -> bi loai khoi ban sao gui model
  (`secrets_blocked`), verify van chay local.
- Prompt gui model -> redact thanh `[REDACTED_SECRET]`.
- **Finding khong bao gio chua gia tri that**: chi co `kind`, dong, preview da che
  (`sk-ant-...3f2a`). Ledger nhan `secret.blocked` voi cung nguyen tac.

### 5. Clarify Gate (3.6) — `clarify.py`

Gom het diem mo ho vao **dung 1 khoi hoi** (`CAN LAM RO TRUOC KHI CHAY`):
thieu truong bat buoc, verify_command bi guard chan, muc tieu chung chung
("sua loi", "fix bug"), tieu chi nghiem thu qua ngan, file dich khong ton tai.
Khong hoi lat nhat: 1 exception `ClarifyRequired` = 1 cau hoi, va khong ton
request nao truoc khi du ro.

### 6. Budget cap (3.7) — trong `recipe_c.run`

- Tran cung moi run: **8 request** va **300 giay**. Hai co `--max-requests` /
  `--max-seconds` chi de **ha xuong**, khong the nang (tran la tuyet doi).
- Kiem tra **truoc moi vong** va **sau moi lan coder** (vi mot lan fallback co the
  dot nhieu request). Cham tran -> dung ngay, ghi `budget.exceeded`, file goc
  khong doi (chua apply gi).
- `fallback.py` nhan `limit_check` tu recipe nen khong the "vuot tran bang
  cach doi model".

### 7. Cost report (3.8) — `cost_report.py`

```powershell
python .agents/cost_report.py
```

```
run_id                 status     req    in_tok   out_tok  time_s fb_rate
--------------------------------------------------------------------------
rc_20261020T070130_759 applied      1       247        98    16.0     0.0
--------------------------------------------------------------------------
TOTAL                               1       247        98    16.0     0.0
runs=1  avg_time=16030.0ms  fallbacks=0  cache_read=0
```

`fb_rate` = so lan phai doi model / tong request cua run. Du lieu lay tu event
`recipe.done` (co `usage.in_tokens/out_tokens` va `data.fallback_attempts`).

### 8. Nghiem thu Phase 3 — `tests/test_phase3.py`

| Nhom | Noi dung | Ket qua |
|---|---|---|
| 1 | Fallback: model dau chet -> model 2 cuu task | PASS (+ ban that `--real`) |
| 2 | Secret scan: key `sk-ant-api03-...` bi chan/redact, khong lot vao ledger | PASS |
| 3 | Plan validator: 3 nhanh song song / thieu verify_command / node la / >6 node / omitted thieu reason | PASS |
| 4 | Khong hoi quy: chay lai smoke suite Phase 1 | 53/53 PASS |
| 5 | Budget cap, session policy (fresh -> resume -> fresh), cost report, clarify gate | PASS |

```powershell
python .agents/tests/test_phase3.py           # 52/52 (gom smoke suite)
python .agents/tests/test_phase3.py --fast    # bo smoke suite
python .agents/tests/test_phase3.py --real    # 55/55, them 1 lan goi CLI that
```

Ket qua do that (2026-10-20): `--real` **55/55 PASS**; smoke suite Phase 1 van
**53/53 engine check, 10/10 task**. End-to-end CLI voi model that:

```
recipe_c.py --order ... --root <temp> --json
status=applied  rounds=1  requests=1  fallback_failures=0
usage: in=247 out=98  duration=16.0s  budget: 1/8 request
```

### Gioi han cua Phase 3

- Fallback chi bat loi **o tang goi model**; neu model sua sai ma verify bat duoc
  thi do la vong lap feedback (co the them fallback theo chat luong sau).
- Cooldown luu trong `tmp/health.json`, chua co backoff luy tien (2 lan 429 lien
  tiep van cung mot moc 5 phut).
- Scanner la **regex**, co the duong tinh gia voi fixture test co chuoi giong key;
  khi do file dich bi tu choi (an toan hon la gui nham).
- `is_sensitive_path` khop ca duong dan, nen thu muc co ten `*secret*` cung bi coi
  la nhay cam (co y bao thu).
- Topology cho phep khai bao <= 2 nhanh song song, nhung `recipe_c` van chay
  tuan tu; validator moi la phan chan cung, chua co executor song song.
- Budget dem **request**, khong dem token (token do qua `cost_report`).

## Phase 4 — Quan sat, bo nho va dong goi

Muc tieu: bien ledger tu "file log" thanh **nguon su that co the doc lai** — xem lai
run cu trong 0ms, nhin bang mat qua dashboard, va rut kinh nghiem tu dong cho lan sau.

### 4.1 — Dong bang schema v1.0.0 (`schema.py`)

9 truong chuan cho moi event:

```
run_id | timestamp | event_type | node | model | status | duration_ms
        | usage {in_tokens, out_tokens} | details
```

- `node` suy ra tu `event_type` (lead / coder / reviewer / systembot / security / qa).
- `model` lay tu `data.model` (hoac `data.models[0]`) — khong doan bua.
- `details` = `data` + `result.error` (giu nguyen payload cu).

**Tuong thich nguoc** (yeu cau bat buoc):

| Tinh huong | Hanh vi |
|---|---|
| Event cu `schema_version: 1` voi `result`/`data` | `normalize()` chuyen sang dang chuan, khong rewrite file |
| Event thieu `node`/`model` | Suy ra / de `null` |
| Event co truong la (tuong lai) | Giu trong `extra`, khong raise |
| Dong JSON bi cat giua | `read_events()` bo qua dong do |
| `validate()` | Chi bao loi that (thieu `run_id`/`timestamp`/`event_type`, `usage` sai kieu) |

```powershell
python .agents/schema.py --describe
python .agents/schema.py --ledger .agents/ledger.jsonl
```

### 4.2 — Replay CLI (`replay.py`)

```powershell
python .agents/replay.py              # run gan nhat
python .agents/replay.py <run_id>
python .agents/replay.py --list       # liet ke cac run (moi nhat truoc)
python .agents/replay.py --last --json
```

Timeline doc thang tu `ledger.jsonl`: bat dau task -> Level -> tung vong Coder /
SystemBot verify / Reviewer -> backup -> ghi -> verify -> ket luan apply/rollback.
**0 request, 0 token, khong cham vao file du an.** Mau theo vai tro, `--no-color`
khi pipe ra file.

### 4.3 — Web Dashboard (`dashboard.py`)

```powershell
python .agents/dashboard.py --port 8080 --open
# http://127.0.0.1:8080/
```

Chi dung `http.server` (khong framework, khong CDN, khong build step). Server bind
`127.0.0.1` — khong mo ra mang.

| Cot | Noi dung |
|---|---|
| 1 - Sidebar | Run history: run_id, trang thai (applied/aborted/dry-run), so event, thoi gian, task, Level, so request |
| 2 - Phong hop | Bong bong chat theo vai tro (Lead/Coder/Reviewer/SystemBot/Security), avatar mau rieng, `status` chip, model, thoi gian, `status_message` o header |
| 3 - Diff & Inspector | Diff ban goc (backup) vs ban da ghi / ban nhap; token in/out; tong thoi gian; lenh verify cua may that |

API noi bo: `GET /api/runs` (mac dinh chi run task; `?filter=all` de xem ca run
scout/acceptance), `/api/runs/<run_id>`, `/api/diff?run_id=...`, `/api/health`.
SPA poll 3 giay/lan de cap nhat gan thuc.

Nguon du lieu: `.agents/ledger.jsonl`, `.agents/backups/` (ban truoc khi ghi),
`.agents/draft/` (ban nhap cua Coder). Duong dan trong diff bi gioi han trong
`<root>/.agents`, khong doc file ngoai.

### 4.4 — Memory Engine (`memory.py`)

```powershell
python .agents/memory.py
python .agents/memory.py --json --limit 5
```

Phan tich ledger va tra loi 2 cau hoi:

1. **Loi nao hay bi may that bat nhat?** Phan loai `SyntaxError`, `IndentationError`,
   `AssertionError`, `NameError`, `TypeError`, `ModuleNotFoundError`, `AttributeError`,
   `IndexError/KeyError`, `ZeroDivisionError`, timeout. Nguon: `recipe.ladder` (guard
   cu phap), `recipe.draft_verify`, `safe_writer.verify`, `recipe.done`.
2. **Model nao hay timeout / quota?** Tu `recipe.coder` + `recipe.fallback` +
   `tmp/health.json` (cooldown dang hieu luc).

`memory.get_suggestions(limit=3)` tra ve cau ngan (vi du *"60% loi verify la syntax
(3/5) -> tu chay py_compile tren ban nhap truoc khi tra file"*). `recipe_c` tu goi
ham nay **truoc vong 1** va chen vao prompt Coder qua `coder.hints` (event
`memory.hints`). Chua du du lieu -> tra `[]`, khong bia loi khuyen.

### 4.5 & 4.6 — Skill + tai lieu

- `.agents/SKILL.md`: frontmatter chuan OpenCode Skill (`name`, `description`), runbook
  2 buoc, bang lenh, 7 bat bien, bang xu ly su co.
- `.opencode/skills/adaptive-swarm-pipeline/SKILL.md`: wrapper de OpenCode tu tim thay
  skill trong du an.
- README: kien truc 5 tang, so do luong, benchmark 4 model, quick start 2 buoc.

### Bang chung Phase 4 (may that, 2026-10-20)

| Nhom | Ket qua |
|---|---|
| `schema` | Event cu -> 9 truong; truong la khong lam gay parser; thieu `run_id` bi bat |
| `replay --last` | Exit 0, dung run gan nhat, du Lead/Coder/SystemBot, dong cuoi `KET LUAN` |
| Dashboard | `GET /` 200 + SPA; `/api/runs` JSON; `/api/runs/<id>` timeline; `/api/diff` >= 2 dong thay doi; `/api/health` ok |
| Memory | Nhan dien `syntax`, `get_suggestions()` tra loi khuyen, hints duoc chen vao Coder, ledger rong -> `[]` |
| Khong hoi quy | Phase 0 35/35, Phase 0.5 69/69, Phase 1 53/53 + 10/10 task, Phase 3 51/51 |
| **Tong** | **`test_phase4.py` 36/36 PASS** (32/32 voi `--fast`) |

Gioi han cua Phase 4:

- Dashboard poll 3 giay (khong websocket); SPA khong co routing/history.
- Diff so sanh **ban backup gan nhat** voi hien tai; nhieu vong sua chi giu ban cuoi
  (muon xem tung vong thi doc `recipe.ladder`/`recipe.draft_verify` trong chat).
- Memory la thong ke + luat regex, khong hoc may; can vai run that de co goi y.
- Schema v1.0.0 la **hop dong doc** (normalize khi doc), file ledger cu khong bi rewrite.

## Hop dong an toan (khong duoc vi pham)

| # | Quy tac |
|---|---|
| 1 | Truoc khi ghi de bat ky file nao -> **bat buoc** co backup trong `backups/` |
| 2 | Ghi **atomic**: file tam -> fsync -> kiem tra hash -> `os.replace`. Khong bao gio de file that ghi do |
| 3 | Sau khi ghi -> **verify bang may that**. That bai -> rollback ve dung 100% byte goc |
| 4 | Backup khong ton tai truoc do -> rollback se **xoa** file (tra ve dung trang thai cu) |
| 5 | SystemBot chan: lenh pha huy, lenh mang, cai dat goi, command substitution, redirection ghi file |
| 6 | Moi hanh dong deu ghi ledger. Ledger la append-only, khong sua, khong xoa |

**Gioi han da biet:** SystemBot la *denylist*, khong phai sandbox. No chay lenh qua
`shell=True` — thu muc bi chan la co y (nhieu lenh that can redirect/shell), nhung
khong the coi la cach ly an toan tuyet doi.

## Exit code cua SystemBot

| Code | Nghia |
|---|---|
| 0 | Lenh chay xong, exit code 0 |
| N | Exit code that cua process |
| 124 | Timeout — da ngat ca cay process con |
| 126 | Bi guard chan (hoac lenh khong spawn duoc) |

## Schema event (ledger v1 / chuan v1.0.0)

```json
{
  "schema_version": 1,
  "event_id": "a1b2c3d4e5f6",
  "timestamp": "2026-10-20T10:00:00.123Z",
  "run_id": "run_20261020T100000_ab12cd",
  "event_type": "system_bot.run",
  "duration_ms": 123,
  "usage": { "in_tokens": 0, "out_tokens": 0, "cache_read_tokens": 0 },
  "result": { "status": "pass", "error": null },
  "data": {}
}
```

`result.status` thuoc tap: `info | ok | pass | fail | error | blocked | timeout | skipped`.

Event type:

- Phase 0: `profile.scan`, `system_bot.run`, `system_bot.blocked`,
  `safe_writer.backup`, `safe_writer.write`, `safe_writer.verify`, `safe_writer.rollback`.
- Phase 0.5: `scout.catalog.read`, `scout.plan`, `scout.verify`,
  `phase05.acceptance.start`, `phase05.acceptance.end`.
- Phase 1: `recipe.start`, `recipe.coder`, `recipe.ladder`, `recipe.draft_verify`,
  `recipe.done`, `recipe.guard.restore`, `reviewer.findings`, `permission.denied`.
- Phase 3: `clarify.required`, `plan.validated`, `secret.blocked`, `budget.exceeded`,
  `recipe.fallback`.
- Phase 4: `memory.hints` (goi y cua Memory Engine duoc chen vao prompt Coder).

Schema v1.0.0 (Phase 4) khong doi file cu: `schema.normalize()` chuyen `result`/`data`
sang 9 truong chuan khi doc, va bo qua truong la.

**Luu y rieng tu:** ledger chi ghi `output_tail[-5:]`; 40 dong tail day du chi tra ve
cho ben goi, khong nam lai tren dia.

## Ghi chu placeholder

`profile.json` -> `verify_commands` co the chua `{file}` (vi du `python -m py_compile {file}`).
Pipeline se thay bang duong dan file vua sua de verify nhanh, thay vi chay ca test suite.

## Pham vi

**Phase 0 — da lam:** khung thu muc, nhan dien stack, SystemBot, ledger v1, backup/rollback.
**Phase 0.5 — da lam:** scout doc catalog + auth, anh xa vai tro (heuristic-v1 +
bai toan gan co rang buoc khac ho), preview diff, ghi config an toan, rang buoc
`--free-only`, micro-ping tuy chon, chuoi du phong 2 cua thoat (quota + provider),
nghiem thu 69/69 tren may that.
**Phase 1 — da lam:** classifier rules-first (Level 0/1/2/3), Work Order 5 truong +
validator tu choi khi thieu `verify_command`, phan quyen Coder (draft-only) /
Reviewer (read-only qua `FindingsSink`), format ladder 3 bac co tu ha bac va chuan
hoa khoang trang, Recipe C (2 lan verify + apply an toan), circuit breaker 2 vong,
smoke suite 10 task: **10/10 PASS, apply fail 0%, 53/53 engine check**.
**Phase 2 — da lam:** eval harness do ty le pass that cua 4 model free (40 luot,
~43 request), `AgenticCoder` + 3 lop containment, report + khuyen nghi model.
**Phase 3 — da lam:** fallback chain + health thu dong, plan validator (node type
dong, <=6 node, <=2 nhanh, verify_command bat buoc, omitted phai co reason),
session policy (2 vong giu phien, vong 3+ mo moi + brief), secret scanner
(chan file nhay cam / redact prompt, khong ghi secret vao ledger), clarify gate
(1 lan hoi), tran cung 8 request + 300 giay, cost report tu ledger; nghiem thu
**55/55 PASS** (gom 1 lan goi model that de thu fallback), smoke suite van 53/53.
**Phase 4 — da lam:** schema ledger v1.0.0 (normalize event cu, giu truong la), replay
CLI (timeline mau, 0 request), dashboard 3 cot bang `http.server`, memory engine
(phan loai loi + suc khoe model + chen goi y vao prompt Coder), SKILL.md + wrapper
`.opencode/skills/`, nghiem thu **36/36 PASS** (gom lai 4 suite Phase 0/0.5/1/3).

**Phase 5 — Level 3 (da lam):** `VALID_LEVELS=(0,1,2,3)`, Work Order da file,
`Coder.patch_set` (AgenticCoder: 1 request cho ca tap file), `Reviewer.review_set`
(doc toan bo diff, bat lech logic giua module), `safe_writer.Transaction` (backup het
-> ghi het -> verify -> commit / batch rollback) + journal + `recover_open_transactions()`,
classifier/clarify cap nhat; nghiem thu **47/47 PASS** (gom 5 suite hoi quy Phase 0/0.5/1/3/4).

**Phase 5B — router theo loai viec (da lam):** `router.py` chia W0-W8 theo tin hieu
do duoc (so file, risk keyword, context, modality, fail count) va bang dinh tuyen
main/escalate uu tien ngon-bo-re; `validate_roles()` chan trung ho o vai kiem toan;
`FallbackReviewer` cho reviewer co chuoi du phong; `quota.py` doc usage THAT tu
`opencode stats` va cat theo tran Go (5h=20%, tuan=50%, thang=100%), `recipe_c --route`
tu day escalate len khi main sap can; `recipe_c --route` ghi event `router.decided`.
Nghiem thu **57/57 PASS** (gom Level 3 + Phase 4) va **15/15** quota.

**Phase 6A — job plan + duyet (da lam):** `job.py` nhan `plan.json` (subtasks +
`depends_on` + verify), ghi plan vao `.agents/tmp/`, in URL dashboard kem token;
nguoi dung bam Duyet/Tu choi tren 127.0.0.1; chi khi approved moi chay tuan tu, moi
subtask 1 Work Order + transaction va tu route W0-W8 + quota. Timeout = tu choi.
Nghiem thu **32/32 PASS** (gom Phase 5 hoi quy).

**Chua lam (co y):** executor song song (Level 3 chay tuan tu trong 1 giao dich),
backoff luy tien cho cooldown, 5 sub-agent song song (QA/security/perf/doc), bridge
Telegram/Discord, va do model tra phi tren task that (benchmark hien chi la smoke vi
chi phi agentic cao).
