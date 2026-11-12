# Adaptive Swarm v3+ — Feature Roadmap & Debug Backlog

> Định hướng: học các ý tưởng orchestration tốt từ Orca (`stablyai/orca`)
> nhưng giữ vững lợi thế của Adaptive Swarm:
> **verification-first, anti-tamper, explicit merge và release evidence.**
>
> Mục tiêu cuối cùng:
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

## Quy ước

- **Fxx** = feature mới, nằm trong roadmap sản phẩm.
- **Dxx** = bug/debug item, nằm trong backlog riêng, **không tính là feature**.
- Khi nói **“làm P0”**, chỉ làm **F01 — Task model + DAG**.
- Khi nói **“fix debug”** hoặc **“QA fix”**, mới làm D01/D02.
- Không gộp bugfix vào P0 feature, tránh việc “làm P0” bị kéo theo sửa lỗi.

## Ưu tiên

| Mức | Ý nghĩa | Phạm vi |
|---|---|---|
| **P0** | Nền tảng bắt buộc cho orchestration | F01 |
| **P1** | Giá trị cao, tạo khác biệt cốt lõi | F02–F05, F11 |
| **P2** | Mở rộng sản phẩm, tăng adoption | F06–F08 |
| **P3** | Platform/remote/automation | F09–F10 |

---

# Debug Backlog — không phải feature

> Đây là các lỗi QA đã phát hiện. Làm riêng, không nằm trong P0.
> Không đưa vào estimate của F01 hoặc bất kỳ feature nào.

## D01 — Token counts bị redacted trong War Room

**Trạng thái:** confirmed
**Mức độ:** P1 UI data bug
**Target:** `v3.0.1` hotfix

### Hiện tượng

`GET /api/unified/runs/<id>` trả:

```json
{
  "estimated_tokens": "[REDACTED_SECRET]",
  "used_tokens": "[REDACTED_SECRET]",
  "remaining_tokens": "[REDACTED_SECRET]"
}
```

### Root cause

`.agents/unified/state.py` dùng:

```python
_SECRET_KEY_HINTS = (
    "token",
    ...
)
```

`redact()` coi mọi key chứa `"token"` là credential, kể cả token count.

### Phạm vi fix

- Chỉ redact credential keys:
  - `signoff_token`
  - `usage_token`
  - `access_token`
  - `refresh_token`
  - `api_key`
  - `authorization`
  - `password`
  - `secret`
- Hoặc bỏ qua các key kết thúc bằng `_tokens` / `tokens`.
- Thêm regression test cho spend projection.

### Acceptance

- [ ] `estimated_tokens`, `used_tokens`, `remaining_tokens` là số.
- [ ] Token/secret thật vẫn bị redacted.
- [ ] War Room Spend panel hiển thị đúng.
- [ ] 56/56 test files pass.

---

## D02 — Verify summary rỗng sau khi verify pass

**Trạng thái:** confirmed
**Mức độ:** P2 UI/state projection
**Target:** `v3.0.1` hotfix

### Hiện tượng

`GET /api/unified/runs/<id>` trả:

```json
{
  "verify": {
    "commands": ["python check.py"],
    "ok": null,
    "last_exit": null,
    "results": []
  }
}
```

Trong khi event thật có:

```text
checkpoint.recorded.verify_exit = 0
run.merged.verify[0].exit_code = 0
```

### Root cause

`_verify_summary()` chỉ đọc event có chữ `"verify"` trong `event_type`.
Nó bỏ qua `checkpoint.recorded` và `run.merged`.

### Phạm vi fix

- Đọc thêm `checkpoint.recorded.verify_exit`.
- Đọc thêm `run.merged.verify`.
- Giữ ưu tiên event `verify.*` nếu có.
- Thêm regression test cho checkpoint/merge verify.

### Acceptance

- [ ] `verify.ok` là `true` sau verify exit 0.
- [ ] `verify.last_exit` là `0`.
- [ ] `results` có evidence tương ứng.
- [ ] War Room không còn hiển thị Verify pending sai.
- [ ] 56/56 test files pass.

---

## D03 ? Legacy v1 Plan/War Room split-brain

**Tr?ng th?i:** partially fixed in UI patch; full redesign pending
**M?c ??:** P1 UI/state synchronization
**Target:** v3.0.1 UI patch

