# Adaptive Swarm v3+ — Product & Integration Roadmap

> Mục tiêu: học các ý tưởng orchestration tốt từ Orca (`stablyai/orca`) nhưng
> giữ vững lợi thế cạnh tranh của Adaptive Swarm:
> **verification-first, anti-tamper, explicit merge và release evidence.**
>
> Không biến sản phẩm thành bản sao Orca. Mục tiêu là:
>
> **Orca-like orchestration + Adaptive Swarm quality gate.**

## Trạng thái hiện tại

- Bản phát hành: `v3.0.0`
- Runtime hiện có:
  - một CLI `swarm`;
  - Unified Orchestrator;
  - Git worktree isolation;
  - E1–E4 execution tiers;
  - Planner P1–P3;
  - War Room `127.0.0.1:8501`;
  - Usage approval;
  - explicit merge/rollback;
  - crash/resume;
  - anti-tamper test guard;
  - 56 test files.
- QA gần nhất phát hiện 2 lỗi state projection cần đóng trước khi thêm tính năng lớn:
  - token counts bị redacted;
  - verify summary rỗng sau khi verify pass.

---

## 1. Cách ưu tiên

| Mức | Ý nghĩa | Khi nào làm |
|---|---|---|
| **P0** | Blocker hoặc nền tảng bắt buộc cho bước tiếp theo | Ngay lập tức |
| **P1** | Giá trị cao, tạo khác biệt cốt lõi | Release kế tiếp |
| **P2** | Mở rộng sản phẩm, tăng adoption | Sau khi P1 ổn định |
| **P3** | Platform/remote/automation | Dài hạn |

Mỗi feature chỉ được coi là xong khi có:

- unit/integration test;
- CLI/API/UI tương ứng;
- tài liệu;
- evidence;
- không auto-merge;
- rollback hoặc recovery path;
- cập nhật `docs/QA_CHECKLIST.md`.

---

## 2. Danh sách 10 chức năng

| ID | Chức năng | Ưu tiên | Giá trị | Effort | Phụ thuộc |
|---|---|---|---|---|---|
| F01 | QA state projection correctness | **P0** | Rất cao | S | — |
| F02 | Task model + DAG + parallel worktrees | **P0** | Rất cao | L | F01 |
| F03 | Worker lifecycle + attempt history | **P1** | Cao | M | F02 |
| F04 | Decision Inbox + ask/reply gates | **P1** | Cao | M | F02, F03 |
| F05 | Agent-agnostic adapter protocol | **P1** | Rất cao | L | F02 |
| F06 | Quota-aware scheduling | **P1** | Cao | M | F03, F05 |
| F07 | Browser/visual verification | **P2** | Rất cao cho web | M/L | F02 |
| F08 | Diff annotations + feedback loop | **P2** | Cao | M | F04 |
| F09 | GitHub/Linear/PR/CI integration | **P2** | Cao | L | F05, F02 |
| F10 | Remote execution + scheduled automations | **P3** | Cao | XL | F05, F06 |

---

# P0 — Bắt buộc trước khi mở rộng

## F01 — QA state projection correctness

### Vấn đề

War Room và API đang trả sai một số state:

- `estimated_tokens`, `used_tokens`, `remaining_tokens` bị
  `[REDACTED_SECRET]`;
- `verify.ok = null` dù `checkpoint.recorded.verify_exit = 0`;
- `/api/health` đếm v1 ledger thay vì unified runs.

### Phạm vi

- Sửa `_SECRET_KEY_HINTS` / `redact()` để chỉ che credential thật, không che
  token count.
- Mở rộng `_verify_summary()` để đọc:
  - `checkpoint.recorded.verify_exit`;
  - `run.merged.verify[].exit_code`;
  - event `verify.*`.
- Làm rõ `health.runs` là v1 hay unified, hoặc trả cả hai.
- Thêm regression test cho spend/verify projection.

### Acceptance

