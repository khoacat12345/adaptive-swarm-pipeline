# Adaptive Swarm v3.0.0 — QA & Release Checklist

> Mục đích: kiểm tra từng chức năng của bản phát hành v3.0.0 theo từng tầng,
> bao gồm CLI, orchestration, worktree, delivery, crash recovery, UI/API,
> packaging, CI và PyPI.
>
> Đây là checklist thủ công có thể lặp lại. Không chạy các bước merge/rollback
> trên repo chính. Dùng một repo tạm.

## Thông tin lần kiểm tra

| Trường | Giá trị |
|---|---|
| Ngày kiểm tra | 2026-11-12 |
| Người kiểm tra | QA gate (OpenCode / AI) |
| Commit đang test | `2caeac25864fe05c9ab26ec46af3fa87007c51ef` |
| Tag | `v3.0.0` → `60e57f03d539df19955fa87925f9f6696f580c76` (HEAD test là commit docs/QA sau tag) |
| Hệ điều hành | Windows (win32) |
| Python | 3.12.10 |
| Git | 2.56.0.windows.2 |
| OpenCode CLI | v2.0.32 |
| Chế độ test | Fake seats E2 + automated regression; live smoke SKIP |
| Kết quả tổng | **FAIL (UI acceptance)** — xem Findings bên dưới |

---

## 0. Quy tắc trước khi test

- [ ] Repo chính sạch: `git status --short` không có thay đổi.
- [ ] Đã ghi lại commit baseline: `git rev-parse HEAD`.
- [ ] Không test merge/rollback trên repo chính.
- [ ] Dùng repo tạm cho E2E và UI có dữ liệu.
- [ ] Chỉ dùng model free; không bật paid model.
- [ ] Không publish lại version `3.0.0` lên PyPI.
- [ ] Lưu log, run ID, ảnh UI và CI URL vào phần Evidence.

### Tạo thư mục evidence

```powershell
$evidence = Join-Path $env:TEMP "swarm-v3-evidence"
New-Item -ItemType Directory -Force $evidence | Out-Null
```

---

## 1. Preflight môi trường

### 1.1 Version và entrypoint

```powershell
python --version
git --version
opencode --version
swarm --version
.\swarm.cmd --version
```

Kỳ vọng:

```text
swarm 3.0.0
swarm 3.0.0
```

- [ ] Python >= 3.11.
- [ ] Git có sẵn.
- [ ] OpenCode CLI có sẵn.
- [ ] `swarm` và `swarm.cmd` cùng báo `3.0.0`.

### 1.2 Health preflight

```powershell
swarm doctor
```

- [ ] Git/worktree OK.
- [ ] OpenCode free models được phát hiện.
- [ ] Không có model paid nào bị chọn mặc định.
- [ ] Nếu có route lỗi, ghi rõ route nào và tiếp tục với test offline.

### 1.3 Tag và release baseline

```powershell
git rev-parse HEAD
git rev-parse "v3.0.0^{commit}"
git tag --list v3.0.0
```

- [ ] Tag `v3.0.0` tồn tại.
- [ ] Tag trỏ tới commit release dự kiến.
- [ ] `v1.0.5` vẫn tồn tại làm fallback.
- [ ] Tag `v2.1.0` không bị di chuyển.

---

## 2. Packaging và cài đặt

### 2.1 Build sdist + wheel

```powershell
$buildDir = Join-Path $env:TEMP "asp-build-check"
Remove-Item $buildDir -Recurse -Force -ErrorAction SilentlyContinue
python -m build --outdir $buildDir
python -m twine check (Join-Path $buildDir "*")
```

- [ ] Sinh được `.whl`.
- [ ] Sinh được `.tar.gz`.
- [ ] `twine check` PASS cho cả hai.
- [ ] Wheel có `swarm` entrypoint.
- [ ] Wheel có `agents/unified/cli.py`.
- [ ] Wheel có `dashboard_ui.html`.
- [ ] Không có `__pycache__`, `.git`, `.env`, `config.json` trong wheel.

### 2.2 Cài wheel trong venv sạch

