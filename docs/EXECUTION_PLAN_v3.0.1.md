# EXECUTION PLAN v3.0.1 — Remaining Work

> **Trạng thái:** DRAFT — chờ chủ dự án duyệt.
> **Không có feature code nào được bắt đầu** cho đến khi từng wave được duyệt.
> **Baseline hiện tại:** commit `e7e4069`, version `3.0.1`, 57 test files,
> full suite `57/57 PASS`, dashboard single live run tại `127.0.0.1:8501`.

---

## 0. Mục tiêu của execution plan này

Đưa toàn bộ phần còn thiếu trong `plan.md` thành các wave có:

- phạm vi file rõ ràng;
- dependency rõ ràng;
- acceptance criteria kiểm chứng được;
- test command cụ thể;
- điểm dừng để chủ dự án duyệt diff trước khi merge.

Plan này **không thay đổi** các nguyên tắc cốt lõi:

- verification-first;
- explicit merge, không auto-merge;
- main tree không bị ghi trước merge;
- không lộ secret/token;
- không xoá ledger v1 hoặc lịch sử run.

---

## 1. Trạng thái thực tế sau audit

### 1.1. Đã xong

| Mục | Bằng chứng |
|---|---|
| D01 — token counts không còn bị redacted | `unified/state.py` + `test_unified_state.py` |
| D02 — verify summary đọc checkpoint/merge | `unified/state.py` + `test_unified_state.py` |
| Plan A — dashboard cũ, single live run | `dashboard.py`, `dashboard_ui.html`, `test_unified_dashboard.py` 31/31 |
| Bỏ War Room nav / run-history selector | UI không còn `data-view="unified"`; selector cũ đã ẩn |
| Full regression | `57/57` test files pass |
| Waker default model | `opencode-go/deepseek-v4.1-flash` (không phải bản free) |

### 1.2. Làm dở

| Mục | Hiện có | Còn thiếu |
|---|---|---|
| D03 — v1/v2 split-brain | Plan A đã bám một run active | Chưa có `runContext` chung; Plan/Chat/Usage vẫn còn nhánh v1; không mở lại thiết kế run bar trừ khi chủ dự án yêu cầu |
| F02 — worker lifecycle | Repair attempts trong `execution.py` | `TaskAttempt`, worker states, retry/reassign, timeout/lost/cancel, attempt history |
| F04 — agent adapter | `AgentRuntime` Protocol + `ClaudeCodeRuntimeStub` | Adapter thật cho Claude/Codex/generic/mock; registry đầy đủ; capability rejection |
| F05 — quota-aware scheduling | `.agents/quota.py` đọc quota | Chưa nối vào scheduler; chưa reserve/reassign; chưa ghi evidence |
| F11 — completion report | `handoff/report.md` + `report_text` | API `/report`, report object đầy đủ, UI Report, download Markdown/JSON |

### 1.3. Chưa làm

| Mục | Ghi chú |
|---|---|
| F01 — Task model + DAG + parallel worktrees | Không có `TaskGraph`, không có scheduler task song song |
| F03 — Decision Inbox | Không có `inbox` / `ask` / `reply` |
| F06 — Browser/visual verification | Không có Playwright/CDP |
| F07 — Diff annotations | Không có comment trên diff |
| F08 — GitHub/Linear/PR/CI | Không có `from-issue` / `review-pr` |
| F09 — Remote execution | Không có SSH/container/cloud backend |
| F10 — Scheduled automations | Không có schedule/nightly sweep |

### 1.4. Giả định cần chủ dự án xác nhận

1. **Plan A là hướng chính thức**: một dashboard bám một run active.
   Không mở lại “run bar + chọn nhiều run” của D03.
2. `plan.md` sẽ được cập nhật trạng thái sau mỗi wave, nhưng **không** dùng
   checkbox cũ làm bằng chứng.
3. Mỗi wave chỉ được merge sau khi chủ dự án duyệt diff và test evidence.

---

## 2. Nguyên tắc thực thi

### 2.1. Một wave = một đơn vị bàn giao