### Hi?n t??ng

Sau khi ch?y m?t task v2 m?i, tab **Plan** v?n hi?n th? k? ho?ch Kaggle c?
(`J1`) v? tr?ng th?i `STOPPED`, trong khi War Room c? c?c run v2 m?i ?ang ch?y.
War Room c? th? ch?n m?t entry v1 `job_J1` v? hi?n th? `UNKNOWN` + legacy
read-only.

### Root cause

C? hai ngu?n state ??c l?p:

- **v1 Plan page**: ??c `/api/job` v? `/api/plan` t?
  `.agents/tmp/job_state.json`, `plan.json`, `control.json`;
- **v2 War Room**: ??c `/api/unified/runs` t?
  `.agents/tmp/v2/runs/<run_id>`.

`dashboard_ui.html::refreshAll()` ch? l?y `/api/job` + `/api/plan` cho Plan tab;
n? kh?ng ??c plan c?a v2 run. V? v?y:

- J1 c? v?n n?m trong `.agents/tmp/plan.json`;
- v2 run m?i ch? xu?t hi?n trong War Room;
- Plan tab v? War Room kh?ng chia s? selected run;
- v1 `job_J1` kh?ng c? completion event n?n adapter hi?n th? `UNKNOWN`.

??y l? **split-brain UI**, kh?ng ph?i run treo. Process v2 `v2_20261112T161443`
v?n ?ang ch?y v? gi? `run.lock`.

### H??ng fix

- Th?m m?t `selectedRunId` d?ng chung cho Plan / War Room / Report / Chat.
- N?u selected run l? v2, Plan tab render `detail.plan`, `detail.execution`,
  `detail.verify`, `detail.handoff` t? unified API.
- Ch? d?ng `/api/job` + `/api/plan` khi selected run l? v1 legacy.
- M?c ??nh Plan tab ch?n run v2 m?i nh?t ?ang active; v1 history n?m trong
  filter ?Legacy v1?.
- Kh?ng auto-select `job_J1`/b?n ghi v1 `UNKNOWN` khi c? v2 run.
- Khi v2 run b?t ??u, archive/clear `job_state`/`plan.json` c? ho?c ??nh d?u
  r? l? legacy.
- Th?m regression test cho mixed v1/v2 state v? default selection.

### Acceptance

- [ ] Plan tab hi?n th? ??ng run v2 ?ang ch?n.
- [ ] J1 c? kh?ng c?n xu?t hi?n nh? plan hi?n t?i.
- [ ] War Room kh?ng auto-select v1 `UNKNOWN` khi c? v2 run.
- [ ] Selected run ??ng b? gi?a Plan / War Room / Report.
- [ ] Run v2 active hi?n th? ??ng plan v? tr?ng th?i.
- [ ] C? test cho split-brain.

---

## UI Synchronization Redesign ? selected run context

> M?c ti?u c?a ph?n n?y l? s?a ??ng ki?n tr?c UI, kh?ng ch? v? ri?ng tab Plan.
> ??y l? thi?t k? b?t bu?c tr??c khi th?m F11 Completion Report.

### V?n ?? hi?n t?i

- **Overview** v?n ??c state v1 (`/api/job` + `/api/plan`) v? ?ang hi?n th?
  Kaggle `J1` d? v2 run m?i ?ang ch?y.
- **Plan** ch? m?i ???c v? ?? ch?n v2 active; ch?a d?ng chung selected run.
- **Chat** v?n d?ng `/api/runs` c?a v1; kh?ng hi?n th? ??ng chat c?a v2 run.
- **War Room** ?ang v?a l? run history, v?a l? run detail, v?a l? blackboard.
  M?c ??ch ??ng c?a War Room l? **n?i xem c?c agent tranh lu?n/meeting**, kh?ng
  ph?i danh s?ch previous runs.
- `state.selected` (v1) v? `uwState.selected` (v2) l? hai ngu?n ??c l?p.
- `refreshAll()` t? ch?n run v1; `uwLoadRuns()` t? ch?n run ??u ti?n.
  Khi c? 2?3 run ch?y song song, selection c? th? nh?y ho?c hi?n th? sai run.
- v1 `job_J1` v?n t?n t?i trong ledger; n?u kh?ng ch?n r?, UI d? hi?n th? n?
  nh? plan hi?n t?i.

