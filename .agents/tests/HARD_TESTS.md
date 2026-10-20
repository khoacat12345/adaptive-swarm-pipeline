# Hard tests - bo de do model (Phase 5B)

Muc dich: phan biet model that su tren 7 domain thay vi task de (typo/bug nho).
Moi bai do bang MAY (verify exit 0), khong dung pytest/third-party.

## 7 bai

| ID | Domain | Bai | Cai kho |
|---|---|---|---|
| B1 | backend | LRU cache O(1) | eviction dung thu tu, get cap nhat recency |
| B2 | frontend | JS classNames (node) | object, nested array, falsy, so, dedupe |
| B3 | db | sqlite migration | them cot NOT NULL + backfill + idempotent |
| B4 | security | path traversal + cmd injection | ../, absolute path, shell metachar |
| B5 | tests | viet test bat bug | phai FAIL tren ban buggy VA PASS tren ban dung |
| B6 | architect | plan.json | dung thu tu, acyclic, du auth + rate-limit |
| B7 | docs | API.md | AST doi chieu ten ham + du tham so, khong bia |

Da kiem chung: ca 7 bai deu FAIL tren fixture buggy (test that su bat duoc loi),
va PASS khi sua dung.

## Chay lai

```bash
python .agents/benchmark.py --models "opencode-go/kimi-k3,opencode-go/glm-5.3" \
    --domains backend,security --budget 1800
```

Ket qua: `eval/bench/<timestamp>/results.jsonl` + `report.json`
(mac dinh `eval/bench/` bi gitignore; copy ra `eval/<ten>.jsonl` neu muon luu).

## Chi phi that (QUAN TRONG)

`opencode run` voi agent `build` tao NHIEU request noi bo moi task (tool loop:
doc file, sua, chay test...), nen 1 task = ~8-12 request. Voi model dat, moi
request ~$0.01-0.015.

Vi du that (OpenCode Go console): **Kimi K3 6 task = 61 request = $0.87**.
Vi vay KHONG nen chay 10 model x 7 task neu chi can chon model. Uu tien:
research cong khai + 1 smoke nho (2-3 task) cho model moi.

## Ket qua da luu

- `eval/hard_smoke_2026-10-20.jsonl`: 6 task Kimi K3 (B1-B6), tat ca PASS,
  1-2 vong, 26-87s, tong ~$0.87 tren console.