Mỗi wave phải có:

```text
1. Phạm vi file
2. Thay đổi hành vi
3. Test mới/cập nhật
4. Test command
5. Acceptance criteria
6. Rủi ro + rollback
```

Không gộp nhiều feature vào một commit lớn.

### 2.2. Cổng duyệt

```text
Plan approved
  → implement
  → targeted tests
  → full suite
  → Waker evidence
  → chủ dự án duyệt diff
  → commit
  → merge
```

### 2.3. Quy tắc test

- Không sửa test chỉ để pass.
- Mỗi bug fix phải có regression test.
- Mỗi feature mới phải có ít nhất:
  - unit test cho logic;
  - integration test cho API/CLI;
  - UI smoke test nếu có UI.
- Test command cho Waker:

```text
python .agents/tests/<test-file>.py
```

- Test command cho UI:

```text
python .agents/tests/test_unified_dashboard.py
```

- Full suite:

```text
python .agents/tests/test_*.py
```

### 2.4. Waker

Default model đã chỉnh thành:

```text
opencode-go/deepseek-v4.1-flash
```

Không dùng bản `free`. Mỗi wave có thể dùng Waker với:

```text
waker start --detach --repo . --tasks .waker/tasks.yaml \
  --test "python .agents/tests/<test-file>.py" \
  --adapter opencode --model opencode-go/deepseek-v4.1-flash \
  --max-kicks 5 --max-cost 1.0
```

---

## 3. Thứ tự thực thi

```text
Wave 0  Baseline + Waker + plan hygiene
Wave 1  F01 — Task model + DAG + parallel worktrees
Wave 2  F11 — Unified run completion report
Wave 3  F02 — Worker lifecycle + attempt history
Wave 4  F04 — Agent-agnostic adapter protocol
Wave 5  F05 — Quota-aware scheduling
Wave 6  F03 — Decision Inbox + ask/reply gates
Wave 7  F06 — Browser/visual verification
Wave 8  F07 — Diff annotations + feedback loop
Wave 9  F08 — GitHub/Linear/PR/CI integration
Wave 10 F09 — Remote execution backend
Wave 11 F10 — Scheduled automations
Wave 12 Release gate + docs + tag
```

Lý do đặt F11 trước F02: F11 không phụ thuộc F01, mang lại giá trị vận hành
ngay, và giúp chủ dự án nhìn được kết quả run trước khi mở các feature nặng.

---

# Wave 0 — Baseline, Waker, plan hygiene

**Mục tiêu:** chốt baseline và công cụ trước khi sửa feature.

### Việc làm

- [ ] Xác nhận baseline `e7e4069`, version `3.0.1`, `57/57` pass.
- [ ] Xác nhận Plan A là hướng chính thức.
- [ ] Xác nhận Waker default model:
  - `opencode-go/deepseek-v4.1-flash`
  - không dùng `opencode/...-free`.
- [ ] Cập nhật `plan.md` trạng thái thực tế:
  - D01/D02 done;
  - D03 superseded by Plan A;
  - F01/F03/F06–F10 not started;
  - F02/F04/F05/F11 partial.
- [ ] Ghi rõ test command chuẩn cho từng wave.

### Test

```text
python .agents/tests/test_unified_dashboard.py
python .agents/tests/test_unified_state.py
```

### Acceptance

- [ ] Full suite `57/57`.
- [ ] Waker parse đúng default model.
- [ ] Chủ dự án duyệt plan này.

---

# Wave 1 — F01: Task model + DAG + parallel worktrees

**Ưu tiên:** P0  
**Effort:** L  
**Phụ thuộc:** Wave 0

### Vấn đề hiện tại