### UX m?c ti?u

```text
Overview = previous runs + selected-run summary
Plan     = plan c?a selected run
Chat     = AI messages/events c?a selected run
War Room = blackboard/debate c?a selected run (ch? y?u E4)
Report   = report c?a selected run (F11)
```

#### Quy t?c m?c ??nh

- Khi m? app: **kh?ng ch?n run n?o**.
- Overview hi?n th? empty state:
  ```text
  No run selected. Choose a run below.
  ```
- Kh?ng t? l?y J1 ho?c run m?i nh?t.
- Kh?ng hi?n th? plan/chat c?a run kh?c.
- N?u c? run m?i, ch? hi?n badge ?new run?, kh?ng c??p selection.

#### Thanh ch?n run tr?n Overview

Th?m m?t horizontal run bar ? ??u Overview:

```text
[? A: 1+1=2       running  v2 E1] [ B: 5+7=?    AWAITING  v2 E2] [ J1 legacy  stopped v1 ]
```

M?i item hi?n th?:

- run ID ng?n;
- goal;
- status;
- source v1/v2;
- tier;
- th?i gian;
- badge ?selected?;
- badge ?live? n?u ?ang ch?y.

Click v?o item:

- set `selectedRunId`;
- render l?i Overview / Plan / Chat / War Room / Report;
- update URL query `?run=<id>` ?? deep link;
- selected run ???c gi? khi refresh n?u v?n t?n t?i.

#### Nhi?u run ch?y song song

- Overview hi?n th? t?t c? active runs.
- M?i run c? selection ??c l?p.
- Plan/Chat/War Room/Report ch? ??c selected run.
- N?u selected run b? x?a/kh?ng c?n state:
  - clear selection;
  - quay v? empty state;
  - kh?ng t? nh?y sang run kh?c.

### Ki?n tr?c state m?i

T?o m?t run context duy nh?t trong frontend:

```js
const runContext = {
  runs: [],              // v2 + v1 history
  selectedRunId: null,
  selectedSource: null,  // 'v2' | 'v1'
  detail: null,
  events: [],
  loading: false
};
```

C?c h?m b?t bu?c:

```text
loadRuns()                 // kh?ng auto-select
selectRun(runId, source)   // set + load detail + events
clearSelection()
refreshSelected()          // ch? refresh selected run
```

B? c?c auto-select hi?n t?i:

- `refreshAll()` kh?ng c?n ch?n `first.run_id`;
- `uwLoadRuns()` kh?ng c?n t? ch?n run ??u ti?n;
- `state.selected` v? `uwState.selected` ???c thay b?ng m?t context.

### Tr?ch nhi?m t?ng tab

| Tab | Ngu?n d? li?u | H?nh vi |
|---|---|---|
| **Overview** | `runContext.runs` + `detail` | Previous runs + selected summary; default empty |
| **Plan** | `detail.plan` | Plan ??ng selected run; v1 fallback n?u source v1 |
| **Chat** | `detail.events` | AI messages/events c?a selected run; v1 adapter cho legacy |
| **War Room** | `detail.blackboard_rounds`, meeting events | Ch? debate/meeting c?a selected run; non-E4 hi?n empty state |
| **Report** | `detail.report` (F11) | Completion report c?a selected run |

### Chi ti?t t?ng view

#### Overview

- Empty state khi ch?a ch?n:
  - ?Select a run to inspect plan, chat, verification and report.?
  - Hi?n th? run selector.
- Khi ch?n v2:
  - goal/status/tier/planner;
  - plan snapshot;
  - verify result;
  - timeline;
  - spend/usage;
  - n?t `Open Plan`, `Open Chat`, `Open War Room` (ch? E4), `Open Report`.
- Khi ch?n v1:
  - hi?n th? legacy v1 summary;
  - banner `Legacy v1 (read-only)`;
  - Plan/Chat d?ng v1 data.
- Kh?ng c?n hi?n th? J1 m?c ??nh.

#### Plan

- `Plan` ch? render `runContext.detail.plan`.
- N?u selected run l? v1:
  - d?ng `/api/plan` legacy;
  - hi?n th? banner legacy.
- N?u ch?a ch?n:
  - empty state.
