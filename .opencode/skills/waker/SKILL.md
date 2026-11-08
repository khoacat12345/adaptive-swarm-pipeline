---
name: waker
description: Supervise a long coding task until every checklist item has on-disk evidence and tests pass. Use when the user says "dùng waker", "canh việc này", "làm tới cùng", or asks whether the agent is still working.
---

# waker — supervisor

Bạn **không được** tự tuyên bố task xong. Chỉ daemon quyết định, dựa trên
`git diff ∩ scope` và `verify` exit code. Lời của bạn không phải bằng chứng.

## Nếu đã có run đang chạy

1. Chạy `waker status --format md --repo .`
2. Chèn **nguyên bảng** vào đầu câu trả lời.
3. Nếu status là `running`: không suy đoán, không hứa hẹn thay daemon.
4. Nếu status khác `running`: đọc `waker report --repo .` và tóm tắt đúng số liệu
   (task done/total, test, số kick, cost).

## Bắt đầu run mới

1. **Xác định repo đích.** Nếu prompt nhắc path không có trong repo hiện tại
   (ví dụ `.agents/v2/...` khi đang ở `waker-ai`), hỏi user repo đích trước khi
   plan. Không đoán.
2. Lấy **nguyên văn** prompt của user (bỏ tiền tố `@waker` nếu có). Không tóm
   tắt, không diễn giải.
3. `waker plan --prompt "<nguyên văn>" --out .waker/tasks.yaml --repo <repo đích>`
4. Đọc kết quả lint:
   - **Nếu có ERROR** (thiếu scope/verify, trùng scope, verify rỗng):
     **KHÔNG hỏi "OK để chạy".** Trình bày lỗi và đề xuất đúng 2 lựa chọn:
     a) `waker plan --via opencode --model <model> --prompt "<nguyên văn>" --repo <repo>`
        để một turn model điền scope/verify, rồi lint lại;
     b) user gộp/sửa task thủ công.
   - Chỉ đi tiếp khi lint **0 error**.
5. Khi lint sạch: in `.waker/tasks.yaml` + cảnh báo (nếu có), rồi hỏi đúng một
   câu: `OK để chạy? (ok / sửa)`.
6. Khi user đồng ý, chạy:

```bash
waker start --detach --repo . --tasks .waker/tasks.yaml \
  --test "<lệnh test của repo>" \
  --adapter opencode --model <provider/model> [--max-kicks 5] [--max-cost 1.0]
```

Nếu không phải OpenCode, dùng adapter generic:

```bash
waker start --detach --repo . --tasks .waker/tasks.yaml \
  --test "<lệnh test>" --adapter generic \
  --agent "claude -p --resume {session} {prompt}"
```

7. Trả lời: "Đang chạy ngầm. Gõ 'tiến độ' để xem."

> Prompt kiểu "lệnh thực thi" (phê duyệt, quyết định, đánh số từng dòng) thường
> không phải task. Dùng `--via opencode` để gộp thành 3–6 task có scope/verify,
> thay vì biến mỗi dòng thành một task rỗng.

## Dừng

`waker stop --repo .`

## Quy tắc cứng

- Lint còn ERROR thì **không được start** và **không được hỏi "OK để chạy"**.
- Không sửa `tasks.yaml` sau khi đã start (hash sẽ báo TAMPER).
- Không sửa file test. Muốn đổi test thì dừng run, sửa, chạy lại.
- Không nói "xong" — chỉ đọc `status`/`report`.
- Nếu người dùng hỏi "còn thiếu gì", trả lời bằng danh sách task `status != done`
  trong JSON của `waker status --format json`, không tự đoán.
