
## Soak started 2026-11-07T00:18:07
- mode: `fake`  interval: 1.0s  hours: 0.002
- 2026-11-07T00:18:20 cycle=1 mode=fake ok=False status=ExecutionError: phase P1 failed at S1 elapsed=13.2s
- Soak stopped 2026-11-07T00:18:21 after 1 cycle(s)

## Soak started 2026-11-07T00:18:43
- mode: `fake`  interval: 1.0s  hours: 0.002
- 2026-11-07T00:19:09 cycle=1 mode=fake ok=True status=HANDOFF_READY elapsed=26.2s
- Soak stopped 2026-11-07T00:19:10 after 1 cycle(s)

## Soak started 2026-11-07T00:19:23
- mode: `fake`  interval: 60.0s  hours: 8.0
- 2026-11-07T00:19:50 cycle=1 mode=fake ok=False status=BudgetOverrun: provider usage exceeded reservation for res_b6588bb8428f elapsed=27.6s
- 2026-11-07T00:21:21 cycle=2 mode=fake ok=True status=HANDOFF_READY elapsed=30.5s
- 2026-11-07T00:22:50 cycle=3 mode=fake ok=True status=HANDOFF_READY elapsed=29.4s

## Soak started 2026-11-07T00:23:34
- mode: `fake`  interval: 1.0s  hours: 0.002
- 2026-11-07T00:23:38 cycle=1 mode=fake ok=True status=HANDOFF_READY elapsed=3.5s
- Soak stopped 2026-11-07T00:23:39 after 1 cycle(s)

## Soak started 2026-11-07T00:23:50
- mode: `fake`  interval: 60.0s  hours: 8.0
- 2026-11-07T00:23:53 cycle=1 mode=fake ok=True status=HANDOFF_READY elapsed=3.2s
- 2026-11-07T00:24:57 cycle=2 mode=fake ok=True status=HANDOFF_READY elapsed=3.2s