- Kh?ng auto ch?n v2 active n?u user ch?a ch?n.
- N?t `Show legacy v1 plan` ch? hi?n khi selected source v1, ho?c trong debug panel.

#### Chat

- V?i v2:
  - fetch `/api/unified/runs/<id>/events`;
  - render per-run events;
  - filter: all / lead / agents / system;
  - E4 blackboard messages c? th? hi?n th? trong Chat v? War Room.
- V?i v1:
  - d?ng `/api/runs/<id>` legacy.
- Kh?ng tr?n event c?a run A v? run B.
- Run selector ? Chat c?ng ph?i d?ng chung `runContext`.

#### War Room

- Kh?ng d?ng War Room l?m run history.
- Khi selected run l? E4:
  - hi?n th? Blackboard Round 1/2;
  - votes, rebuttal, evidence;
  - meeting stats.
- Khi selected run l? E1?E3:
  - empty state:
    ```text
    This run has no E4 meeting. Use Overview to inspect the run.
    ```
- Kh?ng auto-select v1/J1.
- Kh?ng hi?n th? run list/history ? ??y; history n?m ? Overview.
- N?u v?n mu?n gi? t?n ?War Room?, gi? nguy?n nav; ch? ??i n?i dung.

#### Report

- F11 d?ng selected run; kh?ng d?ng J1.
- Ch?a c? report:
  - hi?n th? tr?ng th?i v? verify evidence hi?n c?;
  - kh?ng gi? v? ?? c? k?t qu?.

### API / backend

Kh?ng c?n thay engine. Ch? c?n ??m b?o:

```text
GET /api/unified/runs?include_v1=1
GET /api/unified/runs/<id>
GET /api/unified/runs/<id>/events
GET /api/unified/runs/<id>/diff
GET /api/unified/runs/<id>/report   # F11
GET /api/plan                       # v1 legacy only
GET /api/job                        # v1 legacy only
```

C? th? th?m:

```text
GET /api/ui/context?run=<id>
```

nh?ng kh?ng b?t bu?c; frontend c? th? gh?p t? c?c API hi?n c?.

### Work breakdown

#### P0 ? Selection foundation

- [ ] T?o `runContext` duy nh?t.
- [ ] B? auto-select kh?i `refreshAll()`.
- [ ] B? auto-select kh?i `uwLoadRuns()`.
- [ ] Overview run selector bar.
- [ ] Default empty state.
- [ ] Selected run URL `?run=<id>`.
- [ ] Plan bind selected run.
- [ ] Chat bind selected run.
- [ ] War Room ch? hi?n th? selected run blackboard.
- [ ] Legacy v1 ch? hi?n khi explicit select.

#### P1 ? Per-run chat & report

- [ ] Chat v2 per-run events.
- [ ] E4 blackboard messages per-run.
- [ ] F11 completion report.
- [ ] New-run notification without stealing selection.
- [ ] Preserve selection across refresh.
- [ ] Clear selection when run disappears.

#### P2 ? Polish

- [ ] Keyboard navigation on run bar.
- [ ] Compact/mobile run selector.
- [ ] Deep links to Plan/Chat/Report.
- [ ] ?Copy run link?.
- [ ] Run comparison view (optional).

### Acceptance criteria

- [ ] M? app m?c ??nh kh?ng ch?n run.
- [ ] Overview ch? hi?n empty state + run selector.
- [ ] Ch?n run A ? Plan/Chat/Overview/War Room/Report ??u l? run A.
- [ ] Ch?n run B ? to?n b? ??i sang run B.
- [ ] Run A goal `1+1=2`, run B goal `5+7=?` ? Plan/Chat hi?n th? ??ng goal t??ng ?ng.
- [ ] Refresh trang v?n gi? selected run.
- [ ] 2?3 run ch?y song song kh?ng l?m selection nh?y.
- [ ] War Room E4 hi?n th? debate; E1?E3 empty state.
- [ ] v1 J1 ch? hi?n khi user ch?n legacy.
- [ ] Kh?ng c? event/plan/chat c?a run kh?c b? tr?n.
- [ ] Tests: UI selector, Plan, Chat, War Room, v1 fallback.

### Non-goals

