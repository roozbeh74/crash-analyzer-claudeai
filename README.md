# crash-analyzer-claudeai

24/7 collector for FaucetPay crash rounds.

- `collector.py` — connects to the crash websocket (`crash_update` protocol), records every finished round.
- `.github/workflows/collect.yml` — runs the collector every 5 minutes via GitHub Actions and commits the data.
- `rounds-YYYY-MM.jsonl` — one JSON per line: `{seq, id, crash, t, dur, players, cashouts, cashAt, seed, est}`.

The live dashboard (Hugging Face Space `crash-live`) merges this data and runs the statistical fairness audit.

**Honest note:** the game is provably fair; round outcomes are independent. This data is for verifying that
(fairness audit), not for predicting future multipliers. No dataset gives a real-money edge. 18+
