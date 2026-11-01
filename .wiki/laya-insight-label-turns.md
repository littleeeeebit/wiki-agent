---
scope: project
severity: landmine
triggers: ["(?i)label\\.py", "(?i)max.?turns", "(?i)laya.*(label|라벨).*(budget|상한|cap)"]
reads: [laya/label.py]
slots: []
sources: []
links: [laya, laya-exp-useful-data]
---

# `laya/label.py --max-turns` counts labeller calls, not states

Part of [laya](laya.md).

Rule. Set `--max-turns` to twice the states you mean to judge (two
labellers each), and size the budget from states judged, not from turns.

Why. On 2026-10-14 the validation top-up was bounded with `--max-turns 400`
to judge about 400 states; it stopped at 200 states and 249 of 300 labels
(`"stopped": "turn_limit"`), and a second run was needed to finish.

How to apply. A state costs about $0.0075 (Sonnet; Codex has no per-call
cost) and about 0.75 of judged states become labels
([laya-exp-useful-data](laya-exp-useful-data.md)), so N labels need about N / 0.75 states and
2N / 0.75 turns.