- Kh?ng ??i engine/worktree/merge gate.
- Kh?ng x?a J1 ho?c ledger v1.
- Kh?ng auto-merge.
- Kh?ng ??i t?n War Room n?u kh?ng c?n; ch? ??i n?i dung.

---

# 10 Core Features + F11 Reporting

| ID | Chức năng | Ưu tiên | Giá trị | Effort | Target |
|---|---|---|---|---|---|
| F01 | Task model + DAG + parallel worktrees | **P0** | Rất cao | L | v3.1.0 |
| F02 | Worker lifecycle + attempt history | **P1** | Cao | M | v3.1.x |
| F03 | Decision Inbox + ask/reply gates | **P1** | Cao | M | v3.2.0 |
| F04 | Agent-agnostic adapter protocol | **P1** | Rất cao | L | v3.2.0 |
| F05 | Quota-aware scheduling | **P1** | Cao | M | v3.2.x |
| F11 | Unified run completion report | **P1** | R?t cao | M | v3.1.x |
| F06 | Browser/visual verification | **P2** | Rất cao cho web | M/L | v3.3.0 |
| F07 | Diff annotations + feedback loop | **P2** | Cao | M | v3.3.x |
| F08 | GitHub/Linear/PR/CI integration | **P2** | Cao | L | v3.4.0 |
| F09 | Remote execution backend | **P3** | Cao | XL | v4.0 |
| F10 | Scheduled automations | **P3** | Trung bình | M | v4.0+ |

---

# P0 — Nền tảng orchestration

## F01 — Task model + DAG + parallel worktrees

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

---

# P1 — Orchestration cốt lõi

## F02 — Worker lifecycle + attempt history

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
**Phụ thuộc:** F01

---

## F03 — Decision Inbox + ask/reply gates

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
**Phụ thuộc:** F01, F02

---

## F04 — Agent-agnostic adapter protocol

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
**Phụ thuộc:** F01

---

## F05 — Quota-aware scheduling

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
**Phụ thuộc:** F02, F04

---

## F11 ? Unified Run Completion Report

### V?n ??

War Room hi?n c? timeline, spend, Usage, handoff diff v? l?ch s? event, nh?ng
sau khi run k?t th?c ng??i d?ng v?n kh?ng c? m?t b?o c?o ho?n ch?nh tr? l?i:

- ?? l?m xong vi?c g??
- File n?o ?? thay ??i?
- Verify ?? pass ch?a?
- C? review/veto n?o kh?ng?
- ?? merge hay rollback ch?a?
- T?i c?n ki?m tra g? ti?p theo?

Trang **Report** hi?n t?i ch? y?u ph?c v? job/plan v1; run v2 `HANDOFF_READY`
kh?ng c? m?t completion report t??ng ?ng trong War Room.

### M?c ti?u

Sau m?i run ? ??c bi?t l? run ?? `HANDOFF_READY`, `MERGED`, `ROLLED_BACK` ho?c
`FAILED` ? ng??i d?ng m? War Room v? th?y ngay m?t b?o c?o ho?n ch?nh, kh?ng
ph?i ??c chat log ho?c t? gh?p timeline.

### Ph?m vi

- Th?m **Run Report** v?o War Room cho v2:
  - tr?ng th?i cu?i;
  - goal;
  - tier/planner;
  - th?i gian b?t ??u/k?t th?c/duration;
  - run ID;
  - worktree;
  - s? request/token/cost;
  - file thay ??i v? diff stat;
  - verify commands + exit code + output tail;
  - review roles/votes;
  - E4 Blackboard n?u c?;
  - merge/rollback SHA;
  - warnings v? next action.
- Th?m API:
  ```text
  GET /api/unified/runs/<id>/report
  ```
- B? sung `report` v?o `run_detail` ?? UI kh?ng ph?i gh?p nhi?u request.
- N?t h?nh ??ng:
  - `View diff`
  - `Merge`
  - `Rollback`
  - `Copy run ID`
  - `Download Markdown`
  - `Download JSON`
- Empty state r? r?ng:
  - ch?a c? report;
  - run ?ang ch?y;
  - run fail kh?ng c? handoff.
- Gi? nguy?n explicit merge gate; report kh?ng t? merge.

### Acceptance