```powershell
$venv = Join-Path $env:TEMP "asp-install-check"
Remove-Item $venv -Recurse -Force -ErrorAction SilentlyContinue
python -m venv $venv
$py = "$venv\Scripts\python.exe"
$wheel = Get-ChildItem $buildDir -Filter *.whl | Select-Object -First 1
& $py -m pip install --no-deps $wheel.FullName
& "$venv\Scripts\swarm.exe" --version
& "$venv\Scripts\swarm.exe" --help
```

- [ ] `swarm --version` → `3.0.0`.
- [ ] `swarm --help` chạy.
- [ ] Không import lỗi module `agents.unified`.

### 2.3 Cài từ PyPI

```powershell
$pypiVenv = Join-Path $env:TEMP "asp-pypi-check"
Remove-Item $pypiVenv -Recurse -Force -ErrorAction SilentlyContinue
python -m venv $pypiVenv
& "$pypiVenv\Scripts\pip.exe" install adaptive-swarm-pipeline==3.0.0
& "$pypiVenv\Scripts\swarm.exe" --version
```

- [ ] Cài được từ PyPI.
- [ ] Entrypoint `swarm` hoạt động.
- [ ] `pip show adaptive-swarm-pipeline` đúng version `3.0.0`.

### 2.4 uvx

```powershell
uvx --from adaptive-swarm-pipeline swarm --version
```

- [ ] Output `swarm 3.0.0`.
- [ ] Ghi chú: `uvx adaptive-swarm-pipeline ...` không đúng vì package chỉ cung cấp executable `swarm`.

### 2.5 Source / editable install

```powershell
git clone https://github.com/khoacat12345/adaptive-swarm-pipeline.git
cd adaptive-swarm-pipeline
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
swarm --version
```

- [ ] Editable install thành công.
- [ ] `swarm --version` → `3.0.0`.
- [ ] `python .agents/scout.py --write --verify` chạy nếu cần tạo config local.

---

## 3. Automated tests theo từng nhóm

Chạy từ thư mục gốc repo.

### 3.1 Contract + orchestrator + CLI

```powershell
python .agents\tests\test_unified_contracts.py
python .agents\tests\test_unified_orchestrator.py
python .agents\tests\test_swarm_cli.py
python .agents\tests\test_planner_adapter.py
```

- [ ] Contracts pass.
- [ ] Orchestrator pass.
- [ ] CLI pass.
- [ ] Planner adapter pass.

### 3.2 Planner và tier policy

```powershell
python .agents\tests\test_planner.py
python .agents\tests\test_planner_adapter.py
python .agents\tests\test_v2_planner_repo_map.py
python .agents\tests\test_tiers.py
python .agents\tests\test_v2_tiers.py
```

- [ ] P1/P2/P3 planner không lỗi.
- [ ] E1/E2/E3/E4 policy đúng.
- [ ] Tier override không bypass signed plan.
- [ ] E1 yêu cầu opt-in và chặn sensitive path.

### 3.3 Worktree, execution, budget

```powershell
python .agents\tests\test_v2_m1.py
python .agents\tests\test_v2_m2.py
python .agents\tests\test_v2_m3.py
python .agents\tests\test_v2_m5.py
python .agents\tests\test_v2_p0.py
python .agents\tests\test_cost_reconciliation.py
python .agents\tests\test_preflight.py
```

- [ ] Worktree isolation.
- [ ] Budget reservation/settlement.
- [ ] Actual usage reconciliation.
- [ ] Provider/preflight behavior.
- [ ] Test integrity (`TEST_TAMPER`).
- [ ] Không ghi main trước merge.

### 3.4 Delivery, parity, crash, resume

```powershell
python .agents\tests\test_v2_delivery.py
python .agents\tests\test_v2_parity.py
python .agents\tests\test_v1_v2_parity.py
python .agents\tests\test_phase4b.py
python .agents\tests\test_phase_process_crash.py
```