- [ ] Spend panel hiển thị số token thật.
- [ ] Verify panel hiển thị exit `0` sau checkpoint/merge.
- [ ] Không lộ token/secret thật.
- [ ] 56/56 test files pass.
- [ ] UI QA gate pass.

### Release

`v3.0.1` hotfix.

---

## F02 — Task model + DAG + parallel worktrees

### Ý tưởng học từ Orca

Orca dùng mô hình:

```text
Run → Task → Worker
```

và task DAG. Đây là điểm mạnh nhất nên học.

### Mục tiêu

Biến pipeline tuần tự thành pipeline có nhiều task độc lập chạy song song,
mỗi task có worktree và verify riêng.

### Phạm vi

- Thêm `Task` model:
  ```text
  id
  title
  spec
  dependencies
  status
  attempts
  worktree
  verify_commands
  evidence
  ```
- Thêm `TaskGraph`:
  - DAG validation;
  - topological order;
  - fan-out/fan-in;
  - cycle detection.
- Scheduler:
  - task không phụ thuộc → chạy song song;
  - task phụ thuộc → chờ;
  - task fail → block downstream;
  - integration task để merge kết quả.
- Mỗi task chạy trong worktree riêng dưới:
  ```text
  %LOCALAPPDATA%\AdaptiveSwarm\worktrees\
  ```
- Main vẫn không đổi cho đến explicit merge cuối cùng.
- War Room hiển thị graph:
  - node status;
  - dependency;
  - worktree;
  - attempt;
  - verify;
  - evidence.

### Acceptance

- [ ] Hai task độc lập chạy song song.
- [ ] Task B chờ Task A đúng dependency.
- [ ] Task C chỉ chạy sau khi A + B pass.
- [ ] Mỗi task có worktree riêng.
- [ ] Verify chạy trên từng task.
- [ ] Integration verify chạy trước handoff.
- [ ] Main không đổi trước merge.
- [ ] Rollback trả toàn bộ về baseline.
- [ ] Có test cho cycle, missing dependency, conflict và partial failure.

### Ghi chú thiết kế

Không dùng một worktree cho tất cả task. Worktree là **đơn vị cô lập**, không
chỉ là nơi chứa code.

### Release

`v3.1.0`.

---

# P1 — Orchestration cốt lõi

## F03 — Worker lifecycle + attempt history

### Mục tiêu

Task phải sống lâu hơn một lần agent chạy. Agent fail thì retry hoặc thay
agent, nhưng task và evidence vẫn còn.

### Phạm vi

- Worker states:
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
- `TaskAttempt`:
  - attempt number;
  - agent/model;
  - worktree;
  - start/end;
  - exit status;
  - verify result;
  - failure kind;
  - evidence.
- Retry policy:
  - max attempts;
  - backoff;
  - reassign khi route lỗi;
  - không retry vô hạn.
- Worker timeout và stale detection.
- `swarm status` hiển thị attempt hiện tại và lịch sử.

### Acceptance

- [ ] Worker fail được ghi thành attempt mới.
- [ ] Retry không mất task spec.
- [ ] Reassign sang agent khác hoạt động.
- [ ] Không retry task đã pass.
- [ ] Timeout/lost/cancel có state rõ ràng.
- [ ] War Room hiển thị attempt history.

**Effort:** M  
**Release:** `v3.1.x`

---

## F04 — Decision Inbox + ask/reply gates

### Mục tiêu

Thêm kênh hỏi–đáp có cấu trúc giữa người và worker, thay vì bắt người đọc
chat log hoặc agent tự quyết.

### Phạm vi

- Message model:
  ```text
  question
  answer
  approval
  rejection
  escalation
  blocker
  ```
- CLI:
  ```text
  swarm inbox
  swarm ask <run-id> "..."
  swarm reply <message-id> "..."
  ```
- War Room:
  - Decision Inbox;
  - badge “needs attention”;
  - approve/reject;
  - reply;
  - link tới task/diff.
- Notification:
  - Telegram one-way trước;
  - sau đó push/mobile.
- Gate types:
  - Usage approval;
  - review approval;
  - merge approval;
  - destructive-action confirmation;
  - scope change.