- Không có `TaskGraph`.
- Không có task-level DAG.
- Worktree hiện là đơn vị theo run/phase, không phải theo task.
- Không có fan-out/fan-in, cycle detection, dependency blocking.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `v2/taskgraph.py` | Mới: `Task`, `TaskGraph`, validate, topo sort |
| `v2/scheduler.py` | Mới: scheduler task song song, dependency, failure propagation |
| `v2/runner.py` | Tạo run theo task graph, manifest task |
| `v2/execution.py` | Thực thi task độc lập, integration task |
| `v2/isolation.py` | Worktree riêng cho từng task |
| `v2/plan.py` | Schema task graph (backward compatible) |
| `v2/swarm_cli.py` | `run` và `status` hiển thị graph |
| `unified/state.py` | Projection graph cho UI/API |
| `dashboard_ui.html` | Active run graph panel |
| tests | `test_v2_taskgraph.py`, `test_v2_scheduler.py`, cập nhật test phase4/unified |

### Việc làm chi tiết

- [ ] Định nghĩa `Task`:
  - `id`, `title`, `spec`, `dependencies`, `status`, `attempts`,
    `worktree`, `verify_commands`, `evidence`.
- [ ] Định nghĩa `TaskGraph`:
  - validate ID;
  - validate dependency tồn tại;
  - cycle detection;
  - topological order;
  - fan-out/fan-in.
- [ ] Scheduler:
  - task không phụ thuộc chạy song song;
  - task phụ thuộc chờ đúng;
  - task fail block downstream;
  - integration task chạy sau cùng.