- [ ] Merge chỉ commit khi verify exit 0.
- [ ] Merge dry-run không ghi main.
- [ ] Rollback restore đúng byte.
- [ ] Kill process rồi `swarm resume` chạy tiếp.
- [ ] 3 fixture parity v1/v2 pass.

### 3.5 UI/API automated

```powershell
python .agents\tests\test_phase4.py
python .agents\tests\test_unified_dashboard.py
python .agents\tests\test_v2_web_ui.py
python .agents\tests\test_meeting_ui.py
python .agents\tests\test_sse.py
```

- [ ] War Room API/read models pass.
- [ ] UI contract pass.
- [ ] E4 meeting view pass.
- [ ] SSE/reconnect pass.
- [ ] Token/redaction/diff cap tests pass.

### 3.6 Adaptive/capability suite

```powershell
python .agents\tests\test_v2_adaptive.py
python .agents\tests\test_v2_adaptive_flags.py
python .agents\tests\test_v2_aci_integration.py
python .agents\tests\test_v2_lessons_integration.py
python .agents\tests\test_v2_event_state.py
python .agents\tests\test_capability_registry.py
python .agents\tests\test_capabilities_uniform.py
python .agents\tests\test_context_guard.py
```

- [ ] Repo map/context injection.
- [ ] ACI read-only validation.
- [ ] Failure lessons.
- [ ] Event state.
- [ ] Capability registry.
- [ ] Context guard.

### 3.7 Compatibility và hạ tầng còn lại

```powershell
python .agents\tests\test_phase3.py
python .agents\tests\test_phase4.py
python .agents\tests\test_phase5.py
python .agents\tests\test_phase6.py
python .agents\tests\test_approval_policy.py
python .agents\tests\test_resilience.py
python .agents\tests\test_quota.py
python .agents\tests\test_wave.py
python .agents\tests\test_telegram.py
python .agents\tests\test_skill_consistency.py
```

- [ ] Legacy v1 paths vẫn pass như test oracle.
- [ ] Usage approval pass.
- [ ] Resilience/quota pass.
- [ ] Wave/telegram pass.
- [ ] README/skill consistency pass.

---

## 4. CLI functional checks

### 4.1 Version/help

```powershell
swarm --version
swarm --help
swarm run --help
swarm planner --help
```

- [ ] Exit code `0`.
- [ ] Help liệt kê đủ `doctor`, `planner`, `run`, `status`, `handoff`, `merge`, `resume`, `rollback`.

### 4.2 Planner only

```powershell
swarm planner --goal "Add a health endpoint" --out plan.json
```

- [ ] Sinh `plan.json`.
- [ ] Plan có target files.
- [ ] Plan có `verify_command`.
- [ ] Plan có tier/planner hợp lệ.
- [ ] Không execute, không ghi code.

### 4.3 Run dry-run

```powershell
swarm "small task" --dry-run --preset free-only
```

- [ ] Không gọi merge.
- [ ] Không ghi main.
- [ ] Status là dry-run/planned, không phải HANDOFF_READY giả.

### 4.4 Status/JSON

```powershell
swarm status --last
swarm status --last --json
```

- [ ] Có run ID.
- [ ] Có status.
- [ ] Có tier/planner.
- [ ] Có handoff path.
- [ ] JSON parse được.

### 4.5 Merge dry-run

Chỉ chạy sau khi có run thật trong repo tạm:

```powershell
swarm merge --run-id <id> --dry-run
```

- [ ] Không thay đổi git.
- [ ] Báo verify command sẽ chạy.
- [ ] Không tạo commit.

### 4.6 Merge thật

```powershell
swarm merge --run-id <id>
```

- [ ] Verify exit `0`.
- [ ] Main thay đổi đúng file.
- [ ] Có commit mới.
- [ ] Không có file ngoài phạm vi.

### 4.7 Rollback

```powershell
swarm rollback --run-id <id>
```

- [ ] Main trở về baseline.
- [ ] Không để lại worktree rác.
- [ ] Status ghi nhận rollback.

---

## 5. Worktree E2E trên repo tạm

> Không dùng repo chính cho phần này.

### 5.1 Chuẩn bị repo tạm