### Acceptance

- [ ] Worker có thể hỏi và block chờ trả lời.
- [ ] Người trả lời qua CLI hoặc War Room.
- [ ] Câu trả lời được ghi vào evidence.
- [ ] Không có polling loop; event-driven.
- [ ] Approval không thể bypass bằng tier override.

**Effort:** M  
**Release:** `v3.2.0`

---

## F05 — Agent-agnostic adapter protocol

### Mục tiêu

Không khóa sản phẩm vào OpenCode. Có thể chạy bất kỳ CLI agent nào.

### Phạm vi

Protocol:

```python
class AgentAdapter(Protocol):
    def start(self, task, worktree, model=None) -> Dispatch: ...
    def send(self, dispatch_id, message) -> None: ...
    def wait(self, dispatch_id) -> WorkerResult: ...
    def cancel(self, dispatch_id) -> None: ...
    def capabilities(self) -> AgentCapabilities: ...
```

Adapters ban đầu:

- OpenCode;
- Claude Code;
- Codex;
- generic CLI;
- mock adapter.

Cần có:

- agent registry;
- capability matrix;
- auth/health check;
- model selection;
- process/PTY lifecycle;
- prompt contract;
- evidence extraction.

### Acceptance

- [ ] Một task chạy được bằng mock adapter.
- [ ] Một task chạy được bằng ít nhất hai CLI agent.
- [ ] Capability mismatch bị reject trước khi chạy.
- [ ] Không hard-code provider trong orchestrator.
- [ ] Agent fail có lỗi rõ ràng.

**Effort:** L  
**Release:** `v3.2.0`

---

## F06 — Quota-aware scheduling

### Mục tiêu

Phân việc theo quota thật của từng agent, tránh một model cạn quota hoặc
chiếm quá nhiều việc.

### Phạm vi

- Quota snapshot theo:
  - agent;
  - model;
  - account;
  - window 5h/ngày/tuần.
- Routing:
  - ưu tiên agent còn headroom;
  - giữ reserve cho security/review;
  - không để một agent vượt ngưỡng cấu hình.
- Reassign khi:
  - rate limit;
  - quota exhausted;
  - auth expired.
- Budget theo task/worker.
- Hiển thị quota trong War Room.

### Acceptance

- [ ] Quota được đọc trước khi dispatch.
- [ ] Agent hết quota bị loại khỏi lựa chọn.
- [ ] Có reserve cho reviewer.
- [ ] Reassign được ghi evidence.
- [ ] Không có paid model nếu free-only.

**Effort:** M  
**Release:** `v3.2.x`

---

# P2 — Mở rộng sản phẩm

## F07 — Browser/visual verification

### Mục tiêu

Verify không chỉ bằng unit test mà còn bằng hành vi UI thật.

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
  - so sánh ảnh trước/sau.
- Lưu evidence:
  - screenshot;
  - DOM snapshot;
  - console errors;
  - network errors.
- `swarm merge` chỉ pass khi browser verify pass.

### Acceptance

- [ ] Task web có thể chạy browser verify.
- [ ] Screenshot được lưu vào handoff.
- [ ] UI regression đơn giản bị phát hiện.
- [ ] Không merge nếu browser verify fail.
- [ ] Có timeout và cleanup browser.

**Effort:** M/L  
**Release:** `v3.3.0`

---

## F08 — Diff annotations + feedback loop

### Mục tiêu

Người review có thể note trực tiếp trên diff và gửi feedback lại coder.

### Phạm vi

- War Room:
  - click line;
  - comment;
  - severity;
  - `Send to coder`.
- Tạo task con:
  ```text
  Review fix: <comment>
  ```
- Coder sửa;
- verify lại;
- giữ history comment → fix → verify.

### Acceptance

- [ ] Comment gắn đúng file/line.
- [ ] Gửi feedback tạo task mới.
- [ ] Fix được verify.
- [ ] Comment cũ không làm mất state.
- [ ] Không sửa main trước merge.

**Effort:** M  
**Release:** `v3.3.x`