- [ ] Sau `HANDOFF_READY`, War Room c? b?o c?o ?what changed / what passed?.
- [ ] B?o c?o hi?n th? verify exit code th?t.
- [ ] B?o c?o hi?n th? merge/rollback status v? SHA.
- [ ] C? th? t?i Markdown/JSON.
- [ ] Kh?ng l? secret/token.
- [ ] B?o c?o E4 hi?n th? vote; E1?E3 kh?ng c? panel meeting gi?.
- [ ] Report kh?ng thay ??i main.
- [ ] C? test API + UI.

**Effort:** M
**Ph? thu?c:** state projection hi?n c?; kh?ng c?n ch? F01
**Target:** v3.1.x

---

# P2 — Mở rộng sản phẩm

## F06 — Browser/visual verification

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
**Phụ thuộc:** F01

---

## F07 — Diff annotations + feedback loop

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
**Phụ thuộc:** F03

---

## F08 — GitHub/Linear/PR/CI integration

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
**Phụ thuộc:** F01, F04

---

# P3 — Platform

## F09 — Remote execution backend

### Mục tiêu

Mở rộng khỏi máy local: chạy worktree trên SSH host, container hoặc cloud VM.

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
  - explicit merge only;
  - budget cap.

### Acceptance

- [ ] Cùng một task chạy được local và remote.
- [ ] Worktree remote được cleanup.
- [ ] Evidence đồng bộ về local.
- [ ] Không auto-merge.
- [ ] Không lộ credential.

**Effort:** XL
**Phụ thuộc:** F01, F04

---

## F10 — Scheduled automations

### Mục tiêu

Chạy các tác vụ định kỳ có kiểm soát.

### Phạm vi

- nightly test sweep;
- dependency audit;
- flaky test triage;
- weekly issue triage.
- Cấu hình:
  - schedule;
  - budget cap;
  - free-only;
  - target branch;
  - explicit merge gate.

### Acceptance

- [ ] Scheduled run có budget/time cap.
- [ ] Không auto-merge.
- [ ] Có notification khi cần người.
- [ ] Evidence được lưu.
- [ ] Có thể disable bằng một lệnh.

**Effort:** M
**Phụ thuộc:** F09

---

# Thứ tự thực hiện

## Debug (riêng)

```text
D01 token redaction
D02 verify summary
D03 v1/v2 plan split-brain
→ v3.0.1
```

## Feature roadmap

```text
P0: F01 Task DAG
P1: F02 Worker lifecycle
P1: F03 Decision Inbox
P1: F04 Agent adapter
P1: F05 Quota routing
P1: F11 Unified run report
P2: F06 Browser verification
P2: F07 Diff annotations
P2: F08 GitHub/Linear
P3: F09 Remote backend
P3: F10 Automations
```

> Debug không nằm trong P0. Khi yêu cầu “làm P0”, chỉ thực hiện F01.

---

# Những gì không copy từ Orca

- ❌ Desktop IDE đầy đủ — War Room chỉ nên là control plane.
- ❌ Mobile app ngay — chờ remote runner và notification.
- ❌ Plugin/theme marketplace — không phải core.
- ❌ “Any agent” không có adapter protocol — sẽ thành if/else.
- ❌ Auto-merge/PR bot — giữ explicit merge gate.

---

# Tiêu chí thành công

## Kỹ thuật

- [ ] Task DAG chạy song song thật.
- [ ] Mỗi task có worktree/verify/evidence riêng.
- [ ] Retry/reassign không mất task.
- [ ] Decision Inbox hoạt động.
- [ ] Agent adapter không phụ thuộc OpenCode.
- [ ] Browser verify là một phần của merge gate.
- [ ] GitHub/Linear không tạo auto-merge.

## Sản phẩm

- [ ] Giảm thời gian hoàn thành task lớn.
- [ ] Tăng tỷ lệ handoff pass verify.
- [ ] Giảm số lần người phải đọc chat log.
- [ ] Tăng số task chạy song song an toàn.
- [ ] Giữ nguyên tỷ lệ 0 lần main bị sửa trước merge.

## Release gate

Mỗi release phải có:

- [ ] 56+ test files pass.
- [ ] Parity/regression pass.
- [ ] E2E trên repo tạm.
- [ ] UI/API smoke.
- [ ] Không auto-merge.
- [ ] Evidence được ghi vào `docs/QA_CHECKLIST.md`.