- [ ] Worktree:
  - mỗi task có worktree riêng dưới `%LOCALAPPDATA%\AdaptiveSwarm\worktrees\`;
  - cleanup khi task fail/cancel;
  - không ghi main.
- [ ] Verify:
  - verify từng task;
  - integration verify trước handoff.
- [ ] UI:
  - graph node status;
  - dependency;
  - worktree;
  - attempt;
  - verify;
  - evidence.
- [ ] Backward compatibility:
  - plan cũ không có graph vẫn chạy như phase tuần tự.

### Test bắt buộc

- [ ] Hai task độc lập chạy song song.
- [ ] Task B chờ A đúng dependency.
- [ ] C chỉ chạy sau A+B pass.
- [ ] Cycle bị reject.
- [ ] Missing dependency bị reject.
- [ ] Task fail block downstream.
- [ ] Mỗi task có worktree riêng.
- [ ] Main không đổi trước merge.
- [ ] Rollback trả baseline.
- [ ] Partial failure có evidence.

### Acceptance

- [ ] Tất cả test trên pass.
- [ ] Full suite pass.
- [ ] UI graph hiển thị đúng.
- [ ] Không auto-merge.

### Rủi ro

| Rủi ro | Giảm thiểu |
|---|---|
| Chạy song song làm tăng cost | Budget per task + max parallel |
| Worktree leak | Cleanup test + prune |
| Merge conflict giữa task | Integration task + conflict test |
| Phá plan cũ | Backward-compatible schema |

---

# Wave 2 — F11: Unified run completion report

**Ưu tiên:** P1  
**Effort:** M  
**Phụ thuộc:** Wave 0; không cần F01

### Vấn đề hiện tại

- Có `handoff/report.md` thô.
- Chưa có `GET /api/unified/runs/<id>/report`.
- Chưa có report object đầy đủ trong `run_detail`.
- Report tab cũ chủ yếu phục vụ v1.
- Chưa tải Markdown/JSON.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `unified/report.py` | Mới: build completion report |
| `unified/state.py` | Thêm `report` vào `run_detail` |
| `unified/api.py` | `GET /api/unified/runs/<id>/report` |
| `dashboard.py` | Route proxy nếu cần |
| `dashboard_ui.html` | Active run/Report panel |
| tests | `test_unified_report.py` |

### Nội dung report

- status cuối;
- goal;
- tier/planner;
- start/end/duration;
- run ID;
- worktree;
- request/token/cost;
- file thay đổi + diff stat;
- verify command + exit code + output tail;
- review roles/votes;
- E4 blackboard nếu có;
- merge/rollback SHA;
- warnings;
- next action.

### Việc làm chi tiết

- [ ] Tạo report schema ổn định.
- [ ] Đọc dữ liệu từ manifest/events/handoff/merge/rollback.
- [ ] Redact secret/token.
- [ ] API:
  - `GET /api/unified/runs/<id>/report`
  - trả 404 rõ nếu run không tồn tại.
- [ ] Thêm `report` vào detail để UI không phải gọi nhiều lần.
- [ ] UI:
  - `View report` trong Active run;
  - empty state: đang chạy / chưa có handoff / fail;
  - nút `Download Markdown`, `Download JSON`.
- [ ] Không auto-merge.

### Test bắt buộc

- [ ] `HANDOFF_READY` có report.
- [ ] verify exit code thật.
- [ ] merge/rollback SHA đúng.
- [ ] secret bị redacted.
- [ ] E4 có vote; E1–E3 không có panel meeting giả.
- [ ] API 200/404.
- [ ] UI marker test.

### Acceptance

- [ ] Chủ dự án mở run `HANDOFF_READY` thấy báo cáo đầy đủ.
- [ ] Tải được Markdown/JSON.
- [ ] Report không thay đổi main.
- [ ] Full suite pass.

---

# Wave 3 — F02: Worker lifecycle + attempt history

**Ưu tiên:** P1  
**Effort:** M  
**Phụ thuộc:** Wave 1

### Vấn đề hiện tại

- Chỉ có `repair_attempts` trong budget/execution.
- Không có `TaskAttempt` bền vững.
- Không có worker states đầy đủ.
- Không có attempt history trong `swarm status`/UI.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `v2/attempts.py` | Mới: `TaskAttempt`, state machine, retry policy |
| `v2/execution.py` | Ghi attempt, retry, reassign |
| `v2/runner.py` | Manifest attempt history |
| `v2/swarm_cli.py` | `status` hiển thị attempt |
| `unified/state.py` | Projection attempts |
| `dashboard_ui.html` | Attempt history panel |
| tests | `test_v2_attempts.py` |

### Worker states

```text
queued
starting
running
waiting_input
verifying
done
failed
lost
cancelled
```

### Việc làm chi tiết

- [ ] Ghi attempt number, agent/model, worktree, start/end, exit status,
      verify result, failure kind, evidence.
- [ ] Retry policy:
  - max attempts;
  - backoff;
  - reassign khi route lỗi;
  - không retry task đã pass.
- [ ] Timeout/stale/lost detection.
- [ ] `swarm status` hiển thị attempt hiện tại + history.
- [ ] UI hiển thị attempt history.

### Test bắt buộc

- [ ] Worker fail tạo attempt mới.
- [ ] Retry không mất task spec.
- [ ] Reassign hoạt động.
- [ ] Không retry task đã pass.
- [ ] Timeout/lost/cancel có state rõ.
- [ ] Attempt history còn sau restart.

### Acceptance

- [ ] Fail/retry/reassign có evidence.
- [ ] Full suite pass.

---

# Wave 4 — F04: Agent-agnostic adapter protocol

**Ưu tiên:** P1  
**Effort:** L  
**Phụ thuộc:** Wave 1, Wave 3

### Vấn đề hiện tại

- `AgentRuntime` Protocol đã có.
- `ClaudeCodeRuntimeStub` chỉ là stub.
- Adapter thật chủ yếu là OpenCode worker.
- Chưa có capability mismatch rejection.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `v2/runtime.py` | Chuẩn hoá protocol/registry |
| `v2/adapters.py` | Adapter OpenCode, Claude Code, Codex, generic, mock |
| `v2/swarm_cli.py` | Chọn adapter/runtime |
| `v2/doctor.py` | Health/capability check |
| config docs | Capability matrix |
| tests | `test_v2_adapters.py`, mock adapter test |

### Việc làm chi tiết

- [ ] Protocol:
  - `start`, `send`, `wait`, `cancel`, `capabilities`.
- [ ] Adapters:
  - OpenCode;
  - Claude Code;
  - Codex;
  - generic CLI;
  - mock.
- [ ] Registry:
  - register/get/names;
  - health check;
  - auth check.
- [ ] Capability matrix:
  - headless/tools/json/usage/write mode/streaming.
- [ ] Reject mismatch trước khi chạy.
- [ ] Không hard-code provider trong orchestrator.
- [ ] Prompt contract + evidence extraction.

### Test bắt buộc

- [ ] Mock adapter chạy được một task.
- [ ] Ít nhất hai CLI agent chạy được.
- [ ] Capability mismatch bị reject.
- [ ] Agent fail có lỗi rõ.
- [ ] Không hard-code OpenCode.

### Acceptance

- [ ] Chạy task bằng mock + 2 CLI.
- [ ] Full suite pass.

---

# Wave 5 — F05: Quota-aware scheduling

**Ưu tiên:** P1  
**Effort:** M  
**Phụ thuộc:** Wave 3, Wave 4

### Vấn đề hiện tại

- `.agents/quota.py` đọc quota thật.
- Chưa được scheduler dùng.
- Chưa có reserve/reassign.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `.agents/quota.py` | Chuẩn hoá snapshot API |
| `v2/scheduler.py` | Chọn agent theo quota |
| `v2/execution.py` | Reassign khi rate limit/quota |
| `v2/runner.py` | Ghi quota evidence |
| `unified/state.py` | Projection quota |
| `dashboard_ui.html` | Quota panel |
| tests | `test_v2_quota_scheduling.py` |

### Việc làm chi tiết

- [ ] Snapshot theo agent/model/account/window.
- [ ] Routing ưu tiên headroom.
- [ ] Reserve cho security/review.
- [ ] Reassign khi:
  - rate limit;
  - quota exhausted;
  - auth expired.
- [ ] Budget theo task/worker.
- [ ] Ghi evidence mỗi lần reassign.
- [ ] Không dùng paid model nếu policy `free-only`.

### Test bắt buộc

- [ ] Quota đọc trước dispatch.
- [ ] Agent hết quota bị loại.
- [ ] Reserve reviewer hoạt động.
- [ ] Reassign có evidence.
- [ ] Free-only không chọn paid.

### Acceptance

- [ ] Scheduler không dispatch vào agent hết quota.
- [ ] Full suite pass.

---

# Wave 6 — F03: Decision Inbox + ask/reply gates

**Ưu tiên:** P1  
**Effort:** M  
**Phụ thuộc:** Wave 1, Wave 3

### Vấn đề hiện tại

- Không có `inbox`, `ask`, `reply`.
- Không có message/gate model.
- Người dùng vẫn phải đọc chat log.

### Phạm vi file dự kiến

| File | Thay đổi |
|---|---|
| `v2/inbox.py` | Mới: message/gate model, event-driven |
| `v2/swarm_cli.py` | `inbox`, `ask`, `reply` |
| `unified/api.py` | API inbox/reply |
| `unified/state.py` | Projection inbox |
| `dashboard_ui.html` | Decision Inbox panel |
| notification | Telegram one-way trước |
| tests | `test_v2_inbox.py` |

### Message/gate types

```text
question
answer
approval
rejection
escalation
blocker
```

### Việc làm chi tiết

- [ ] Worker có thể hỏi và block chờ trả lời.
- [ ] Người trả lời qua CLI hoặc UI.
- [ ] Câu trả lời ghi vào evidence.
- [ ] Event-driven, không polling loop.
- [ ] Gate:
  - usage approval;
  - review approval;
  - merge approval;
  - destructive-action confirmation;
  - scope change.
- [ ] Approval không bypass bằng tier override.
- [ ] Badge “needs attention”.

### Test bắt buộc

- [ ] Worker hỏi → block → reply → tiếp tục.
- [ ] Answer trong evidence.
- [ ] Không polling loop.
- [ ] Gate không bypass.
- [ ] UI badge/action.

### Acceptance

- [ ] Người dùng xử lý được gate từ CLI/UI.
- [ ] Full suite pass.

---

# Wave 7 — F06: Browser/visual verification

**Ưu tiên:** P2  
**Effort:** M/L  
**Phụ thuộc:** Wave 1

### Phạm vi

- Browser adapter:
  - Edge/Chromium CDP;
  - Playwright nếu có.
- Verify hooks:
  - mở URL;
  - click selector;
  - nhập text;
  - kiểm tra text/selector;
  - chụp screenshot;
  - so sánh ảnh.
- Evidence:
  - screenshot;
  - DOM snapshot;
  - console errors;
  - network errors.
- Merge gate:
  - browser verify pass mới được merge.

### File dự kiến

- `v2/browser.py`
- `v2/execution.py`
- `unified/state.py`
- `dashboard_ui.html`
- `test_v2_browser.py`

### Acceptance

- [ ] Web task chạy browser verify.
- [ ] Screenshot lưu handoff.
- [ ] UI regression đơn giản bị phát hiện.
- [ ] Không merge nếu fail.
- [ ] Timeout/cleanup browser.

---

# Wave 8 — F07: Diff annotations + feedback loop

**Ưu tiên:** P2  
**Effort:** M  
**Phụ thuộc:** Wave 6

### Phạm vi

- UI:
  - click line;
  - comment;
  - severity;
  - `Send to coder`.
- Tạo task con:
  - `Review fix: <comment>`.
- Coder sửa → verify lại.
- Giữ history comment → fix → verify.

### File dự kiến

- `unified/api.py`
- `unified/state.py`
- `dashboard_ui.html`
- `v2/execution.py`
- `test_v2_diff_annotations.py`

### Acceptance

- [ ] Comment đúng file/line.
- [ ] Feedback tạo task.
- [ ] Fix được verify.
- [ ] Comment cũ không mất state.
- [ ] Không sửa main trước merge.

---

# Wave 9 — F08: GitHub/Linear/PR/CI integration

**Ưu tiên:** P2  
**Effort:** L  
**Phụ thuộc:** Wave 1, Wave 4

### Phạm vi

- `swarm from-issue <number>`;
- `swarm review-pr <number>`;
- tạo task DAG từ issue;
- tạo PR draft;
- nhận CI failure;
- gửi CI failure cho worker;
- gửi review comment về agent;
- link run ↔ issue/PR.

### File dự kiến

- `v2/integrations/github.py`
- `v2/integrations/linear.py`
- `v2/swarm_cli.py`
- `dashboard_ui.html`
- `test_v2_integrations.py`

### Acceptance

- [ ] Import issue thành task.
- [ ] Run ID gắn issue/PR.
- [ ] CI failure tạo feedback task.
- [ ] PR không auto-merge.
- [ ] Không lộ token.

---

# Wave 10 — F09: Remote execution backend

**Ưu tiên:** P3  
**Effort:** XL  
**Phụ thuộc:** Wave 1, Wave 4

### Phạm vi

- Execution backend interface:
  - local;
  - SSH;
  - container;
  - cloud VM.
- Remote worktree lifecycle:
  - create;
  - sync;
  - cleanup.
- Evidence sync:
  - patch;
  - verify log;
  - screenshots;
  - run manifest.
- Security:
  - SSH key isolation;
  - secret scoping;
  - no plaintext token;
  - explicit merge;
  - budget cap.

### Acceptance

- [ ] Cùng task chạy local + remote.
- [ ] Remote worktree cleanup.
- [ ] Evidence sync.
- [ ] Không auto-merge.
- [ ] Không lộ credential.

---

# Wave 11 — F10: Scheduled automations

**Ưu tiên:** P3  
**Effort:** M  
**Phụ thuộc:** Wave 10

### Phạm vi

- nightly test sweep;
- dependency audit;
- flaky test triage;
- weekly issue triage.
- Config:
  - schedule;
  - budget cap;
  - free-only;
  - target branch;
  - explicit merge gate.

### Acceptance

- [ ] Scheduled run có budget/time cap.
- [ ] Không auto-merge.
- [ ] Notification khi cần người.
- [ ] Evidence lưu.
- [ ] Disable bằng một lệnh.

---

# Wave 12 — Release gate + docs

**Mục tiêu:** đóng version sau khi các wave được duyệt.

### Việc làm

- [ ] Cập nhật `plan.md` trạng thái thực tế.
- [ ] Cập nhật `docs/QA_CHECKLIST.md` bằng evidence từng wave.
- [ ] Cập nhật README/Operator Guide nếu CLI/API đổi.
- [ ] Chạy full suite.
- [ ] Chạy E2E trên repo tạm.
- [ ] UI/API smoke.
- [ ] Xác nhận không auto-merge.
- [ ] Tag release.

### Release gate

- [ ] 57+ test files pass.
- [ ] Parity/regression pass.
- [ ] E2E repo tạm pass.
- [ ] UI/API smoke pass.
- [ ] Không auto-merge.
- [ ] Evidence ghi vào `docs/QA_CHECKLIST.md`.

---

## 4. Dependency graph

```text
Wave 0
  └─ Wave 1 (F01)
       ├─ Wave 2 (F11)
       ├─ Wave 3 (F02)
       │    ├─ Wave 4 (F04)
       │    │    ├─ Wave 5 (F05)
       │    │    ├─ Wave 9 (F08)
       │    │    └─ Wave 10 (F09)
       │    │         └─ Wave 11 (F10)
       │    └─ Wave 6 (F03)
       │         └─ Wave 8 (F07)
       └─ Wave 7 (F06)