```powershell
$qa = Join-Path $env:TEMP "swarm-qa-v3"
Remove-Item $qa -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $qa | Out-Null
Set-Location $qa

git init
git config user.name "QA Tester"
git config user.email "qa@example.invalid"

@'
def add(a, b):
    return a + b
'@ | Set-Content calc.py

@'
from calc import add

def test_add():
    assert add(1, 2) == 3
'@ | Set-Content test_calc.py

git add .
git commit -m "baseline"
```

- [ ] Baseline commit được tạo.
- [ ] `git status` sạch.

Nếu cần model config free-only, copy config máy hiện tại vào repo tạm:

```powershell
New-Item -ItemType Directory -Force "$qa\.agents" | Out-Null
Copy-Item "<repo>\.agents\config.json" "$qa\.agents\config.json" -ErrorAction SilentlyContinue
```

- [ ] `swarm doctor --root $qa` nhận model free.
- [ ] Không copy secret/config vào Git.

### 5.2 Chạy task nhỏ

```powershell
swarm run --root $qa `
  "add a subtract function and a passing test" `
  --tier E2 `
  --preset free-only `
  --target calc.py `
  --verify "python -c \"from calc import sub; assert sub(3, 1) == 2\"" `
  --yes
```

- [ ] Run kết thúc `HANDOFF_READY`.
- [ ] Verify exit `0`.
- [ ] Có diff/handoff report.
- [ ] Main vẫn ở baseline.
- [ ] Worktree có thay đổi.
- [ ] Không có auto-merge.

### 5.3 Kiểm tra main trước merge

```powershell
git -C $qa status --short
git -C $qa log -1 --oneline
git -C $qa worktree list
```

- [ ] Main không đổi.
- [ ] Worktree tồn tại.
- [ ] Không có commit mới trên main.

### 5.4 Merge và rollback

```powershell
swarm status --root $qa --last
swarm merge --root $qa --run-id <id> --dry-run
swarm merge --root $qa --run-id <id>
```

- [ ] Dry-run không ghi main.
- [ ] Merge thật chỉ commit sau verify.
- [ ] Diff đúng file.
- [ ] Test/verify pass sau merge.

```powershell
swarm rollback --root $qa --run-id <id>
git -C $qa log -1 --oneline
git -C $qa status --short
```

- [ ] Rollback đưa main về baseline.
- [ ] Không còn thay đổi ngoài ý muốn.
- [ ] Run state ghi nhận rollback.

---

## 6. Tier behavior cần xác nhận

### E1

```powershell
# Không opt-in: phải bị từ chối
swarm "low-risk change" --tier E1 --dry-run

# Có opt-in: chỉ low-risk
swarm "low-risk change" --tier E1 --e1-opt-in --dry-run
```

- [ ] Thiếu `--e1-opt-in` → reject.
- [ ] Có opt-in nhưng chạm sensitive path → reject.
- [ ] Không tự nâng lên tier khác.

### E2

- [ ] Có security reviewer.
- [ ] Reviewer là model family khác coder nếu có thể.
- [ ] Security veto block apply.

### E3

- [ ] Có đủ 4 reviewer: security, QA, perf, docs.
- [ ] Thiếu route → fail closed.
- [ ] Bất kỳ reject → block merge.

### E4

- [ ] Blackboard Round 1 chạy.
- [ ] Có tối đa một rebuttal có mục tiêu.
- [ ] Vote được ghi vào state.
- [ ] War Room hiển thị vote.
- [ ] Thiếu reviewer → không auto-approve.

### Planner

- [ ] P1 một seat.
- [ ] P2 proposal + critique.
- [ ] P3 ba seat.
- [ ] Plan được validate trước execute.
- [ ] `--planner-model` optional.

---

## 7. War Room UI/API checklist

### 7.1 Khởi động

```powershell
python .agents\dashboard.py --root $qa --port 8501 --open
```

Hoặc:

```powershell
.\dashboard.cmd
```