---

## F09 — GitHub/Linear/PR/CI integration

### Mục tiêu

Đưa task từ issue/PR vào pipeline và trả kết quả về GitHub/Linear.

### Phạm vi

- `swarm from-issue <number>`;
- `swarm review-pr <number>`;
- tạo task DAG từ issue;
- tạo PR draft;
- nhận CI failure;
- gửi CI failure cho worker sở hữu task;
- gửi review comment về agent;
- link run ↔ issue/PR.

### Acceptance

- [ ] Import issue thành task.
- [ ] Run ID được gắn vào issue/PR.
- [ ] CI failure tạo feedback task.
- [ ] PR không auto-merge.
- [ ] Không lộ token trong comment/log.

**Effort:** L  
**Release:** `v3.4.0`

---

# P3 — Platform

## F10 — Remote execution + scheduled automations

### Mục tiêu

Mở rộng khỏi máy local và cho phép chạy theo lịch.

### Phạm vi

Execution backend:

- local;
- SSH;
- container;
- cloud VM.

Automation:

- nightly test sweep;
- dependency audit;
- flaky test triage;
- weekly issue triage.

Bảo mật:

- SSH key isolation;
- secret scoping;
- no plaintext token in logs;
- explicit merge only;
- budget cap;
- remote cleanup.

### Acceptance

- [ ] Cùng một task chạy được local và remote.
- [ ] Worktree remote được cleanup.
- [ ] Scheduled run có budget/time cap.
- [ ] Không auto-merge.
- [ ] Evidence được sync về local.

**Effort:** XL  
**Release:** `v4.0` hoặc sau v3.4

---

## 3. Thứ tự thực hiện đề xuất

```text
P0: F01 QA correctness
P0: F02 Task model + DAG + parallel worktrees
P1: F03 Worker lifecycle
P1: F04 Decision Inbox
P1: F05 Agent adapter
P1: F06 Quota-aware scheduling
P2: F07 Browser verification
P2: F08 Diff annotations
P2: F09 GitHub/Linear/PR/CI
P3: F10 Remote + automations
```

Lý do:

- F01 phải xong trước vì mọi feature mới đều dựa trên state projection.
- F02 tạo nền tảng cho parallel workers.
- F03/F04 làm cho worker có lifecycle và human gates.
- F05/F06 mở rộng agent và quota.
- F07/F08 cải thiện chất lượng review/UI.
- F09 mở rộng workflow.
- F10 là platform dài hạn.

---

## 4. Những gì không nên copy từ Orca

- ❌ Desktop IDE đầy đủ — War Room chỉ nên là control plane.
- ❌ Mobile app ngay — chờ remote runner và notification.
- ❌ Plugin/theme marketplace — không phải core.
- ❌ “Any agent” không có adapter protocol — sẽ thành if/else.
- ❌ Auto-merge/PR bot — giữ explicit merge gate.

---

## 5. Tiêu chí thành công

### Kỹ thuật

- [ ] Task DAG chạy song song thật.
- [ ] Mỗi task có worktree/verify/evidence riêng.
- [ ] Retry/reassign không mất task.
- [ ] Decision Inbox hoạt động.
- [ ] Agent adapter không phụ thuộc OpenCode.
- [ ] Browser verify là một phần của merge gate.
- [ ] GitHub/Linear không tạo auto-merge.

### Sản phẩm

- [ ] Giảm thời gian hoàn thành task lớn.
- [ ] Tăng tỷ lệ handoff pass verify.
- [ ] Giảm số lần người phải đọc chat log.
- [ ] Tăng số task chạy song song an toàn.
- [ ] Giữ nguyên tỷ lệ 0 lần main bị sửa trước merge.

### Release gate

Mỗi release phải có:

- [ ] 56+ test files pass.
- [ ] Parity/regression pass.
- [ ] E2E trên repo tạm.
- [ ] UI/API smoke.
- [ ] Không auto-merge.
- [ ] Evidence được ghi vào `docs/QA_CHECKLIST.md`.