```

---

## 5. Thay đổi dự kiến theo file (tổng hợp)

| Khu vực | File chính |
|---|---|
| Task graph / scheduler | `v2/taskgraph.py`, `v2/scheduler.py` |
| Execution | `v2/execution.py`, `v2/runner.py`, `v2/isolation.py` |
| Plan/schema | `v2/plan.py`, `v2/tasks.py` |
| Adapters | `v2/runtime.py`, `v2/adapters.py` |
| CLI | `v2/swarm_cli.py` |
| Report | `unified/report.py`, `unified/state.py`, `unified/api.py` |
| Inbox | `v2/inbox.py`, `unified/api.py` |
| Quota | `.agents/quota.py`, `v2/scheduler.py` |
| UI | `dashboard_ui.html`, `dashboard.py` |
| Browser | `v2/browser.py` |
| Integrations | `v2/integrations/*` |
| Tests | `.agents/tests/test_*.py` |
| Docs | `plan.md`, `docs/QA_CHECKLIST.md`, `docs/OPERATOR_GUIDE.md` |

---

## 6. Điều kiện dừng / rollback

Mỗi wave phải dừng ngay nếu:

- full suite fail;
- test mới fail;
- main tree bị ghi trước merge;
- secret bị lộ;
- budget vượt cap;
- worktree không cleanup;
- Waker báo `BREAKER`/`TAMPER`.

Rollback:

```text
git revert <wave-commit>
```

Không reset history của user.

---

## 7. Checklist duyệt của chủ dự án

Trước khi mình bắt tay vào Wave 1, cần chủ dự án xác nhận:

- [ ] Đồng ý thứ tự wave.
- [ ] Đồng ý Plan A là hướng chính thức.
- [ ] Đồng ý F11 làm trước F02.
- [ ] Đồng ý Waker dùng `opencode-go/deepseek-v4.1-flash`.
- [ ] Đồng ý mỗi wave phải dừng để duyệt diff.
- [ ] Đồng ý phạm vi file dự kiến (có thể chỉnh trước khi làm).

Sau khi duyệt, mình chỉ chờ lệnh kiểu:

```text
làm Wave 1
```

hoặc:

```text
sửa plan: ...
```

---

## 8. Ghi chú về Waker

Đã chỉnh default model trong package Waker:

```text
C:\Users\tuank\AppData\Local\Programs\Python\Python312\Lib\site-packages\waker\cli.py
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
```

Đã xác nhận parser:

```text
waker run  --tasks x  → model = opencode-go/deepseek-v4.1-flash
waker plan --prompt x → model = opencode-go/deepseek-v4.1-flash
```

Lưu ý: nếu cài lại/gia cố Waker, cần kiểm tra lại default model.