- [ ] Server bind `127.0.0.1`, không phải `0.0.0.0`.
- [ ] Mở được `http://127.0.0.1:8501`.
- [ ] `--port 8080` vẫn là alias.

### 7.2 API smoke

```powershell
Invoke-RestMethod http://127.0.0.1:8501/api/health
Invoke-RestMethod http://127.0.0.1:8501/api/unified/runs
```

- [ ] Health trả 200.
- [ ] Runs trả danh sách.
- [ ] Không có lỗi 500 trong server log.

Với run cụ thể:

```text
GET /api/unified/runs/<id>
GET /api/unified/runs/<id>/events
GET /api/unified/runs/<id>/diff
```

- [ ] Detail có tier/planner/spend/verify/handoff.
- [ ] Events phân trang được.
- [ ] Diff đúng và có cap kích thước.

### 7.3 UI manual

- [ ] Timeline: Planning → Worktree → Executing → Verifying → Handoff.
- [ ] Badge tier/planner đúng.
- [ ] Spend/token/remaining budget đúng.
- [ ] Usage approval hiển thị đúng.
- [ ] E4 Blackboard Round 1/2 hiển thị.
- [ ] Handoff diff có thể xem.
- [ ] Merge chỉ enable khi đúng state.
- [ ] Rollback hiển thị đúng.
- [ ] Run history có v2 và v1 read-only.
- [ ] Refresh không mất state.
- [ ] SSE cập nhật timeline khi run đổi state.
- [ ] Browser console không có lỗi.
- [ ] Network không có request 500.
- [ ] Layout không vỡ ở 1366px và mobile width.

### 7.4 UI security

- [ ] Merge không token → bị từ chối.
- [ ] Token sai → bị từ chối.
- [ ] Token không bị hiển thị trong diff/state/log.
- [ ] Secret bị redacted.
- [ ] Diff quá lớn bị cap.
- [ ] Không truy cập được server từ máy khác nếu chỉ bind loopback.

### 7.5 UI automated

```powershell
python .agents\tests\test_phase4.py
python .agents\tests\test_unified_dashboard.py
python .agents\tests\test_v2_web_ui.py
python .agents\tests\test_meeting_ui.py
python .agents\tests\test_sse.py
```

---

## 8. Live free-only smoke

Chỉ chạy **một** task nhỏ, không chạy nhiều lần để tránh tốn quota.

Điều kiện:

- [ ] OpenCode CLI hoạt động.
- [ ] `.agents/config.json` có free models.
- [ ] `swarm doctor` xanh.
- [ ] Repo là repo tạm.

Sau khi chạy E2E ở mục 5, kiểm tra:

- [ ] Status `HANDOFF_READY`.
- [ ] Verify exit `0`.
- [ ] Main không đổi trước merge.
- [ ] Run ID được ghi lại.
- [ ] Không có paid model.
- [ ] Không có secret trong log.

Nếu không có OpenCode/model route, đánh dấu **SKIP có lý do**, dùng evidence live smoke đã có:

```text
v2_20261110T070617_56c0a0
E2 / P1
HANDOFF_READY
verify exit 0
```

---

## 9. CI, PyPI và release artifacts

### GitHub Actions

- [ ] `cross-platform` xanh trên Windows.
- [ ] `cross-platform` xanh trên Ubuntu.
- [ ] `cross-platform` xanh trên macOS.
- [ ] `publish.yml` chỉ chạy khi release/workflow_dispatch.
- [ ] Không trigger publish lại `3.0.0`.

### PyPI

```powershell
python -m pip index versions adaptive-swarm-pipeline
```

- [ ] PyPI có `3.0.0`.
- [ ] Có wheel.
- [ ] Có sdist.
- [ ] Metadata đúng entrypoint `swarm`.
- [ ] Cài từ PyPI chạy được.

### GitHub Release

- [ ] Release `v3.0.0` tồn tại.
- [ ] Title đúng.
- [ ] Có ZIP offline.
- [ ] Có file `.sha256`.
- [ ] Checksum khớp:
  ```text
  75a245312bd912d7b19ab4e6e5e592bb82d76a27c76a4387e828197e67f4d7a3
  ```

### Tag hygiene

- [ ] `v3.0.0` không bị di chuyển.
- [ ] `v1.0.5` vẫn còn.
- [ ] `v2.1.0` không bị retag.

---

## 10. Full regression gate

Chạy **sau khi đã hoàn tất manual tests**. Đảm bảo quay lại repo chính và
working tree sạch.

```powershell
$fail = @()
$files = Get-ChildItem .agents\tests\test_*.py | Sort-Object Name
foreach ($f in $files) {
    Write-Host "== $($f.Name)"
    python $f.FullName
    if ($LASTEXITCODE -ne 0) {
        $fail += $f.Name
    }
}

Write-Host "TOTAL=$($files.Count) FAILURES=$($fail.Count)"
if ($fail.Count -gt 0) {
    Write-Host "FAILED: $($fail -join ', ')"
    exit 1
}
```

Kỳ vọng:

```text
TOTAL=56 FAILURES=0
```

- [ ] 56/56 test files pass.
- [ ] Exit code `0`.
- [ ] Không có test bị skip ngoài lý do đã ghi.
- [ ] Không có thay đổi không mong muốn trong `git status`.

---

## 11. Evidence cần lưu

| Evidence | Giá trị |
|---|---|
| Commit SHA | `2caeac25864fe05c9ab26ec46af3fa87007c51ef` |
| `swarm --version` | `swarm 3.0.0` |
| Full suite result | **56/56 PASS, 0 failures, exit code 0** |
| Test log path | `C:\Users\tuank\AppData\Local\Temp\swarm-v3-evidence\regression.log` |
| Packaging logs | `build.log`, `twine.log`, `wheel-install.log`, `pypi-install.log`, `uvx.log` trong thư mục evidence |
| E2E logs | `e2e_run.log`, `e2e_status.log`, `e2e_merge_dryrun.log`, `e2e_merge.log`, `e2e_rollback.log` |
| Live smoke run ID | **SKIP** — không chạy lại; dùng evidence cũ `v2_20261110T070617_56c0a0`. Fake E2 run: `v2_20261111T171242_7ac1be` |
| War Room screenshot | `C:\Users\tuank\AppData\Local\Temp\swarm-v3-evidence\war-room-unified.png` |
| Merge run ID | `v2_20261111T171242_7ac1be` — pre-merge `684e58cc1069ca16ea983c42aefad8d21a13142e`, merge `0881a647852e8e0560d105fd43c03278204d68b8` |
| Rollback run ID | `v2_20261111T171242_7ac1be` — rollback commit `d42abd7cec6f6193274a998a7adf7a397544e76e`; tree equal baseline (`git diff --quiet 684e58c HEAD` → exit 0) |
| CI run URL | cross-platform: https://github.com/khoacat12345/adaptive-swarm-pipeline/actions/runs/43062462649 · publish: https://github.com/khoacat12345/adaptive-swarm-pipeline/actions/runs/43041207834 |
| PyPI URL | https://pypi.org/project/adaptive-swarm-pipeline/3.0.0/ |
| ZIP SHA256 | `75a245312bd912d7b19ab4e6e5e592bb82d76a27c76a4387e828197e67f4d7a3` |
| Ghi chú lỗi / skip | UI token counts bị redacted; UI verify summary rỗng; rollback là revert commit (nội dung khớp baseline nhưng HEAD khác SHA); worktree được giữ lại để làm evidence; live smoke và 8h soak SKIP theo Mục 13 |

### Giá trị thực tế thay cho placeholder

| Placeholder | Giá trị thực tế |
|---|---|
| `<id>` | `v2_20261111T171242_7ac1be` |
| `<repo>` | `C:\Users\tuank\AppData\Local\Temp\swarm-qa-temp-repo` |
| Worktree | `C:\Users\tuank\AppData\Local\AdaptiveSwarm\worktrees\v2_20261111T171242_7ac1be` |
| State root | `C:\Users\tuank\AppData\Local\Temp\swarm-qa-temp-repo\.agents\tmp\v2\runs\v2_20261111T171242_7ac1be` |
| Baseline SHA | `684e58cc1069ca16ea983c42aefad8d21a13142e` |
| Merge SHA | `0881a647852e8e0560d105fd43c03278204d68b8` |
| Rollback SHA | `d42abd7cec6f6193274a998a7adf7a397544e76e` |

### Findings

1. **UI token counts bị redacted — P1 UI data defect.**
   - `GET /api/unified/runs/<id>` trả:
     ```text
     estimated_tokens: "[REDACTED_SECRET]"
     used_tokens: "[REDACTED_SECRET]"
     remaining_tokens: "[REDACTED_SECRET]"
     ```
   - Root cause: `.agents/unified/state.py` dùng `_SECRET_KEY_HINTS` chứa
     `"token"`; `redact()` khớp mọi key có chuỗi `token`, kể cả token count.
   - Ảnh hưởng: Spend panel trong War Room không hiển thị token thật.
   - Đề xuất: chỉ redact credential keys (`signoff_token`, `usage_token`, ...),
     hoặc bỏ qua các key kết thúc bằng `_tokens`/`tokens`; thêm regression test.
2. **UI verify summary chưa lấy được verify exit 0 — P2.**
   - `GET /api/unified/runs/<id>` trả `verify.ok = null`, `last_exit = null`,
     `results = []` dù `checkpoint.recorded` có `verify_exit: 0` và
     `run.merged.verify[0].exit_code == 0`.
   - Root cause: `_verify_summary()` chỉ đọc event có chữ `verify`; bỏ qua
     `checkpoint.recorded`/`run.merged`.
   - Ảnh hưởng: War Room có thể hiển thị Verify pending sau khi verify pass.
3. **Rollback semantics — cần chốt acceptance.**
   - Nội dung main khớp baseline: `git diff --quiet baseline HEAD` → exit 0.
   - HEAD là revert commit `d42abd7...`, không phải baseline SHA `684e58c...`.
   - Nếu tiêu chí là "exact SHA reset", case này FAIL; nếu là "restore đúng byte",
     case này PASS.
4. **Worktree retention — không phải lỗi chức năng.**
   - Sau rollback, worktree vẫn tồn tại để giữ evidence.
   - Cleanup dự kiến qua `prune`; chưa xóa trong lần QA này.
5. **Live smoke và 8-hour soak — SKIP có lý do.**
   - Live smoke không chạy lại để tiết kiệm quota; dùng evidence cũ.
   - Soak dài không chạy lại vì execution core không thay đổi; evidence
     459/459 fake cycles đã có.

---

## 12. Điều kiện PASS / FAIL

### PASS khi

- [ ] Tất cả test bắt buộc pass.
- [ ] Không có lỗi P0/P1.
- [ ] Worktree/merge/rollback đúng.
- [ ] UI hiển thị đúng run thật.
- [ ] PyPI install và entrypoint hoạt động.
- [ ] CI xanh.
- [ ] Không có regression ngoài danh sách deferred đã biết.

### FAIL khi

- [ ] Có test fail không có lý do.
- [ ] Main bị thay đổi trước merge.
- [ ] Merge không verify.
- [ ] Rollback không restore.
- [ ] Resume không hoạt động.
- [ ] UI hiển thị sai state hoặc lộ secret.
- [ ] `swarm` entrypoint lỗi sau khi cài.
- [ ] CI đỏ.

---

## 13. Ghi chú về phạm vi

- Không cần chạy lại full 8-hour soak mỗi lần. Release hiện tại đã có evidence
  **459/459 fake-mode cycles**, cộng **3/3** post-Phase 4 cycles.
- Soak dài chỉ cần chạy lại khi thay đổi execution core, worktree, budget,
  planner hoặc event state.
- E3/E4 live model có thể skip nếu route không có; automated tier tests vẫn
  phải pass.
- Không test paid model trừ khi Product Owner yêu cầu rõ ràng.
- UI là dashboard stdlib `http.server`, không phải Streamlit; không cần test
  Streamlit runtime.
